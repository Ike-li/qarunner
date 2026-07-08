"""Dependency container for DI ports and authentication dependencies."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from qarunner.adapters.allure_cli_reporter import AllureCliReporter
from qarunner.adapters.anthropic_analyzer import AnthropicFailureAnalyzer
from qarunner.adapters.apscheduler_schedule import ApschedulerSchedulePort
from qarunner.adapters.asyncio_scheduler import AsyncioScheduler
from qarunner.adapters.docker_runner import DockerRunner
from qarunner.adapters.junit_collector import JunitCollector
from qarunner.adapters.openai_analyzer import OpenAiFailureAnalyzer
from qarunner.adapters.sqlite_store import SqliteStore
from qarunner.adapters.subprocess_runner import SubprocessRunner
from qarunner.adapters.system_clock import SystemClock
from qarunner.adapters.uuid_ids import UuidIds
from qarunner.config import Settings
from qarunner.core.auth import decode_access_token
from qarunner.core.login_throttle import LoginThrottle
from qarunner.core.orchestrator import RunOrchestrator
from qarunner.core.profile_service import ProfileService
from qarunner.core.runners.playwright_runner import PlaywrightRunner
from qarunner.core.runners.pytest_runner import PytestRunner
from qarunner.core.runners.registry import RunnerRegistry
from qarunner.core.schedule_service import ScheduleService
from qarunner.models import User, UserRole
from qarunner.ports.ai import FailureAnalyzer
from qarunner.ports.schedule import SchedulePort
from qarunner.ports.store import Store


@dataclass
class Container:
    """Holds all port implementations.  Tests inject fakes.

    ``task_scheduler`` controls run-execution concurrency (the poller);
    ``scheduler`` is the cron scheduler (APScheduler).
    """

    orchestrator: RunOrchestrator
    store: Store
    task_scheduler: object  # TaskScheduler (not typed to avoid churn during rewrite)
    scheduler: SchedulePort  # cron scheduler
    schedule_service: ScheduleService
    profile_service: ProfileService
    login_throttle: LoginThrottle
    settings: Settings
    ai_analyzer: FailureAnalyzer | None = None


def _build_ai_analyzer(cfg: Settings) -> FailureAnalyzer | None:
    """Construct the configured provider adapter, or None when AI is disabled.

    An empty api_key or an unknown provider yields None — the endpoints then
    report the feature as disabled rather than failing.
    """
    if not cfg.ai_api_key:
        return None
    kwargs = {
        "api_key": cfg.ai_api_key,
        "model": cfg.ai_model,
        "base_url": cfg.ai_base_url,
        "timeout": cfg.ai_request_timeout_seconds,
    }
    if cfg.ai_provider == "anthropic":
        return AnthropicFailureAnalyzer(**kwargs)
    if cfg.ai_provider == "openai":
        return OpenAiFailureAnalyzer(**kwargs)
    return None


def create_container(settings: Settings | None = None) -> Container:
    """Wire up real adapters from settings."""
    cfg = settings or Settings()
    executable = cfg.executable or sys.executable

    store = SqliteStore(cfg.db_path)
    clock = SystemClock()
    ids = UuidIds()
    process = SubprocessRunner()
    docker_process = DockerRunner(
        allow_runtime_build=cfg.executor_autobuild,
        playwright_executor_image=cfg.playwright_executor_image,
        extra_readonly_roots=[
            root for root in cfg.executor_extra_readonly_roots.split(os.pathsep) if root
        ],
    )
    collector = JunitCollector()
    reporter = AllureCliReporter(process=process, allure_bin=cfg.allure_bin)
    task_scheduler = AsyncioScheduler(store=store, max_concurrency=cfg.max_concurrency)

    registry = RunnerRegistry()
    registry.register(PytestRunner())
    registry.register(PlaywrightRunner())

    orchestrator = RunOrchestrator(
        registry=registry,
        store=store,
        scheduler=task_scheduler,
        process=docker_process,
        collector=collector,
        reporter=reporter,
        clock=clock,
        ids=ids,
        tests_root=cfg.tests_root,
        artifacts_root=cfg.artifacts_root,
        executable=executable,
        default_timeout=cfg.default_timeout_seconds,
        worker_node_id=cfg.worker_node_id,
        public_url=cfg.public_url,
    )

    # Wire the execution callback after orchestrator is constructed (circ ref).
    task_scheduler.set_run_fn(orchestrator.execute)

    schedule_port = ApschedulerSchedulePort(store=store, orchestrator=orchestrator)
    schedule_service = ScheduleService(store=store, scheduler=schedule_port)
    profile_service = ProfileService(store=store)
    login_throttle = LoginThrottle(
        clock=clock,
        threshold=cfg.login_throttle_threshold,
        base_seconds=cfg.login_throttle_base_seconds,
        max_seconds=cfg.login_throttle_max_seconds,
    )

    return Container(
        orchestrator=orchestrator,
        store=store,
        task_scheduler=task_scheduler,
        scheduler=schedule_port,
        schedule_service=schedule_service,
        profile_service=profile_service,
        login_throttle=login_throttle,
        settings=cfg,
        ai_analyzer=_build_ai_analyzer(cfg),
    )


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_current_user(request: Request, token: str | None = Depends(oauth2_scheme)) -> User:
    """FastAPI dependency to retrieve the current authenticated user via JWT.

    Credentials are accepted from the ``Authorization: Bearer`` header (API
    clients) or the ``token`` cookie (browser; planted HttpOnly at login). The
    ``?token=`` query-parameter fallback was removed (SEC-6): long-lived JWTs in
    URLs leak into access logs, browser history and Referer headers. Same-origin
    report/stream/download requests carry the cookie automatically, so no token
    ever needs to appear in a URL.
    """
    if not token:
        token = request.cookies.get("token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    container = request.app.state.container
    payload = decode_access_token(token, container.settings)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload["sub"]

    user_record = await container.store.get_user(username)
    if not user_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from datetime import datetime

    created_at_dt = (
        datetime.fromisoformat(user_record["created_at"])
        if user_record.get("created_at")
        else datetime.now(UTC)
    )
    return User(
        username=user_record["username"],
        role=UserRole(user_record["role"]),
        created_at=created_at_dt,
    )


async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency to enforce that the user is an Administrator."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires Admin role",
        )
    return current_user
