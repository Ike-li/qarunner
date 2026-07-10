"""Tests for api.routes — HTTP endpoints via TestClient."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from qarunner.api.app import create_app as _real_create_app
from qarunner.api.deps import Container, get_current_admin, get_current_user
from qarunner.config import Settings
from qarunner.core.login_throttle import LoginThrottle
from qarunner.core.profile_service import ProfileService
from qarunner.core.schedule_service import ScheduleService
from qarunner.errors import RunNotFound, UnknownRunner, UnsafePath
from qarunner.models import (
    CaseHistoryPoint,
    Credential,
    ReportRef,
    Run,
    RunRequest,
    RunStatus,
    TestCaseResult,
    TestProfile,
    TestSchedule,
    TestSuite,
    TestSummary,
    User,
    UserRole,
)
from qarunner.ports.store import Store
from tests.fakes.fake_clock import FakeClock
from tests.fakes.fake_schedule_port import FakeSchedulePort
from tests.fakes.fake_scheduler import FakeScheduler

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def create_app(container: Container | None = None) -> FastAPI:
    app = _real_create_app(container)

    async def mock_get_current_user() -> User:
        return User(username="test_user", role=UserRole.ADMIN, created_at=NOW)

    async def mock_get_current_admin() -> User:
        return User(username="test_user", role=UserRole.ADMIN, created_at=NOW)

    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[get_current_admin] = mock_get_current_admin
    return app


# ── fakes local to this test module ────────────────────────────────────


@dataclass
class FakeStore:
    """In-memory store for route tests."""

    _runs: dict[str, Run] = field(default_factory=dict)
    _profiles: dict[str, TestProfile] = field(default_factory=dict)
    _suites: dict[str, TestSuite] = field(default_factory=dict)
    _schedules: dict[str, TestSchedule] = field(default_factory=dict)
    _users: dict[str, dict] = field(default_factory=dict)
    _credentials: dict[str, tuple] = field(default_factory=dict)
    _cases: dict[str, list] = field(default_factory=dict)
    _ai_diagnoses: dict[str, object] = field(default_factory=dict)
    count_flaky_calls: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._profiles = {}
        self._suites = {}
        self._schedules = {}
        self._users = {}
        self._credentials = {}
        from qarunner.core.auth import hash_password

        self._users["test_user"] = {
            "username": "test_user",
            "password_hash": hash_password("test_pass"),
            "role": "admin",
            "created_at": "2026-06-20T16:00:00Z",
            "token_version": 0,
        }

    async def get_user(self, username: str) -> dict | None:
        # Snapshot, not a live reference — mirrors SqliteStore.get_user(), which
        # rebuilds a fresh dict from a DB row on every call. Callers that read
        # `existing` before a later mutation (e.g. update_user's demote-then-
        # skip-redundant-update path) must not see in-place changes here.
        record = self._users.get(username)
        return dict(record) if record is not None else None

    async def create_user(self, username: str, password_hash: str, role: str) -> None:
        from datetime import UTC, datetime

        self._users[username] = {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "created_at": datetime.now(UTC).isoformat(),
            "token_version": 0,
        }

    async def list_users(self) -> list[dict]:
        return [
            {"username": u["username"], "role": u["role"], "created_at": u["created_at"]}
            for u in sorted(self._users.values(), key=lambda x: x["username"])
        ]

    async def delete_user(self, username: str) -> bool:
        return self._users.pop(username, None) is not None

    async def update_password(self, username: str, password_hash: str) -> bool:
        if username not in self._users:
            return False
        self._users[username]["password_hash"] = password_hash
        return True

    async def update_role(self, username: str, role: str) -> bool:
        if username not in self._users:
            return False
        self._users[username]["role"] = role
        return True

    async def demote_if_not_last_admin(self, username: str, new_role: str) -> bool:
        """BUG-6: atomic demote — only if more than one admin remains."""
        if username not in self._users:
            return False
        admin_count = sum(1 for u in self._users.values() if u["role"] == "admin")
        if admin_count <= 1:
            return False
        self._users[username]["role"] = new_role
        return True

    async def increment_token_version(self, username: str) -> bool:
        """BUG-5+13: bump token_version to invalidate all existing JWTs."""
        if username not in self._users:
            return False
        self._users[username]["token_version"] = self._users[username].get("token_version", 0) + 1
        return True

    async def save_credential(self, credential: object, encrypted_secret: str) -> None:
        self._credentials[credential.id] = (credential, encrypted_secret)  # type: ignore[attr-defined]

    async def get_credential(self, credential_id: str) -> object | None:
        entry = self._credentials.get(credential_id)
        return entry[0] if entry else None

    async def get_credential_secret(self, credential_id: str) -> str | None:
        entry = self._credentials.get(credential_id)
        return entry[1] if entry else None

    async def list_credentials(self) -> list:
        return [c for c, _ in self._credentials.values()]

    async def delete_credential(self, credential_id: str) -> bool:
        return self._credentials.pop(credential_id, None) is not None

    async def save(self, run: Run) -> None:
        self._runs[run.id] = run

    async def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFound(run_id) from None

    async def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    async def save_cases(self, run_id, tests_path, created_at, cases) -> None:
        self._cases[run_id] = list(cases)

    async def get_cases_for_run(self, run_id: str) -> list:
        return list(self._cases.get(run_id, []))

    async def save_ai_diagnosis(self, run_id, diagnosis, provider, model, created_at) -> None:
        self._ai_diagnoses[run_id] = diagnosis

    async def get_ai_diagnosis(self, run_id):
        return self._ai_diagnoses.get(run_id)

    async def get_case_history(
        self,
        tests_path,
        suite,
        name,
        limit=20,
        created_by=None,
        profile_id=None,
    ) -> list:
        rows = []
        for run_id, cases in self._cases.items():
            run = self._runs.get(run_id)
            if run is None or run.tests_path != tests_path:
                continue
            if created_by is not None and run.created_by != created_by:
                continue
            if profile_id is not None and run.profile_id != profile_id:
                continue
            for c in cases:
                if c.suite == suite and c.name == name:
                    rows.append((run.created_at, c.status))
        rows.sort(key=lambda x: x[0], reverse=True)
        return [CaseHistoryPoint(created_at=ca, status=st) for ca, st in reversed(rows[:limit])]

    async def count_flaky_tests(
        self,
        days: int = 30,
        created_by: str | None = None,
        *,
        min_observations: int = 4,
        flip_threshold: int = 3,
    ) -> int:
        from datetime import timedelta

        self.count_flaky_calls.append(
            {
                "days": days,
                "created_by": created_by,
                "min_observations": min_observations,
                "flip_threshold": flip_threshold,
            }
        )
        since = datetime.now(UTC) - timedelta(days=days)
        grouped: dict[tuple[str, str, str], list[str]] = {}
        for run_id, cases in self._cases.items():
            run = self._runs.get(run_id)
            if run is None or run.status != RunStatus.COMPLETED:
                continue
            if run.created_at < since:
                continue
            if created_by is not None and run.created_by != created_by:
                continue
            for c in cases:
                key = (run.tests_path, c.suite, c.name)
                grouped.setdefault(key, []).append(c.status)
        count = 0
        for statuses in grouped.values():
            seq = "".join("F" if s in ("failed", "error") else "P" for s in statuses)
            flips = sum(1 for i in range(len(seq) - 1) if seq[i] != seq[i + 1])
            if len(statuses) >= min_observations and flips >= flip_threshold:
                count += 1
        return count

    async def lock_run(self, run_id: str, locked: bool) -> None:
        if run_id in self._runs:
            run = self._runs[run_id]
            self._runs[run_id] = run.model_copy(update={"locked": locked})

    async def delete_run(self, run_id: str) -> bool:
        return self._runs.pop(run_id, None) is not None

    async def count_inflight_runs(self, created_by: str) -> int:
        return sum(
            1
            for r in self._runs.values()
            if r.created_by == created_by and r.status in (RunStatus.QUEUED, RunStatus.RUNNING)
        )

    async def get_old_unlocked_runs(self, retention_days: int) -> list[Run]:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        res = []
        for r in self._runs.values():
            if (
                r.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.TIMEOUT)
                and not r.locked
                and r.finished_at
                and r.finished_at <= cutoff
            ):
                res.append(r)
        return res

    async def save_suite(self, suite: TestSuite) -> None:
        self._suites[suite.name] = suite

    async def get_suite(self, name: str) -> TestSuite | None:
        return self._suites.get(name)

    async def list_suites(self) -> list[TestSuite]:
        return sorted(self._suites.values(), key=lambda s: s.name)

    async def delete_suite(self, name: str) -> bool:
        if name in self._suites:
            del self._suites[name]
            return True
        return False

    async def save_profile(self, profile: TestProfile) -> None:
        self._profiles[profile.id] = profile

    async def get_profile(self, profile_id: str) -> TestProfile | None:
        return self._profiles.get(profile_id)

    async def list_profiles(self, tests_path: str | None = None) -> list[TestProfile]:
        res = list(self._profiles.values())
        if tests_path:
            res = [p for p in res if p.tests_path == tests_path]
        return sorted(res, key=lambda p: p.created_at, reverse=True)

    async def delete_profile(self, profile_id: str) -> bool:
        if profile_id in self._profiles:
            del self._profiles[profile_id]
            # Cascade delete schedules bound to this profile
            to_delete = [sid for sid, s in self._schedules.items() if s.profile_id == profile_id]
            for sid in to_delete:
                del self._schedules[sid]
            return True
        return False

    async def save_schedule(self, schedule: TestSchedule) -> None:
        self._schedules[schedule.id] = schedule

    async def get_schedule(self, schedule_id: str) -> TestSchedule | None:
        return self._schedules.get(schedule_id)

    async def list_schedules(self, profile_id: str | None = None) -> list[TestSchedule]:
        res = list(self._schedules.values())
        if profile_id:
            res = [s for s in res if s.profile_id == profile_id]
        return sorted(res, key=lambda s: s.created_at, reverse=True)

    async def delete_schedule(self, schedule_id: str) -> bool:
        if schedule_id in self._schedules:
            del self._schedules[schedule_id]
            return True
        return False

    async def claim_schedule_run(self, schedule_id: str, fire_time: datetime) -> bool:
        return True

    async def update_schedule_next_run(
        self, schedule_id: str, next_run_at: datetime | None
    ) -> None:
        existing = self._schedules.get(schedule_id)
        if existing is not None:
            self._schedules[schedule_id] = existing.model_copy(update={"next_run_at": next_run_at})

    async def mark_interrupted_runs(self, worker_node_id: str | None = None) -> int:
        return 0

    async def dequeue_next_queued(self) -> str | None:
        return None  # no queued runs in route-test fake

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass


@dataclass
class FakeOrchestrator:
    """Minimal orchestrator fake for route tests."""

    store: FakeStore
    _counter: int = 0
    raise_unknown_runner: bool = False
    raise_unsafe_path: bool = False
    last_req: RunRequest | None = None
    last_profile_id: str | None = None

    async def create(self, req: RunRequest, created_by: str, profile_id: str | None = None) -> Run:
        if self.raise_unknown_runner:
            raise UnknownRunner(req.runner)
        if self.raise_unsafe_path:
            raise UnsafePath(f"{req.tests_path!r} resolves outside root")
        self.last_req = req
        self.last_profile_id = profile_id
        self._counter += 1
        run = Run(
            id=f"id-{self._counter:03d}",
            status=RunStatus.QUEUED,
            runner=req.runner,
            created_by=created_by,
            tests_path=req.tests_path,
            args=req.args,
            allure_enabled=req.allure,
            timeout=req.timeout,
            executor_mode=req.executor_mode,
            created_at=NOW,
            env=req.env,
            profile_id=profile_id,
        )
        await self.store.save(run)
        return run

    async def drain(self, timeout: float | None = None) -> None:
        return None

    async def cancel(self, run_id: str) -> Run:
        run = await self.store.get(run_id)
        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            run = run.model_copy(update={"status": RunStatus.CANCELLED, "finished_at": NOW})
            await self.store.save(run)
        return run


def _make_container(
    *,
    login_throttle: object = None,
    settings: Settings | None = None,
    **orch_kwargs: object,
) -> Container:
    store = FakeStore()
    orchestrator = FakeOrchestrator(store=store, **orch_kwargs)  # type: ignore[arg-type]
    scheduler = FakeSchedulePort()
    return Container(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        task_scheduler=FakeScheduler(),
        scheduler=scheduler,
        schedule_service=ScheduleService(store=store, scheduler=scheduler),  # type: ignore[arg-type]
        profile_service=ProfileService(store=store),  # type: ignore[arg-type]
        login_throttle=login_throttle or LoginThrottle(clock=FakeClock()),  # type: ignore[arg-type]
        settings=settings or Settings(),
    )


def test_fake_store_implements_full_store_port() -> None:
    # The route fake stands in for the real adapter, so it must satisfy the
    # whole Store port (ARCH-5), not just the run subset.
    assert isinstance(FakeStore(), Store)


def _make_run_in_store(store: FakeStore, **overrides: object) -> Run:
    base: dict[str, object] = dict(
        id="run-001",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=NOW,
    )
    base.update(overrides)
    run = Run(**base)  # type: ignore[arg-type]

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(store.save(run))
    loop.close()
    return run


# ── GET /health (DEP-4) ──────────────────────────────────────────────────


def test_health_ok() -> None:
    app = create_app(_make_container())
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_db_unavailable_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _make_container()

    async def _boom(_username: str) -> None:
        raise OSError("db down")

    monkeypatch.setattr(container.store, "get_user", _boom)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 503


# ── POST /runs ─────────────────────────────────────────────────────────


def test_create_run_returns_202() -> None:
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "pytest"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] == "id-001"
    assert body["status"] == "queued"
    assert body["runner"] == "pytest"
    assert body["tests_path"] == "tests/"
    assert body["passed"] is None


def test_create_run_unknown_runner_400() -> None:
    container = _make_container(raise_unknown_runner=True)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "nope"})
    assert resp.status_code == 400
    assert "nope" in resp.json()["detail"]


def test_create_run_unsafe_path_400() -> None:
    # Value is irrelevant here: raise_unsafe_path makes the fake orchestrator
    # unconditionally raise UnsafePath, so this exercises the route's error
    # mapping, not path validation itself — must stay a value RunRequest's
    # own tests_path validator (BUG-25) accepts, or it 422s before reaching
    # the orchestrator at all.
    container = _make_container(raise_unsafe_path=True)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "pytest"})
    assert resp.status_code == 400


def test_create_run_default_mode_docker_allows_non_admin() -> None:
    # The docker default lets a non-admin create a run without tripping the
    # subprocess restriction (no executor_mode supplied → defaults to docker).
    container = _make_container()
    app = create_app(container)
    _override_user(app, "normal_user", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "pytest"})
    assert resp.status_code == 202


def test_create_run_forbidden_for_other_users_registered_suite() -> None:
    container = _make_container()
    _save_suite_in_store(container.store, name="bob_suite", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "bob_suite/tests", "runner": "pytest"})

    assert resp.status_code == 403
    assert "bob_suite" not in resp.text
    assert container.orchestrator.last_req is None


def test_create_run_rejects_traversal_past_owned_suite() -> None:
    """BUG-25: the owner-scope check only inspects the first path segment of
    tests_path — "alice_suite/../victim_suite" would pass that check (alice
    owns alice_suite) while orchestrator.create() actually resolves and runs
    against victim_suite, a suite alice doesn't own. Must be rejected before
    it ever reaches the owner check or the orchestrator."""
    container = _make_container()
    _save_suite_in_store(container.store, name="alice_suite", created_by="alice")
    _save_suite_in_store(container.store, name="victim_suite", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"tests_path": "alice_suite/../victim_suite", "runner": "pytest"},
        )

    assert resp.status_code == 422
    assert container.orchestrator.last_req is None


def test_create_run_allows_admin_registered_shared_suite_for_non_admin() -> None:
    container = _make_container()
    _save_suite_in_store(container.store, name="shared_suite", created_by="admin")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "shared_suite", "runner": "pytest"})

    assert resp.status_code == 202
    assert resp.json()["tests_path"] == "shared_suite"


def test_create_run_playwright_docker_allowed() -> None:
    # All runs are docker now — executor_mode removed from API input.
    container = _make_container()
    app = create_app(container)
    _override_user(app, "admin_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post(
            "/runs",
            json={"tests_path": "tests/", "runner": "playwright"},
        )
    assert resp.status_code == 202
    assert resp.json()["runner"] == "playwright"
    assert resp.json()["executor_mode"] == "docker"


def test_create_run_rate_limited_at_inflight_cap() -> None:
    # P2-7: a non-admin already at the per-user in-flight cap is refused (429),
    # bounding unbounded QUEUED-run accumulation.
    container = _make_container()
    container.settings.max_inflight_runs_per_user = 2
    _make_run_in_store(
        container.store, id="if-1", status=RunStatus.QUEUED, created_by="normal_user"
    )
    _make_run_in_store(
        container.store, id="if-2", status=RunStatus.RUNNING, created_by="normal_user"
    )
    app = create_app(container)
    _override_user(app, "normal_user", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "pytest"})
    assert resp.status_code == 429


def test_create_run_under_inflight_cap_allowed() -> None:
    # P2-7: below the cap the run is accepted; finished runs don't count toward it.
    container = _make_container()
    container.settings.max_inflight_runs_per_user = 2
    _make_run_in_store(
        container.store, id="if-1", status=RunStatus.QUEUED, created_by="normal_user"
    )
    _make_run_in_store(
        container.store, id="done", status=RunStatus.COMPLETED, created_by="normal_user"
    )
    app = create_app(container)
    _override_user(app, "normal_user", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "pytest"})
    assert resp.status_code == 202


def test_create_run_admin_exempt_from_inflight_cap() -> None:
    # P2-7: admins are not subject to the per-user in-flight cap.
    container = _make_container()
    container.settings.max_inflight_runs_per_user = 1
    _make_run_in_store(
        container.store, id="if-1", status=RunStatus.RUNNING, created_by="admin_user"
    )
    app = create_app(container)
    _override_user(app, "admin_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "pytest"})
    assert resp.status_code == 202


def test_create_run_inflight_cap_disabled_when_zero() -> None:
    # P2-7: max_inflight_runs_per_user=0 disables the limit entirely.
    container = _make_container()
    container.settings.max_inflight_runs_per_user = 0
    _make_run_in_store(
        container.store, id="if-1", status=RunStatus.RUNNING, created_by="normal_user"
    )
    _make_run_in_store(
        container.store, id="if-2", status=RunStatus.RUNNING, created_by="normal_user"
    )
    app = create_app(container)
    _override_user(app, "normal_user", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "tests/", "runner": "pytest"})
    assert resp.status_code == 202


# ── GET /runs ──────────────────────────────────────────────────────────


def test_list_runs_empty() -> None:
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_list_runs_with_data() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="aaa")
    _make_run_in_store(container.store, id="bbb")
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["runs"]]
    # Both have identical created_at; sorted returns insertion order
    assert set(ids) == {"aaa", "bbb"}
    assert len(ids) == 2


# ── GET /runs/{run_id} ────────────────────────────────────────────────


def test_get_run_found() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="abc")
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/abc")
    assert resp.status_code == 200
    assert resp.json()["id"] == "abc"


def test_get_run_includes_persisted_case_results() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="with-cases")
    container.store._cases["with-cases"] = [
        TestCaseResult(
            suite="checkout",
            name="test_guest_checkout",
            status="failed",
            duration_ms=456,
            message="Expected confirmation",
        )
    ]
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/with-cases")
    assert resp.status_code == 200
    assert resp.json()["cases"] == [
        {
            "suite": "checkout",
            "name": "test_guest_checkout",
            "status": "failed",
            "duration_ms": 456,
            "message": "Expected confirmation",
        }
    ]


def test_get_run_not_found() -> None:
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/missing")
    assert resp.status_code == 404
    assert "missing" in resp.json()["detail"]


# ── GET /runs/{run_id}/report ──────────────────────────────────────────


def test_get_report_not_available() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="r1")
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/r1/report")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Report not available"


def test_get_report_not_found_run() -> None:
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/nope/report")
    assert resp.status_code == 404


def test_get_report_success(tmp_path: Path) -> None:
    html_file = tmp_path / "report.html"
    html_file.write_text("<html><body>Report</body></html>")

    container = _make_container(settings=Settings(artifacts_root=str(tmp_path)))
    report = ReportRef(
        allure_results_dir=str(tmp_path),
        allure_report_file=str(html_file),
        html_generated=True,
    )
    _make_run_in_store(container.store, id="r2", status=RunStatus.COMPLETED, report=report)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/r2/report")
    assert resp.status_code == 200
    assert "Report" in resp.text


def test_get_report_rejects_file_outside_results_dir(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    secret_file = tmp_path / "secret.html"
    secret_file.write_text("<html>TOPSECRET</html>")

    container = _make_container(settings=Settings(artifacts_root=str(tmp_path)))
    report = ReportRef(
        allure_results_dir=str(results_dir),
        allure_report_file=str(secret_file),
        html_generated=True,
    )
    _make_run_in_store(container.store, id="r-out", status=RunStatus.COMPLETED, report=report)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/r-out/report")

    assert resp.status_code == 403
    assert "TOPSECRET" not in resp.text


def test_get_report_rejects_results_dir_outside_artifacts_root(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    report_dir = tmp_path / "outside-results"
    report_dir.mkdir()
    report_file = report_dir / "index.html"
    report_file.write_text("<html>TOPSECRET</html>", encoding="utf-8")

    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    report = ReportRef(
        allure_results_dir=str(report_dir),
        allure_report_file=str(report_file),
        html_generated=True,
    )
    _make_run_in_store(container.store, id="r-out-root", status=RunStatus.COMPLETED, report=report)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/r-out-root/report")

    assert resp.status_code == 403
    assert "TOPSECRET" not in resp.text


def test_get_report_html_not_generated(tmp_path: Path) -> None:
    """Report exists but html_generated is False → 404."""
    container = _make_container()
    report = ReportRef(
        allure_results_dir=str(tmp_path),
        allure_report_file=str(tmp_path / "report.html"),
        html_generated=False,
    )
    _make_run_in_store(container.store, id="r3", report=report)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/r3/report")
    assert resp.status_code == 404


# ── GET /runs/{run_id}/report/{path} (static assets) ───────────────────


def _report_run(store: FakeStore, run_id: str, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "index.html").write_text("<html></html>")
    report = ReportRef(
        allure_results_dir=str(report_dir.parent),
        allure_report_file=str(report_dir / "index.html"),
        html_generated=True,
    )
    _make_run_in_store(store, id=run_id, status=RunStatus.COMPLETED, report=report)


def test_get_report_assets_success(tmp_path: Path) -> None:
    report_dir = tmp_path / "allure-report"
    container = _make_container(settings=Settings(artifacts_root=str(tmp_path)))
    _report_run(container.store, "ra", report_dir)
    (report_dir / "data").mkdir()
    (report_dir / "data" / "suites.json").write_text('{"ok": true}')
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/ra/report/data/suites.json")
    assert resp.status_code == 200
    assert '"ok": true' in resp.text


def test_get_report_assets_run_not_found() -> None:
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/nope/report/index.html")
    assert resp.status_code == 404


def test_get_report_assets_report_not_available() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="rb")  # no report attached
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/rb/report/index.html")
    assert resp.status_code == 404


def test_get_report_assets_traversal_blocked(tmp_path: Path) -> None:
    report_dir = tmp_path / "allure-report"
    container = _make_container(settings=Settings(artifacts_root=str(tmp_path)))
    _report_run(container.store, "rc", report_dir)
    # A symlink inside the report dir pointing outside must not be served.
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    (report_dir / "escape").symlink_to(secret)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/rc/report/escape")
    assert resp.status_code == 403
    assert "TOPSECRET" not in resp.text


def test_get_report_assets_missing_file(tmp_path: Path) -> None:
    report_dir = tmp_path / "allure-report"
    container = _make_container(settings=Settings(artifacts_root=str(tmp_path)))
    _report_run(container.store, "rd", report_dir)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/rd/report/nonexistent.js")
    assert resp.status_code == 404


# ── GET /runs/{run_id}/artifacts (Playwright artifacts) ────────────────


def _playwright_artifact_run(tmp_path: Path, run_id: str = "pw-run") -> tuple[Container, Path]:
    artifacts_root = tmp_path / "artifacts"
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(
        container.store,
        id=run_id,
        runner="playwright",
        status=RunStatus.COMPLETED,
    )
    artifact_dir = artifacts_root / run_id / "results" / "playwright-results"
    artifact_dir.mkdir(parents=True)
    return container, artifact_dir


def test_list_run_artifacts_returns_playwright_files(tmp_path: Path) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path)
    nested = artifact_dir / "failed-case"
    nested.mkdir()
    (nested / "trace.zip").write_bytes(b"trace-data")
    (nested / "screenshot.png").write_bytes(b"png-data")
    (artifact_dir / "raw-artifact").write_bytes(b"raw")
    (artifact_dir / "empty-dir").mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    (artifact_dir / "escape").symlink_to(secret)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts")

    assert resp.status_code == 200
    assert resp.json() == {
        "artifacts": [
            {"path": "failed-case/screenshot.png", "size_bytes": 8, "content_type": "image/png"},
            {
                "path": "failed-case/trace.zip",
                "size_bytes": 10,
                "content_type": "application/zip",
            },
            {"path": "raw-artifact", "size_bytes": 3, "content_type": "application/octet-stream"},
        ]
    }


def test_list_run_artifacts_missing_dir_returns_empty(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(
        container.store,
        id="pw-run",
        runner="playwright",
        status=RunStatus.COMPLETED,
    )

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts")

    assert resp.status_code == 200
    assert resp.json() == {"artifacts": []}


def test_list_run_artifacts_run_not_found(tmp_path: Path) -> None:
    container = _make_container(settings=Settings(artifacts_root=str(tmp_path / "artifacts")))

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/missing/artifacts")

    assert resp.status_code == 404


def test_list_run_artifacts_non_owner_forbidden(tmp_path: Path) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path)
    (artifact_dir / "trace.zip").write_bytes(b"trace")
    run = container.store._runs["pw-run"]
    container.store._runs["pw-run"] = run.model_copy(update={"created_by": "alice"})

    app = create_app(container)
    _override_user(app, "bob", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts")

    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("username", "role", "expected_status"),
    [
        ("alice", UserRole.USER, 200),
        ("admin", UserRole.ADMIN, 200),
        ("bob", UserRole.USER, 403),
    ],
)
@pytest.mark.parametrize(
    "path",
    [
        "/runs/pw-run/artifacts",
        "/runs/pw-run/artifacts/trace.zip",
        "/runs/pw-run/artifacts.zip",
    ],
)
def test_run_artifact_endpoints_enforce_owner_or_admin_access(
    tmp_path: Path,
    username: str,
    role: UserRole,
    expected_status: int,
    path: str,
) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path)
    (artifact_dir / "trace.zip").write_bytes(b"trace")
    run = container.store._runs["pw-run"]
    container.store._runs["pw-run"] = run.model_copy(update={"created_by": "alice"})

    app = create_app(container)
    _override_user(app, username, role)
    with TestClient(app) as client:
        resp = client.get(path)

    assert resp.status_code == expected_status
    if expected_status == 200:
        if path.endswith("/artifacts"):
            assert resp.json()["artifacts"] == [
                {
                    "path": "trace.zip",
                    "size_bytes": 5,
                    "content_type": "application/zip",
                }
            ]
        elif path.endswith(".zip"):
            assert resp.headers["content-type"] == "application/zip"
        else:
            assert resp.content == b"trace"
    else:
        assert "trace" not in resp.text


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/runs/pw-link/artifacts", 200),
        ("/runs/pw-link/artifacts.zip", 404),
        ("/runs/pw-link/artifacts/secret.txt", 404),
    ],
)
def test_run_artifact_endpoints_do_not_follow_run_dir_symlink_escape(
    tmp_path: Path, path: str, expected_status: int
) -> None:
    artifacts_root = tmp_path / "artifacts"
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(
        container.store,
        id="pw-link",
        runner="playwright",
        status=RunStatus.COMPLETED,
    )
    outside_root = tmp_path / "outside-run"
    outside_artifacts = outside_root / "results" / "playwright-results"
    outside_artifacts.mkdir(parents=True)
    (outside_artifacts / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
    artifacts_root.mkdir()
    (artifacts_root / "pw-link").symlink_to(outside_root, target_is_directory=True)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get(path)

    assert resp.status_code == expected_status
    if path.endswith("/artifacts"):
        assert resp.json() == {"artifacts": []}
    assert b"TOPSECRET" not in resp.content


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/runs/pw-nested-link/artifacts", 200),
        ("/runs/pw-nested-link/artifacts.zip", 404),
        ("/runs/pw-nested-link/artifacts/secret.txt", 404),
    ],
)
def test_run_artifact_endpoints_do_not_follow_artifact_dir_symlink_escape(
    tmp_path: Path, path: str, expected_status: int
) -> None:
    artifacts_root = tmp_path / "artifacts"
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(
        container.store,
        id="pw-nested-link",
        runner="playwright",
        status=RunStatus.COMPLETED,
    )
    outside_artifacts = tmp_path / "outside-results"
    outside_artifacts.mkdir()
    (outside_artifacts / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
    run_results = artifacts_root / "pw-nested-link" / "results"
    run_results.mkdir(parents=True)
    (run_results / "playwright-results").symlink_to(outside_artifacts, target_is_directory=True)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get(path)

    assert resp.status_code == expected_status
    if path.endswith("/artifacts"):
        assert resp.json() == {"artifacts": []}
    assert b"TOPSECRET" not in resp.content


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/runs/pw-resolve-error/artifacts", 200),
        ("/runs/pw-resolve-error/artifacts.zip", 404),
        ("/runs/pw-resolve-error/artifacts/secret.txt", 404),
    ],
)
def test_run_artifact_endpoints_hide_artifact_root_resolve_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, expected_status: int
) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path, run_id="pw-resolve-error")
    (artifact_dir / "secret.txt").write_text("SHOULD_NOT_LEAK", encoding="utf-8")

    original_resolve = Path.resolve

    def _raise_on_artifact_root(self: Path, *args: object, **kwargs: object):
        if self == artifact_dir:
            raise OSError("resolve failed")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _raise_on_artifact_root)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get(path)

    assert resp.status_code == expected_status
    if path.endswith("/artifacts"):
        assert resp.json() == {"artifacts": []}
    assert b"SHOULD_NOT_LEAK" not in resp.content


def test_download_run_artifact_success(tmp_path: Path) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path)
    (artifact_dir / "trace.zip").write_bytes(b"trace-data")

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts/trace.zip")

    assert resp.status_code == 200
    assert resp.content == b"trace-data"


def test_download_run_artifact_run_not_found(tmp_path: Path) -> None:
    container = _make_container(settings=Settings(artifacts_root=str(tmp_path / "artifacts")))

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/missing/artifacts/trace.zip")

    assert resp.status_code == 404


def test_download_run_artifact_traversal_blocked(tmp_path: Path) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    (artifact_dir / "escape").symlink_to(secret)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts/escape")

    assert resp.status_code == 403
    assert "TOPSECRET" not in resp.text


def test_download_run_artifact_missing_file(tmp_path: Path) -> None:
    container, _artifact_dir = _playwright_artifact_run(tmp_path)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts/missing.zip")

    assert resp.status_code == 404


def test_download_run_artifacts_archive_success(tmp_path: Path) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path)
    nested = artifact_dir / "failed-case"
    nested.mkdir()
    (nested / "trace.zip").write_bytes(b"trace-data")
    (nested / "screenshot.png").write_bytes(b"png-data")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    (artifact_dir / "escape").symlink_to(secret)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts.zip")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"] == ('attachment; filename="pw-run-artifacts.zip"')
    archive_path = tmp_path / "artifacts.zip"
    archive_path.write_bytes(resp.content)
    with ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "failed-case/screenshot.png",
            "failed-case/trace.zip",
        ]
        assert archive.read("failed-case/trace.zip") == b"trace-data"
        assert b"TOPSECRET" not in resp.content


def test_download_run_artifacts_archive_missing_dir_returns_404(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts"
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(
        container.store,
        id="pw-run",
        runner="playwright",
        status=RunStatus.COMPLETED,
    )

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts.zip")

    assert resp.status_code == 404


def test_download_run_artifacts_archive_run_not_found(tmp_path: Path) -> None:
    container = _make_container(settings=Settings(artifacts_root=str(tmp_path / "artifacts")))

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/missing/artifacts.zip")

    assert resp.status_code == 404


def test_download_run_artifacts_archive_non_owner_forbidden(tmp_path: Path) -> None:
    container, artifact_dir = _playwright_artifact_run(tmp_path)
    (artifact_dir / "trace.zip").write_bytes(b"trace")
    run = container.store._runs["pw-run"]
    container.store._runs["pw-run"] = run.model_copy(update={"created_by": "alice"})

    app = create_app(container)
    _override_user(app, "bob", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/pw-run/artifacts.zip")

    assert resp.status_code == 403


# ── object-level authorization (SEC-4) ─────────────────────────────────


def _override_user(app: FastAPI, username: str, role: UserRole) -> None:
    async def _u() -> User:
        return User(username=username, role=role, created_at=NOW)

    app.dependency_overrides[get_current_user] = _u


# ── GET /runs/{run_id}/diff (cross-run stage 2) ─────────────────────────


def _seed_cases(store: FakeStore, run_id: str, cases: list[TestCaseResult]) -> None:
    store._cases[run_id] = list(cases)


def test_run_diff_with_baseline_classifies_cases() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="base",
        created_by="alice",
        status=RunStatus.COMPLETED,
        created_at=NOW,
    )
    _make_run_in_store(
        container.store,
        id="head",
        created_by="alice",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=5),
    )
    _seed_cases(
        container.store,
        "base",
        [
            TestCaseResult(suite="s", name="keeps", status="passed", duration_ms=0),
            TestCaseResult(suite="s", name="regressed", status="passed", duration_ms=0),
        ],
    )
    _seed_cases(
        container.store,
        "head",
        [
            TestCaseResult(suite="s", name="keeps", status="passed", duration_ms=0),
            TestCaseResult(suite="s", name="regressed", status="failed", duration_ms=0),
        ],
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/head/diff")
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"]["id"] == "base"
    assert [c["name"] for c in body["diff"]["new_failures"]] == ["regressed"]


def test_run_diff_no_baseline_returns_null_and_empty() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="solo",
        created_by="alice",
        status=RunStatus.COMPLETED,
        created_at=NOW,
    )
    _seed_cases(
        container.store,
        "solo",
        [
            TestCaseResult(suite="s", name="t", status="failed", duration_ms=0),
        ],
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/solo/diff")
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"] is None
    assert body["diff"]["new_failures"] == []
    assert body["diff"]["new_cases"] == []


def test_run_diff_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/ghost/diff")
    assert resp.status_code == 404


def test_run_diff_baseline_excludes_other_owners_run() -> None:
    """Owner-scope: a non-admin's diff must not pick another user's run as the
    baseline — otherwise the response leaks that run's id and case-level results
    (names/statuses/messages). Mirrors how GET /runs and /runs/trend filter."""
    container = _make_container()
    # bob's earlier, same-scope COMPLETED run — must stay invisible to alice.
    _make_run_in_store(
        container.store,
        id="bob-base",
        created_by="bob",
        status=RunStatus.COMPLETED,
        created_at=NOW,
    )
    _make_run_in_store(
        container.store,
        id="head",
        created_by="alice",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=5),
    )
    _seed_cases(
        container.store,
        "bob-base",
        [
            TestCaseResult(suite="s", name="secret", status="failed", duration_ms=0),
        ],
    )
    _seed_cases(
        container.store,
        "head",
        [
            TestCaseResult(suite="s", name="secret", status="passed", duration_ms=0),
        ],
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/head/diff")
    assert resp.status_code == 200
    body = resp.json()
    # No baseline alice may see → null + empty diff, not bob's run leaked as a
    # "fixed" case.
    assert body["baseline"] is None
    assert body["diff"]["fixed"] == []


def test_run_diff_baseline_spans_owners_for_admin() -> None:
    """Admin spans owners (like GET /runs/trend): the baseline may be another
    user's earlier same-scope run. Locks the fix to filter by the *requester*,
    not by the head run's owner."""
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="bob-base",
        created_by="bob",
        status=RunStatus.COMPLETED,
        created_at=NOW,
    )
    _make_run_in_store(
        container.store,
        id="head",
        created_by="alice",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=5),
    )
    _seed_cases(
        container.store,
        "bob-base",
        [
            TestCaseResult(suite="s", name="t", status="passed", duration_ms=0),
        ],
    )
    _seed_cases(
        container.store,
        "head",
        [
            TestCaseResult(suite="s", name="t", status="passed", duration_ms=0),
        ],
    )
    app = create_app(container)
    _override_user(app, "carol", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.get("/runs/head/diff")
    assert resp.status_code == 200
    assert resp.json()["baseline"]["id"] == "bob-base"


def test_run_diff_baseline_prefers_same_profile_scope() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="same-profile-base",
        created_by="alice",
        tests_path="suite_a",
        status=RunStatus.COMPLETED,
        created_at=NOW,
        profile_id="profile-a",
    )
    _make_run_in_store(
        container.store,
        id="sibling-profile-base",
        created_by="alice",
        tests_path="suite_a",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=3),
        profile_id="profile-b",
    )
    _make_run_in_store(
        container.store,
        id="head",
        created_by="alice",
        tests_path="suite_a",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=5),
        profile_id="profile-a",
    )
    _seed_cases(
        container.store,
        "same-profile-base",
        [TestCaseResult(suite="s", name="t", status="passed", duration_ms=0)],
    )
    _seed_cases(
        container.store,
        "sibling-profile-base",
        [TestCaseResult(suite="s", name="t", status="failed", duration_ms=0)],
    )
    _seed_cases(
        container.store,
        "head",
        [TestCaseResult(suite="s", name="t", status="failed", duration_ms=0)],
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/head/diff")
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"]["id"] == "same-profile-base"
    assert [c["name"] for c in body["diff"]["new_failures"]] == ["t"]


# ── GET /runs/trend (cross-run stage 1) ─────────────────────────────────


def test_runs_trend_ascending_points_filtered_by_suite() -> None:
    container = _make_container()
    summ = TestSummary(total=10, passed=7, failed=3, skipped=0, error=0, duration_ms=1)
    _make_run_in_store(
        container.store,
        id="r2",
        created_by="alice",
        tests_path="suite_a",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=5),
        summary=summ,
    )
    _make_run_in_store(
        container.store,
        id="r1",
        created_by="alice",
        tests_path="suite_a",
        status=RunStatus.COMPLETED,
        created_at=NOW,
        summary=summ,
    )
    # excluded: other suite, and a non-COMPLETED run of suite_a
    _make_run_in_store(
        container.store,
        id="other",
        created_by="alice",
        tests_path="suite_b",
        status=RunStatus.COMPLETED,
        created_at=NOW,
        summary=summ,
    )
    _make_run_in_store(
        container.store,
        id="failed",
        created_by="alice",
        tests_path="suite_a",
        status=RunStatus.FAILED,
        created_at=NOW + timedelta(minutes=9),
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/trend?tests_path=suite_a")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tests_path"] == "suite_a"
    assert [p["run_id"] for p in body["points"]] == ["r1", "r2"]  # oldest-first
    assert body["points"][0]["pass_rate"] == 0.7


def test_runs_trend_owner_scoped_for_non_admin() -> None:
    container = _make_container()
    summ = TestSummary(total=2, passed=2, failed=0, skipped=0, error=0, duration_ms=1)
    _make_run_in_store(
        container.store,
        id="mine",
        created_by="alice",
        tests_path="s",
        status=RunStatus.COMPLETED,
        created_at=NOW,
        summary=summ,
    )
    _make_run_in_store(
        container.store,
        id="theirs",
        created_by="bob",
        tests_path="s",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=1),
        summary=summ,
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/trend?tests_path=s")
    assert [p["run_id"] for p in resp.json()["points"]] == ["mine"]  # bob's excluded


def test_runs_trend_admin_sees_all_owners() -> None:
    container = _make_container()
    summ = TestSummary(total=1, passed=1, failed=0, skipped=0, error=0, duration_ms=1)
    _make_run_in_store(
        container.store,
        id="a",
        created_by="alice",
        tests_path="s",
        status=RunStatus.COMPLETED,
        created_at=NOW,
        summary=summ,
    )
    _make_run_in_store(
        container.store,
        id="b",
        created_by="bob",
        tests_path="s",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=1),
        summary=summ,
    )
    app = create_app(container)
    _override_user(app, "admin", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.get("/runs/trend?tests_path=s")
    assert [p["run_id"] for p in resp.json()["points"]] == ["a", "b"]  # both owners


def test_runs_trend_can_be_profile_scoped() -> None:
    container = _make_container()
    summ = TestSummary(total=4, passed=4, failed=0, skipped=0, error=0, duration_ms=1)
    _make_run_in_store(
        container.store,
        id="profile-a-old",
        created_by="alice",
        tests_path="s",
        status=RunStatus.COMPLETED,
        created_at=NOW,
        summary=summ,
        profile_id="profile-a",
    )
    _make_run_in_store(
        container.store,
        id="profile-b",
        created_by="alice",
        tests_path="s",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=1),
        summary=summ,
        profile_id="profile-b",
    )
    _make_run_in_store(
        container.store,
        id="profile-a-new",
        created_by="alice",
        tests_path="s",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=2),
        summary=summ,
        profile_id="profile-a",
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/trend?tests_path=s&profile_id=profile-a")
    assert resp.status_code == 200
    assert [p["run_id"] for p in resp.json()["points"]] == [
        "profile-a-old",
        "profile-a-new",
    ]


def test_runs_trend_empty_for_unknown_suite() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/trend?tests_path=nope")
    assert resp.status_code == 200
    assert resp.json()["points"] == []


def test_runs_trend_rejects_nonpositive_limit() -> None:
    # BUG: limit=0 used to silently mean "all points" (Python's `[-0:]` slice
    # trap) instead of "no points" — reject non-positive limits outright
    # rather than let that inversion reach the response body.
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/trend?tests_path=suite_a&limit=0")
    assert resp.status_code == 422


# ── GET /metrics ────────────────────────────────────────────────────────


def test_metrics_empty_data() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_runs"] == 0
    assert body["completed_runs"] == 0
    assert body["pass_rate_7d"] == 0.0
    assert body["avg_duration_ms_7d"] == 0.0
    assert body["flaky_count_30d"] == 0
    assert body["run_volume_7d"] == 0
    assert body["suites"] == []


def test_metrics_computes_pass_rate_and_duration() -> None:
    container = _make_container()
    recent = datetime.now(UTC) - timedelta(hours=1)
    # 2 completed runs: one passed, one failed
    _make_run_in_store(
        container.store,
        id="r1",
        created_by="alice",
        tests_path="suite/",
        status=RunStatus.COMPLETED,
        created_at=recent,
        finished_at=recent + timedelta(seconds=10),
        summary=TestSummary(
            total=5, passed=5, failed=0, skipped=0, error=0, duration_ms=1000, pass_rate=1.0
        ),
    )
    _make_run_in_store(
        container.store,
        id="r2",
        created_by="alice",
        tests_path="suite/",
        status=RunStatus.COMPLETED,
        created_at=recent + timedelta(minutes=1),
        finished_at=recent + timedelta(minutes=1, seconds=20),
        summary=TestSummary(
            total=5, passed=3, failed=2, skipped=0, error=0, duration_ms=2000, pass_rate=0.6
        ),
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    body = resp.json()
    assert body["completed_runs"] == 2
    assert body["pass_rate_7d"] == 0.5  # 1 of 2 passed
    assert body["avg_duration_ms_7d"] == 1500.0  # (1000+2000)/2
    assert body["run_volume_7d"] == 2
    assert len(body["suites"]) == 1
    assert body["suites"][0]["tests_path"] == "suite/"
    assert body["suites"][0]["pass_rate"] == 0.5


def test_metrics_excludes_non_completed_runs() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="r1",
        created_by="alice",
        tests_path="suite/",
        status=RunStatus.QUEUED,
        created_at=NOW,
    )
    _make_run_in_store(
        container.store,
        id="r2",
        created_by="alice",
        tests_path="suite/",
        status=RunStatus.RUNNING,
        created_at=NOW + timedelta(minutes=1),
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    body = resp.json()
    assert body["total_runs"] == 2
    assert body["completed_runs"] == 0
    assert body["run_volume_7d"] == 0


def test_metrics_owner_scoped_for_non_admin() -> None:
    container = _make_container()
    recent = datetime.now(UTC) - timedelta(hours=1)
    _make_run_in_store(
        container.store,
        id="r-alice",
        created_by="alice",
        tests_path="suite/",
        status=RunStatus.COMPLETED,
        created_at=recent,
        finished_at=recent + timedelta(seconds=5),
        summary=TestSummary(
            total=2, passed=2, failed=0, skipped=0, error=0, duration_ms=100, pass_rate=1.0
        ),
    )
    _make_run_in_store(
        container.store,
        id="r-bob",
        created_by="bob",
        tests_path="bob_private_suite/",
        status=RunStatus.COMPLETED,
        created_at=recent,
        finished_at=recent + timedelta(seconds=5),
        summary=TestSummary(
            total=2, passed=0, failed=2, skipped=0, error=0, duration_ms=100, pass_rate=0.0
        ),
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    body_text = resp.text
    body = resp.json()
    assert body["total_runs"] == 1  # only alice's run
    assert body["pass_rate_7d"] == 1.0
    assert len(body["suites"]) == 1
    assert body["suites"][0]["tests_path"] == "suite/"
    assert "bob_private_suite/" not in body_text


def test_metrics_per_suite_breakdown() -> None:
    container = _make_container()
    recent = datetime.now(UTC) - timedelta(hours=1)
    _make_run_in_store(
        container.store,
        id="r1",
        created_by="alice",
        tests_path="suite_a/",
        status=RunStatus.COMPLETED,
        created_at=recent,
        finished_at=recent + timedelta(seconds=5),
        summary=TestSummary(
            total=5, passed=5, failed=0, skipped=0, error=0, duration_ms=500, pass_rate=1.0
        ),
    )
    _make_run_in_store(
        container.store,
        id="r2",
        created_by="alice",
        tests_path="suite_b/",
        status=RunStatus.COMPLETED,
        created_at=recent,
        finished_at=recent + timedelta(seconds=5),
        summary=TestSummary(
            total=3, passed=1, failed=2, skipped=0, error=0, duration_ms=300, pass_rate=0.33
        ),
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    suites = resp.json()["suites"]
    assert len(suites) == 2
    by_path = {s["tests_path"]: s for s in suites}
    assert by_path["suite_a/"]["pass_rate"] == 1.0
    assert by_path["suite_b/"]["pass_rate"] == 0.0  # 0 of 1 passed


def test_metrics_admin_sees_all_owners_and_zero_duration_suites() -> None:
    container = _make_container()
    recent = datetime.now(UTC) - timedelta(hours=1)
    _make_run_in_store(
        container.store,
        id="r-alice",
        created_by="alice",
        tests_path="suite_a/",
        status=RunStatus.COMPLETED,
        created_at=recent,
        finished_at=recent + timedelta(seconds=5),
        summary=TestSummary(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            error=0,
            duration_ms=0,
            pass_rate=1.0,
        ),
    )
    _make_run_in_store(
        container.store,
        id="r-bob",
        created_by="bob",
        tests_path="suite_b/",
        status=RunStatus.COMPLETED,
        created_at=recent,
        finished_at=recent + timedelta(seconds=5),
        summary=TestSummary(
            total=1,
            passed=0,
            failed=1,
            skipped=0,
            error=0,
            duration_ms=0,
            pass_rate=0.0,
        ),
    )
    app = create_app(container)
    _override_user(app, "admin", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    body = resp.json()
    assert body["total_runs"] == 2
    assert body["avg_duration_ms_7d"] == 0.0
    assert {s["tests_path"] for s in body["suites"]} == {"suite_a/", "suite_b/"}
    assert all(s["avg_duration_ms"] == 0.0 for s in body["suites"])


def test_metrics_includes_completed_suite_with_no_recent_summary() -> None:
    container = _make_container()
    old = datetime.now(UTC) - timedelta(days=10)
    _make_run_in_store(
        container.store,
        id="old-completed",
        created_by="alice",
        tests_path="old_suite/",
        status=RunStatus.COMPLETED,
        created_at=old,
        finished_at=old + timedelta(seconds=5),
        summary=None,
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    suite = resp.json()["suites"][0]
    assert suite["tests_path"] == "old_suite/"
    assert suite["total_runs"] == 0
    assert suite["pass_rate"] == 0.0
    assert suite["avg_duration_ms"] == 0.0
    assert suite["last_run_at"] is not None


def test_metrics_passes_configured_flaky_policy_to_store() -> None:
    settings = Settings(flaky_min_observations=6, flaky_flip_threshold=4)
    container = _make_container(settings=settings)
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert container.store.count_flaky_calls[-1] == {
        "days": 30,
        "created_by": "alice",
        "min_observations": 6,
        "flip_threshold": 4,
    }


# ── GET /cases/history (cross-run stage 3) ──────────────────────────────


def test_case_history_oldest_first_with_flaky_verdict() -> None:
    container = _make_container()
    for i, st in enumerate(["passed", "failed", "passed", "failed"]):
        _make_run_in_store(
            container.store,
            id=f"h{i}",
            created_by="alice",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=NOW + timedelta(minutes=i),
        )
        _seed_cases(
            container.store,
            f"h{i}",
            [
                TestCaseResult(suite="s", name="t", status=st, duration_ms=0),
            ],
        )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/cases/history?tests_path=suite_a&suite=s&name=t")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["status"] for p in body["points"]] == ["passed", "failed", "passed", "failed"]
    assert body["flaky"] is True
    assert body["flip_count"] == 3


def test_case_history_two_flips_below_calibrated_threshold() -> None:
    container = _make_container()
    for i, st in enumerate(["passed", "failed", "passed"]):
        _make_run_in_store(
            container.store,
            id=f"h2-{i}",
            created_by="alice",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=NOW + timedelta(minutes=i),
        )
        _seed_cases(
            container.store,
            f"h2-{i}",
            [TestCaseResult(suite="s", name="t", status=st, duration_ms=0)],
        )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/cases/history?tests_path=suite_a&suite=s&name=t")
    assert resp.status_code == 200
    assert resp.json()["flaky"] is False
    assert resp.json()["flip_count"] == 2


def test_case_history_owner_scoped_for_non_admin() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="mine",
        created_by="alice",
        tests_path="s2",
        status=RunStatus.COMPLETED,
        created_at=NOW,
    )
    _seed_cases(
        container.store,
        "mine",
        [TestCaseResult(suite="x", name="t", status="passed", duration_ms=0)],
    )
    _make_run_in_store(
        container.store,
        id="theirs",
        created_by="bob",
        tests_path="s2",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=1),
    )
    _seed_cases(
        container.store,
        "theirs",
        [TestCaseResult(suite="x", name="t", status="failed", duration_ms=0)],
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/cases/history?tests_path=s2&suite=x&name=t")
    assert [p["status"] for p in resp.json()["points"]] == ["passed"]  # bob's excluded


def test_case_history_admin_sees_all_owners() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="ca",
        created_by="alice",
        tests_path="s3",
        status=RunStatus.COMPLETED,
        created_at=NOW,
    )
    _seed_cases(
        container.store,
        "ca",
        [TestCaseResult(suite="x", name="t", status="passed", duration_ms=0)],
    )
    _make_run_in_store(
        container.store,
        id="cb",
        created_by="bob",
        tests_path="s3",
        status=RunStatus.COMPLETED,
        created_at=NOW + timedelta(minutes=1),
    )
    _seed_cases(
        container.store,
        "cb",
        [TestCaseResult(suite="x", name="t", status="passed", duration_ms=0)],
    )
    app = create_app(container)
    _override_user(app, "admin", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.get("/cases/history?tests_path=s3&suite=x&name=t")
    assert len(resp.json()["points"]) == 2


def test_case_history_can_be_profile_scoped() -> None:
    container = _make_container()
    for rid, profile_id, status, minute in [
        ("p1-old", "profile-a", "passed", 0),
        ("p2", "profile-b", "failed", 1),
        ("p1-new", "profile-a", "passed", 2),
    ]:
        _make_run_in_store(
            container.store,
            id=rid,
            created_by="alice",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=NOW + timedelta(minutes=minute),
            profile_id=profile_id,
        )
        _seed_cases(
            container.store,
            rid,
            [TestCaseResult(suite="s", name="t", status=status, duration_ms=0)],
        )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/cases/history?tests_path=suite_a&suite=s&name=t&profile_id=profile-a")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["status"] for p in body["points"]] == ["passed", "passed"]
    assert body["flaky"] is False
    assert body["flip_count"] == 0


def test_case_history_empty_for_unknown_case() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/cases/history?tests_path=nope&suite=x&name=y")
    assert resp.status_code == 200
    assert resp.json() == {"points": [], "flaky": False, "flip_count": 0}


def test_case_history_rejects_nonpositive_limit() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/cases/history?tests_path=nope&suite=x&name=y&limit=0")
    assert resp.status_code == 422


def test_run_diff_forbidden_for_non_owner() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="r",
        created_by="bob",
        status=RunStatus.COMPLETED,
        created_at=NOW,
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/r/diff")
    assert resp.status_code == 403


# ── POST /runs/{run_id}/cancel ──────────────────────────────────────────


def test_cancel_run_owner_marks_cancelled() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store, id="r-cancel", created_by="alice", status=RunStatus.RUNNING
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/r-cancel/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_cancel_run_forbidden_for_non_owner() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="r-cancel", created_by="bob", status=RunStatus.RUNNING)
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/r-cancel/cancel")
    assert resp.status_code == 403


def test_cancel_run_forbidden_precedes_terminal_state() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store, id="r-cancel", created_by="bob", status=RunStatus.COMPLETED
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/r-cancel/cancel")
    assert resp.status_code == 403
    assert "already finished" not in resp.text


def test_cancel_run_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/ghost/cancel")
    assert resp.status_code == 404


def test_cancel_run_terminal_returns_409() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store, id="r-done", created_by="alice", status=RunStatus.COMPLETED
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/r-done/cancel")
    assert resp.status_code == 409


# ── DELETE /runs/{run_id} (P1-4) ─────────────────────────────────────────


def test_delete_run_owner_removes_row_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-4: deleting a finished, unlocked run drops its DB row and wipes its
    artifact directory."""
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    _make_run_in_store(container.store, id="r-del", created_by="alice", status=RunStatus.COMPLETED)
    run_dir = tmp_path / "r-del"
    run_dir.mkdir()
    (run_dir / "stdout.log").write_text("log")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.delete("/runs/r-del")

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert not run_dir.exists()
    assert "r-del" not in container.store._runs  # type: ignore[attr-defined]


def test_delete_run_does_not_remove_artifacts_parent_for_traversal_run_id(
    tmp_path: Path,
) -> None:
    """A poisoned run id must not let artifact cleanup escape artifacts_root."""
    workspace = tmp_path / "workspace"
    artifacts_root = workspace / "artifacts"
    protected_dir = workspace / "protected"
    artifacts_root.mkdir(parents=True)
    protected_dir.mkdir()
    protected_file = protected_dir / "keep.txt"
    protected_file.write_text("KEEP", encoding="utf-8")
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(container.store, id="..", created_by="alice", status=RunStatus.COMPLETED)
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.delete("/runs/%2E%2E")

    assert resp.status_code == 200
    assert protected_file.read_text(encoding="utf-8") == "KEEP"
    assert artifacts_root.exists()
    assert ".." not in container.store._runs  # type: ignore[attr-defined]


def test_delete_run_without_artifact_dir_still_deletes_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-4: a run whose artifact dir was already cleaned up still deletes its row
    (covers the ``run_dir`` absent branch)."""
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    _make_run_in_store(container.store, id="r-bare", created_by="alice", status=RunStatus.FAILED)
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.delete("/runs/r-bare")

    assert resp.status_code == 200
    assert "r-bare" not in container.store._runs  # type: ignore[attr-defined]


def test_delete_run_forbidden_for_non_owner() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="r-del", created_by="bob", status=RunStatus.COMPLETED)
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/runs/r-del")
    assert resp.status_code == 403
    assert "r-del" in container.store._runs  # type: ignore[attr-defined]


def test_delete_run_forbidden_precedes_locked_state() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="r-lock",
        created_by="bob",
        status=RunStatus.COMPLETED,
        locked=True,
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/runs/r-lock")
    assert resp.status_code == 403
    assert "locked" not in resp.text
    assert "r-lock" in container.store._runs  # type: ignore[attr-defined]


def test_delete_run_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/runs/ghost")
    assert resp.status_code == 404


def test_delete_run_locked_returns_409() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="r-lock",
        created_by="alice",
        status=RunStatus.COMPLETED,
        locked=True,
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/runs/r-lock")
    assert resp.status_code == 409
    assert "r-lock" in container.store._runs  # type: ignore[attr-defined]


def test_delete_run_active_returns_409() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="r-run", created_by="alice", status=RunStatus.RUNNING)
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/runs/r-run")
    assert resp.status_code == 409


# ── POST /runs/{run_id}/rerun (P2-7) ─────────────────────────────────────


def test_rerun_request_from_run_preserves_parameters() -> None:
    """from_run carries every executable parameter so a re-run is faithful;
    selective fields stay empty because args already holds the compiled argv."""
    run = Run(
        id="x",
        status=RunStatus.COMPLETED,
        runner="playwright",
        created_by="alice",
        tests_path="suite/",
        args=["--workers", "2"],
        allure_enabled=False,
        timeout=600,
        executor_mode="docker",
        env={"BASE_URL": "https://x"},
        created_at=NOW,
    )
    req = RunRequest.from_run(run)
    assert req.tests_path == "suite/"
    assert req.runner == "playwright"
    assert req.args == ["--workers", "2"]
    assert req.allure is False
    assert req.timeout == 600
    assert req.executor_mode == "docker"
    assert req.env == {"BASE_URL": "https://x"}
    assert req.selected_files == [] and req.selected_markers == []


def test_rerun_creates_new_run_owned_by_caller() -> None:
    container = _make_container()
    _make_run_in_store(
        container.store,
        id="orig",
        created_by="alice",
        status=RunStatus.COMPLETED,
        runner="pytest",
        tests_path="suite/",
        args=["-m", "smoke"],
        executor_mode="docker",
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/orig/rerun")
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] != "orig"
    assert body["created_by"] == "alice"
    new_run = container.store._runs[body["id"]]  # type: ignore[attr-defined]
    assert new_run.args == ["-m", "smoke"]
    assert new_run.tests_path == "suite/"
    assert new_run.executor_mode == "docker"


def test_rerun_forbidden_for_non_owner() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="orig", created_by="bob", status=RunStatus.COMPLETED)
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/orig/rerun")
    assert resp.status_code == 403


def test_rerun_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/ghost/rerun")
    assert resp.status_code == 404


def test_get_run_forbidden_for_non_owner() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="r1", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/r1")
    assert resp.status_code == 403


def test_get_run_allowed_for_owner() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="r1", created_by="bob")
    app = create_app(container)
    _override_user(app, "bob", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs/r1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "r1"


def test_list_runs_non_admin_sees_only_own() -> None:
    container = _make_container()
    _make_run_in_store(container.store, id="ra", created_by="alice")
    _make_run_in_store(container.store, id="rb", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/runs")
    ids = [r["id"] for r in resp.json()["runs"]]
    assert "ra" in ids
    assert "rb" not in ids


def test_list_tests_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test successful listing of valid test directories, filtering out hidden/internal ones."""
    # Create subdirectories
    (tmp_path / "suite_a").mkdir()
    (tmp_path / "suite_b").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "some_file.txt").touch()

    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tmp_path))

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/tests")

    assert resp.status_code == 200
    assert resp.json() == ["suite_a", "suite_b"]


def test_list_tests_missing_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test empty list return when QARUNNER_TESTS_ROOT directory doesn't exist."""
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", "/nonexistent/tests/path")

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/tests")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_tests_and_suites_hide_other_users_registered_suites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "alice_suite").mkdir()
    (tests_root / "admin_suite").mkdir()
    (tests_root / "bob_suite").mkdir()
    (tests_root / "manual_suite").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(
        container.store,
        name="alice_suite",
        created_by="alice",
        repo_url="https://example.com/alice.git",
    )
    _save_suite_in_store(
        container.store,
        name="admin_suite",
        created_by="admin",
        repo_url="https://example.com/admin-shared.git",
    )
    _save_suite_in_store(
        container.store,
        name="bob_suite",
        created_by="bob",
        repo_url="https://example.com/bob-private.git",
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        tests_resp = client.get("/tests")
        suites_resp = client.get("/suites")

    assert tests_resp.status_code == 200
    assert tests_resp.json() == ["admin_suite", "alice_suite", "manual_suite"]
    assert "bob_suite" not in tests_resp.text

    assert suites_resp.status_code == 200
    suites_body = suites_resp.text
    suite_names = [suite["name"] for suite in suites_resp.json()]
    assert suite_names == ["admin_suite", "alice_suite", "manual_suite"]
    assert "admin-shared" in suites_body
    assert "bob_suite" not in suites_body
    assert "bob-private" not in suites_body


def test_list_suites_detailed_left_join(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /suites = filesystem entities left-joined with metadata (R5).

    Registered dirs carry their recorded source/repo_url/ref; unregistered dirs
    still appear, defaulting to ``local`` (manual placement isn't broken).
    """
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "git_suite").mkdir()
    (tests_root / "manual_suite").mkdir()
    (tests_root / ".hidden").mkdir()
    (tests_root / "__pycache__").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(
        container.store,
        name="git_suite",
        source="git",
        repo_url="https://example.com/r.git",
        ref="main",
    )
    app = create_app(container)

    with TestClient(app) as client:
        resp = client.get("/suites")

    assert resp.status_code == 200
    by_name = {s["name"]: s for s in resp.json()}
    assert set(by_name) == {"git_suite", "manual_suite"}  # hidden/__ filtered out
    assert by_name["git_suite"]["source"] == "git"
    assert by_name["git_suite"]["repo_url"] == "https://example.com/r.git"
    assert by_name["git_suite"]["ref"] == "main"
    assert by_name["manual_suite"]["source"] == "local"
    assert by_name["manual_suite"]["repo_url"] is None


def test_list_suites_detailed_missing_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", "/nonexistent/suites/path")
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/suites")

    assert resp.status_code == 200
    assert resp.json() == []


# ── Schedules Endpoints ───────────────────────────────────────────────


def test_schedule_crud_and_preview_endpoints() -> None:
    container = _make_container()

    # 1. Mock creating a profile first, as schedules are bound to them
    profile = TestProfile(
        id="profile-abc",
        name="Reg Profile",
        tests_path="tests/",
        created_by="test_user",
        created_at=NOW,
    )

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save_profile(profile))
    loop.close()

    app = create_app(container)
    with TestClient(app) as client:
        # A. Test preview schedule with valid expression
        resp = client.get("/schedules/preview?expression=*/5 * * * *&timezone=UTC")
        assert resp.status_code == 200
        # TEST-2: don't just count — pin the actual fire times. For "*/5 * * * *"
        # they must be five strictly-increasing instants exactly five minutes
        # apart. A mutation (get_prev instead of get_next, wrong field, dropped
        # timezone) breaks ordering or spacing and turns this red.
        next_runs = [datetime.fromisoformat(t) for t in resp.json()["next_runs"]]
        assert len(next_runs) == 5
        for earlier, later in pairwise(next_runs):
            assert later > earlier
            assert later - earlier == timedelta(minutes=5)

        # B. Test preview schedule with invalid expression
        resp_err = client.get("/schedules/preview?expression=invalid_expr&timezone=UTC")
        assert resp_err.status_code == 400

        # C. Create schedule
        sched_payload = {
            "name": "Nightly Regression",
            "profile_id": "profile-abc",
            "cron_expression": "0 2 * * *",
            "enabled": True,
            "timezone": "America/New_York",
        }
        resp_create = client.post("/schedules", json=sched_payload)
        assert resp_create.status_code == 201
        sched_id = resp_create.json()["id"]
        assert resp_create.json()["name"] == "Nightly Regression"
        assert resp_create.json()["timezone"] == "America/New_York"
        # ARCH-1: the route drives the injected SchedulePort (not app.state).
        assert container.scheduler.upserted == [sched_id]

        # D. Get schedule
        resp_get = client.get(f"/schedules/{sched_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["id"] == sched_id

        # E. List schedules
        resp_list = client.get("/schedules")
        assert resp_list.status_code == 200
        assert len(resp_list.json()) == 1
        assert resp_list.json()[0]["id"] == sched_id

        # F. Update schedule
        update_payload = {
            "name": "Daily Regression Updated",
            "profile_id": "profile-abc",
            "cron_expression": "0 3 * * *",
            "enabled": False,
            "timezone": "America/Los_Angeles",
        }
        resp_update = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp_update.status_code == 200
        assert resp_update.json()["name"] == "Daily Regression Updated"
        assert resp_update.json()["cron_expression"] == "0 3 * * *"
        assert resp_update.json()["enabled"] is False
        assert resp_update.json()["timezone"] == "America/Los_Angeles"
        # Disabling routes through SchedulePort.remove.
        assert container.scheduler.removed == [sched_id]

        # G. Delete schedule
        resp_del = client.delete(f"/schedules/{sched_id}")
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "success"
        # Deletion routes through SchedulePort.remove again.
        assert container.scheduler.removed == [sched_id, sched_id]

        # H. Get deleted schedule (should be 404)
        resp_get_deleted = client.get(f"/schedules/{sched_id}")
        assert resp_get_deleted.status_code == 404


def test_create_schedule_forbidden_for_cross_owner_profile() -> None:
    container = _make_container()
    container.store._profiles["profile-bob"] = TestProfile(
        id="profile-bob",
        name="Bob Secret Profile",
        tests_path="tests/",
        created_by="bob",
        created_at=NOW,
    )

    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post(
            "/schedules",
            json={
                "name": "Alice Cross Owner Schedule",
                "profile_id": "profile-bob",
                "cron_expression": "0 2 * * *",
                "enabled": True,
                "timezone": "UTC",
            },
        )

    assert resp.status_code == 403
    assert "profile-bob" not in resp.text
    assert "Bob Secret Profile" not in resp.text
    assert container.store._schedules == {}


def test_update_schedule_forbidden_for_cross_owner_profile() -> None:
    container = _make_container()
    container.store._profiles["profile-alice"] = TestProfile(
        id="profile-alice",
        name="Alice Profile",
        tests_path="tests/",
        created_by="alice",
        created_at=NOW,
    )
    container.store._profiles["profile-bob"] = TestProfile(
        id="profile-bob",
        name="Bob Secret Profile",
        tests_path="tests/",
        created_by="bob",
        created_at=NOW,
    )
    container.store._schedules["sched-alice"] = TestSchedule(
        id="sched-alice",
        name="Alice Schedule",
        profile_id="profile-alice",
        cron_expression="0 2 * * *",
        enabled=True,
        timezone="UTC",
        created_by="alice",
        created_at=NOW,
    )

    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.put(
            "/schedules/sched-alice",
            json={
                "name": "Alice Schedule",
                "profile_id": "profile-bob",
                "cron_expression": "0 3 * * *",
                "enabled": True,
                "timezone": "UTC",
            },
        )

    assert resp.status_code == 403
    assert "profile-bob" not in resp.text
    assert "Bob Secret Profile" not in resp.text
    assert container.store._schedules["sched-alice"].profile_id == "profile-alice"
    assert container.store._schedules["sched-alice"].cron_expression == "0 2 * * *"


def test_lock_run() -> None:
    container = _make_container()
    run = Run(
        id="run-lock-test",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=NOW,
        locked=False,
    )
    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save(run))
    loop.close()

    app = create_app(container)
    with TestClient(app) as client:
        # A. Lock run
        resp = client.put("/runs/run-lock-test/lock", json={"locked": True})
        assert resp.status_code == 200
        assert resp.json()["locked"] is True

        # B. Unlock run
        resp = client.put("/runs/run-lock-test/lock", json={"locked": False})
        assert resp.status_code == 200
        assert resp.json()["locked"] is False


def test_cleanup_runs_rejects_nonpositive_retention() -> None:
    # retention_days < 1 would purge same-day / all finished runs; reject it (422)
    # rather than silently nuking artifacts on a fat-fingered 0/negative.
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs/cleanup?retention_days=0")
    assert resp.status_code == 422


def test_cleanup_skips_run_locked_after_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TOCTOU: a run selected as unlocked but locked before deletion must survive.
    # The store holds it LOCKED; get_old_unlocked_runs is stubbed to return the
    # stale unlocked snapshot it saw a moment earlier.
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    locked_run = _make_run_in_store(
        container.store, id="r-lock", status=RunStatus.COMPLETED, locked=True
    )
    stale = locked_run.model_copy(update={"locked": False})

    async def _stale_unlocked(retention_days: int) -> list[Run]:
        return [stale]

    monkeypatch.setattr(container.store, "get_old_unlocked_runs", _stale_unlocked)
    run_dir = tmp_path / "r-lock"
    run_dir.mkdir()
    (run_dir / "x.log").write_text("keep me")
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs/cleanup?retention_days=30")
    assert resp.status_code == 200
    assert resp.json()["cleaned_runs"] == 0
    assert run_dir.exists()  # lock respected despite the stale selection


def test_cleanup_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()

    from datetime import UTC, timedelta

    old_date = datetime.now(UTC) - timedelta(days=40)

    # 1. Old unlocked run (should be physically deleted)
    run_old = Run(
        id="run-old",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=old_date,
        finished_at=old_date,
        locked=False,
        report=ReportRef(
            allure_results_dir="run-old/results",
            allure_report_file="run-old/report.html",
            html_generated=True,
        ),
    )

    # 2. Old locked run (should NOT be physically deleted)
    run_old_locked = Run(
        id="run-old-locked",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=old_date,
        finished_at=old_date,
        locked=True,
    )

    # Create dummy artifact directories
    dir_old = tmp_path / "run-old"
    dir_old.mkdir()
    (dir_old / "stdout.log").write_text("old logs")

    dir_locked = tmp_path / "run-old-locked"
    dir_locked.mkdir()
    (dir_locked / "stdout.log").write_text("locked logs")

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save(run_old))
    loop.run_until_complete(container.store.save(run_old_locked))

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs/cleanup?retention_days=30")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["cleaned_runs"] == 1

        # Check physical existence on disk
        assert not dir_old.exists()
        assert dir_locked.exists()

        # Check that the run's report attribute was updated to None in the SQLite store
        updated_run_old = loop.run_until_complete(container.store.get("run-old"))
        assert updated_run_old.report is None

    loop.close()


def test_cleanup_runs_skips_artifact_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(artifacts_root))
    artifacts_root.mkdir()
    container = _make_container()

    old_date = datetime.now(UTC) - timedelta(days=40)
    run = Run(
        id="../outside",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=old_date,
        finished_at=old_date,
        locked=False,
        report=ReportRef(
            allure_results_dir="outside/results",
            allure_report_file="outside/report.html",
            html_generated=True,
        ),
    )
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "stdout.log").write_text("keep me")

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save(run))

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs/cleanup?retention_days=30")

    assert resp.status_code == 200
    assert resp.json()["cleaned_runs"] == 0
    assert outside_dir.exists()
    assert loop.run_until_complete(container.store.get("../outside")).report is not None
    loop.close()


def test_stream_run_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()

    run = Run(
        id="run-stream",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=NOW,
        finished_at=NOW,
    )

    # Write dummy stdout log
    run_dir = tmp_path / "run-stream"
    run_dir.mkdir()
    (run_dir / "stdout.log").write_text("line1\nline2\n", encoding="utf-8")

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save(run))
    loop.close()

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run-stream/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # TEST-2: assert the full ordered payload sequence, not mere membership.
        # The log holds "line1\nline2\n", so the stream must emit exactly those
        # two lines in order — a reorder, drop, or duplicate turns this red.
        events = [e for e in resp.text.split("\n\n") if e.startswith("data: ")]
        payloads = [e.removeprefix("data: ") for e in events]
        assert payloads == ["line1", "line2"]


def test_stream_run_logs_does_not_follow_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    _make_run_in_store(container.store, id="run_stream_symlink", status=RunStatus.COMPLETED)
    secret = tmp_path / "secret.log"
    secret.write_text("STREAMSECRET\n", encoding="utf-8")
    run_dir = tmp_path / "run_stream_symlink"
    run_dir.mkdir()
    (run_dir / "stdout.log").symlink_to(secret)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_stream_symlink/stream")

    assert resp.status_code == 200
    assert "[System] Log file not found." in resp.text
    assert "STREAMSECRET" not in resp.text


def test_stream_run_logs_does_not_read_outside_artifacts_root_for_traversal_run_id(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    artifacts_root = workspace / "artifacts"
    artifacts_root.mkdir(parents=True)
    outside_stdout = workspace / "stdout.log"
    outside_stdout.write_text("STREAMSECRET\n", encoding="utf-8")
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(
        container.store,
        id="..",
        created_by="alice",
        status=RunStatus.COMPLETED,
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.get("/runs/%2E%2E/stream")

    assert resp.status_code == 200
    assert "[System] Log file not found." in resp.text
    assert "STREAMSECRET" not in resp.text


def test_user_registration_and_login() -> None:
    container = _make_container()
    app = create_app(container)

    with TestClient(app) as client:
        # A. Register user successfully
        payload = {"username": "new_guy", "password": "secret_password", "role": "user"}
        resp = client.post("/users", json=payload)
        assert resp.status_code == 201
        assert resp.json()["username"] == "new_guy"
        assert resp.json()["role"] == "user"

        # B. Register user with already existing username
        resp_err = client.post("/users", json=payload)
        assert resp_err.status_code == 400
        assert "Username already exists" in resp_err.json()["detail"]

        # C. List users
        resp_list = client.get("/users")
        assert resp_list.status_code == 200
        usernames = [u["username"] for u in resp_list.json()["users"]]
        assert "new_guy" in usernames

        # D. Login successfully
        resp_login = client.post(
            "/auth/login", json={"username": "new_guy", "password": "secret_password"}
        )
        assert resp_login.status_code == 200
        assert "access_token" in resp_login.json()

        # E. Login with incorrect password
        resp_bad_pw = client.post(
            "/auth/login", json={"username": "new_guy", "password": "wrong_password"}
        )
        assert resp_bad_pw.status_code == 401

        # F. Login with nonexistent user
        resp_bad_user = client.post(
            "/auth/login", json={"username": "ghost", "password": "some_password"}
        )
        assert resp_bad_user.status_code == 401

        # G. Get me
        resp_me = client.get("/auth/me")
        assert resp_me.status_code == 200
        assert resp_me.json()["username"] == "test_user"


def test_create_user_missing_after_create_returns_500(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _make_container()

    async def _always_none(_username: str) -> None:
        return None

    # Existence check and the post-create re-fetch both miss: the store broke its
    # create->read invariant, so the route must 500 (ARCH-7: an `assert` here would
    # be stripped under `python -O`, then None would crash on subscripting).
    monkeypatch.setattr(container.store, "get_user", _always_none)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post(
            "/users",
            json={"username": "ghost", "password": "secret_password", "role": "user"},
        )
    assert resp.status_code == 500


# ── User management: delete / update (P1-3) ──────────────────────────────


def _seed_user(
    store: FakeStore, username: str, *, role: str = "user", password: str = "pw"
) -> None:
    from qarunner.core.auth import hash_password

    store._users[username] = {  # type: ignore[attr-defined]
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "created_at": "2026-06-20T16:00:00Z",
        "token_version": 0,
    }


def test_user_responses_never_include_password_material() -> None:
    container = _make_container()
    _seed_user(container.store, "bob", password="oldpw")
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)

    with TestClient(app) as client:
        create_resp = client.post(
            "/users",
            json={
                "username": "alice",
                "password": "create-plain-secret",
                "role": "user",
            },
        )
        list_resp = client.get("/users")
        update_resp = client.put("/users/bob", json={"password": "update-plain-secret"})

    for resp in (create_resp, list_resp, update_resp):
        assert resp.status_code in (200, 201)
        assert "password" not in resp.text
        assert "password_hash" not in resp.text
        assert "create-plain-secret" not in resp.text
        assert "update-plain-secret" not in resp.text

    assert set(create_resp.json()) == {"username", "role", "created_at"}
    assert set(update_resp.json()) == {"username", "role", "created_at"}
    assert all(
        set(user) == {"username", "role", "created_at"} for user in list_resp.json()["users"]
    )


def test_delete_user_admin_removes_target() -> None:
    container = _make_container()
    _seed_user(container.store, "bob")
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.delete("/users/bob")
    assert resp.status_code == 200
    assert "bob" not in container.store._users  # type: ignore[attr-defined]


def test_delete_user_cascades_owned_resources() -> None:
    """BUG-14: deleting a user removes every run/profile/schedule/credential/
    suite they own, so no orphaned owner references remain — and leaves
    other users' resources of the same kinds untouched."""
    container = _make_container()
    _seed_user(container.store, "bob")
    store = container.store
    _make_run_in_store(store, id="run-bob", created_by="bob")
    _make_run_in_store(store, id="run-carol", created_by="carol")
    store._profiles["profile-bob"] = TestProfile(  # type: ignore[attr-defined]
        id="profile-bob", name="p", tests_path="t/", created_by="bob", created_at=NOW
    )
    store._profiles["profile-carol"] = TestProfile(  # type: ignore[attr-defined]
        id="profile-carol", name="p", tests_path="t/", created_by="carol", created_at=NOW
    )
    store._schedules["sched-bob"] = TestSchedule(  # type: ignore[attr-defined]
        id="sched-bob",
        name="s",
        # Deliberately not profile-bob: deleting that profile cascades onto
        # any schedule bound to it (see FakeStore.delete_profile), which
        # would delete sched-bob as a side effect and mask whether
        # delete_user's own schedule-cleanup loop actually runs.
        profile_id="profile-unrelated",
        cron_expression="* * * * *",
        created_by="bob",
        created_at=NOW,
    )
    store._schedules["sched-carol"] = TestSchedule(  # type: ignore[attr-defined]
        id="sched-carol",
        name="s",
        profile_id="profile-carol",
        cron_expression="* * * * *",
        created_by="carol",
        created_at=NOW,
    )
    store._credentials["cred-bob"] = (  # type: ignore[attr-defined]
        Credential(id="cred-bob", name="c", type="https_token", created_by="bob", created_at=NOW),
        "encrypted",
    )
    store._credentials["cred-carol"] = (  # type: ignore[attr-defined]
        Credential(
            id="cred-carol", name="c", type="https_token", created_by="carol", created_at=NOW
        ),
        "encrypted",
    )
    store._suites["suite-bob"] = TestSuite(  # type: ignore[attr-defined]
        name="suite-bob", created_by="bob", created_at=NOW
    )
    store._suites["suite-carol"] = TestSuite(  # type: ignore[attr-defined]
        name="suite-carol", created_by="carol", created_at=NOW
    )
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)

    with TestClient(app) as client:
        resp = client.delete("/users/bob")

    assert resp.status_code == 200
    assert "run-bob" not in store._runs  # type: ignore[attr-defined]
    assert "profile-bob" not in store._profiles  # type: ignore[attr-defined]
    assert "sched-bob" not in store._schedules  # type: ignore[attr-defined]
    assert "cred-bob" not in store._credentials  # type: ignore[attr-defined]
    assert "suite-bob" not in store._suites  # type: ignore[attr-defined]
    assert "run-carol" in store._runs  # type: ignore[attr-defined]
    assert "profile-carol" in store._profiles  # type: ignore[attr-defined]
    assert "sched-carol" in store._schedules  # type: ignore[attr-defined]
    assert "cred-carol" in store._credentials  # type: ignore[attr-defined]
    assert "suite-carol" in store._suites  # type: ignore[attr-defined]


def test_delete_user_cannot_delete_self() -> None:
    container = _make_container()  # default test_user is admin
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.delete("/users/test_user")
    assert resp.status_code == 400
    assert "test_user" in container.store._users  # type: ignore[attr-defined]


def test_delete_user_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.delete("/users/ghost")
    assert resp.status_code == 404


def test_delete_user_requires_admin() -> None:
    container = _make_container()
    _seed_user(container.store, "bob")
    app = create_app(container)
    _override_user(app, "carol", UserRole.USER)
    # create_app mock-overrides get_current_admin to always allow; drop it so the
    # real admin guard runs against the (non-admin) overridden user.
    del app.dependency_overrides[get_current_admin]
    with TestClient(app) as client:
        resp = client.delete("/users/bob")
    assert resp.status_code == 403
    assert "bob" in container.store._users  # type: ignore[attr-defined]


def test_update_user_password_takes_effect() -> None:
    from qarunner.core.auth import verify_password

    container = _make_container()
    _seed_user(container.store, "bob", password="oldpw")
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.put("/users/bob", json={"password": "newpassword"})
    assert resp.status_code == 200
    new_hash = container.store._users["bob"]["password_hash"]  # type: ignore[attr-defined]
    assert verify_password("newpassword", new_hash)
    assert not verify_password("oldpw", new_hash)


def test_update_user_promote_to_admin() -> None:
    container = _make_container()
    _seed_user(container.store, "bob", role="user")
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.put("/users/bob", json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert container.store._users["bob"]["role"] == "admin"  # type: ignore[attr-defined]


def test_update_user_cannot_demote_last_admin() -> None:
    container = _make_container()  # test_user is the only admin
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.put("/users/test_user", json={"role": "user"})
    assert resp.status_code == 400
    assert container.store._users["test_user"]["role"] == "admin"  # type: ignore[attr-defined]


def test_update_user_cannot_demote_self_even_when_other_admin_exists() -> None:
    container = _make_container()  # test_user admin
    _seed_user(container.store, "bob", role="admin")
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.put("/users/test_user", json={"role": "user"})
    assert resp.status_code == 400
    assert container.store._users["test_user"]["role"] == "admin"  # type: ignore[attr-defined]


def test_update_user_cannot_demote_other_last_admin() -> None:
    container = _make_container()  # test_user is the only stored admin
    app = create_app(container)

    async def external_admin() -> User:
        return User(username="external_admin", role=UserRole.ADMIN, created_at=NOW)

    app.dependency_overrides[get_current_admin] = external_admin
    with TestClient(app) as client:
        resp = client.put("/users/test_user", json={"role": "user"})
    assert resp.status_code == 400
    assert container.store._users["test_user"]["role"] == "admin"  # type: ignore[attr-defined]


def test_update_user_demote_admin_when_others_exist() -> None:
    container = _make_container()  # test_user admin
    _seed_user(container.store, "bob", role="admin")
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.put("/users/bob", json={"role": "user"})
    assert resp.status_code == 200
    assert container.store._users["bob"]["role"] == "user"  # type: ignore[attr-defined]


def test_update_user_empty_payload_rejected() -> None:
    container = _make_container()
    _seed_user(container.store, "bob")
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.put("/users/bob", json={})
    assert resp.status_code == 400


def test_update_user_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.put("/users/ghost", json={"role": "admin"})
    assert resp.status_code == 404


def test_update_user_requires_admin() -> None:
    container = _make_container()
    _seed_user(container.store, "bob")
    app = create_app(container)
    _override_user(app, "carol", UserRole.USER)
    # create_app mock-overrides get_current_admin to always allow; drop it so the
    # real admin guard runs against the (non-admin) overridden user.
    del app.dependency_overrides[get_current_admin]
    with TestClient(app) as client:
        resp = client.put("/users/bob", json={"role": "admin"})
    assert resp.status_code == 403


# ── Credentials: create / list / delete (P0-1) ───────────────────────────


def _seed_credential(container: Container, *, cred_id: str, owner: str, name: str = "c") -> None:
    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(
        container.store.save_credential(
            Credential(
                id=cred_id,
                name=name,
                type="https_token",
                created_by=owner,
                created_at=NOW,
            ),
            "ENC-PLACEHOLDER",
        )
    )
    loop.close()


def test_create_credential_encrypts_secret_and_never_echoes_it() -> None:
    from qarunner.core.credentials import CredentialCipher

    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post(
            "/credentials",
            json={"name": "gh", "type": "https_token", "secret": "ghp_supersecret"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "gh"
    assert body["type"] == "https_token"
    assert body["created_by"] == "alice"
    # The plaintext secret is never returned to the caller.
    assert "secret" not in body
    assert "ghp_supersecret" not in resp.text

    cred_id = body["id"]
    enc = container.store._credentials[cred_id][1]  # type: ignore[attr-defined]
    # At rest it's ciphertext, but it decrypts back to the original.
    assert "ghp_supersecret" not in enc
    assert CredentialCipher(container.settings.secret_key).decrypt(enc) == "ghp_supersecret"


def test_create_credential_rejects_unknown_type() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post(
            "/credentials",
            json={"name": "x", "type": "ssh_key", "secret": "s"},
        )
    assert resp.status_code == 422


def test_list_credentials_is_owner_scoped_and_secretless() -> None:
    container = _make_container()
    _seed_credential(container, cred_id="a", owner="alice")
    _seed_credential(container, cred_id="b", owner="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.get("/credentials")
    assert resp.status_code == 200
    items = resp.json()["credentials"]
    assert {c["id"] for c in items} == {"a"}  # only alice's
    assert all("secret" not in c for c in items)


def test_list_credentials_admin_sees_all() -> None:
    container = _make_container()
    _seed_credential(container, cred_id="a", owner="alice")
    _seed_credential(container, cred_id="b", owner="bob")
    app = create_app(container)
    _override_user(app, "admin", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.get("/credentials")
    assert {c["id"] for c in resp.json()["credentials"]} == {"a", "b"}


def test_delete_credential_owner_removes_it() -> None:
    container = _make_container()
    _seed_credential(container, cred_id="a", owner="alice")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/credentials/a")
    assert resp.status_code == 200
    assert "a" not in container.store._credentials  # type: ignore[attr-defined]


def test_delete_credential_forbidden_for_non_owner() -> None:
    container = _make_container()
    _seed_credential(container, cred_id="a", owner="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/credentials/a")
    assert resp.status_code == 403
    assert "a" in container.store._credentials  # type: ignore[attr-defined]


def test_delete_credential_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.delete("/credentials/ghost")
    assert resp.status_code == 404


# ── SEC-5: login brute-force protection ─────────────────────────────────


def test_login_locks_out_after_repeated_failures() -> None:
    throttle = LoginThrottle(clock=FakeClock(), threshold=2, base_seconds=60.0)
    app = create_app(_make_container(login_throttle=throttle))
    with TestClient(app) as client:
        for _ in range(2):
            bad = client.post("/auth/login", json={"username": "test_user", "password": "nope"})
            assert bad.status_code == 401
        # Threshold reached — further attempts are locked out (429 + Retry-After).
        locked = client.post("/auth/login", json={"username": "test_user", "password": "nope"})
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) >= 1
        # The lock rejects even the *correct* password while it is active.
        good = client.post("/auth/login", json={"username": "test_user", "password": "test_pass"})
        assert good.status_code == 429


def test_login_failure_is_audited_without_password(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(_make_container())
    with caplog.at_level("WARNING", logger="qarunner.api.routes"), TestClient(app) as client:
        resp = client.post(
            "/auth/login", json={"username": "test_user", "password": "s3cret-leak"}
        )
        assert resp.status_code == 401
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("Failed login" in m and "test_user" in m for m in messages)
    # The plaintext password must never reach the audit log.
    assert all("s3cret-leak" not in m for m in messages)


def test_login_succeeds_after_lockout_window_expires() -> None:
    from datetime import timedelta

    clock = FakeClock()
    throttle = LoginThrottle(clock=clock, threshold=2, base_seconds=60.0)
    app = create_app(_make_container(login_throttle=throttle))
    with TestClient(app) as client:
        for _ in range(2):
            client.post("/auth/login", json={"username": "test_user", "password": "nope"})
        assert (
            client.post(
                "/auth/login", json={"username": "test_user", "password": "test_pass"}
            ).status_code
            == 429
        )
        # Advance past the lock window — the correct password is accepted again.
        clock.current = clock.current + timedelta(seconds=61)
        ok = client.post("/auth/login", json={"username": "test_user", "password": "test_pass"})
        assert ok.status_code == 200
        assert "access_token" in ok.json()


def test_logout_revokes_token_via_cookie() -> None:
    """BUG-5+13: logout bumps token_version so every other JWT for that
    user (cookie or bearer) is invalidated immediately."""
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"username": "test_user", "password": "test_pass"})
        assert login.status_code == 200
        assert client.cookies.get("token") is not None

        resp = client.post("/auth/logout")
        assert resp.status_code == 204

    assert container.store._users["test_user"]["token_version"] == 1  # type: ignore[attr-defined]


def test_logout_revokes_token_via_bearer_header() -> None:
    """Same revocation, reached via the Authorization header fallback when
    no cookie is present (e.g. a programmatic API client)."""
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"username": "test_user", "password": "test_pass"})
        token = login.json()["access_token"]
        client.cookies.clear()

        resp = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 204

    assert container.store._users["test_user"]["token_version"] == 1  # type: ignore[attr-defined]


def test_revoked_token_is_rejected_after_logout() -> None:
    """BUG-5+13: a JWT captured before logout no longer authenticates
    anything afterwards, since its embedded token_version is now stale."""
    container = _make_container()
    app = create_app(container)
    # /auth/me depends on the real get_current_user — create_app() stubs it
    # out by default for route tests that don't care about auth internals;
    # drop the stub so this test exercises the actual token_version check.
    del app.dependency_overrides[get_current_user]
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"username": "test_user", "password": "test_pass"})
        old_token = login.json()["access_token"]

        client.post("/auth/logout")
        client.cookies.clear()  # logout's delete_cookie already cleared it; be explicit

        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token has been revoked"


def test_logout_without_any_token_still_succeeds() -> None:
    """Revocation is best-effort: logout always returns 204 even when there
    is no token to revoke."""
    app = create_app(_make_container())
    with TestClient(app) as client:
        resp = client.post("/auth/logout")
    assert resp.status_code == 204


def test_logout_with_garbage_bearer_token_still_succeeds() -> None:
    """A present-but-undecodable token must not crash logout — revocation
    is best-effort, not a hard requirement (BUG-5+13's docstring)."""
    app = create_app(_make_container())
    with TestClient(app) as client:
        resp = client.post("/auth/logout", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 204


def test_client_ip_uses_trusted_proxy_x_forwarded_for() -> None:
    """BUG-8: behind a trusted reverse proxy, the login-throttle key uses the
    left-most X-Forwarded-For entry instead of the proxy's own peer address —
    so two distinct client IPs behind that proxy get independent lockouts."""
    throttle = LoginThrottle(clock=FakeClock(), threshold=2, base_seconds=60.0)
    settings = Settings(trusted_proxies="testclient")  # matches TestClient's peer host
    app = create_app(_make_container(login_throttle=throttle, settings=settings))
    with TestClient(app) as client:
        headers_a = {"X-Forwarded-For": "9.9.9.9"}
        for _ in range(2):
            bad = client.post(
                "/auth/login",
                json={"username": "test_user", "password": "nope"},
                headers=headers_a,
            )
            assert bad.status_code == 401
        locked = client.post(
            "/auth/login",
            json={"username": "test_user", "password": "nope"},
            headers=headers_a,
        )
        assert locked.status_code == 429

        # A different forwarded IP behind the same trusted proxy is a separate
        # throttle bucket — proves the key was derived from X-Forwarded-For,
        # not the shared proxy peer address.
        other_ip = client.post(
            "/auth/login",
            json={"username": "test_user", "password": "nope"},
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
        assert other_ip.status_code == 401

        # No X-Forwarded-For header at all falls back to the raw peer
        # address — yet another independent bucket from either IP above.
        no_header = client.post("/auth/login", json={"username": "test_user", "password": "nope"})
        assert no_header.status_code == 401


def test_successful_login_resets_failure_counter() -> None:
    throttle = LoginThrottle(clock=FakeClock(), threshold=2, base_seconds=60.0)
    app = create_app(_make_container(login_throttle=throttle))
    with TestClient(app) as client:
        # One miss, then a success clears the counter...
        client.post("/auth/login", json={"username": "test_user", "password": "nope"})
        assert (
            client.post(
                "/auth/login", json={"username": "test_user", "password": "test_pass"}
            ).status_code
            == 200
        )
        # ...so a subsequent single miss does not immediately lock.
        again = client.post("/auth/login", json={"username": "test_user", "password": "nope"})
        assert again.status_code == 401


def test_login_nonexistent_user_calls_verify_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """BUG-1: verify_password must be called even when user doesn't exist.

    Without this, Python short-circuits the ``or`` and skips bcrypt, leaking a
    timing signal that distinguishes "user not found" from "wrong password".
    """
    from qarunner.core.auth import verify_password as real_verify

    app = create_app(_make_container())
    call_count = 0

    def _counting_verify(password: str, hashed: str) -> bool:
        nonlocal call_count
        call_count += 1
        return real_verify(password, hashed)

    monkeypatch.setattr("qarunner.api.routes.verify_password", _counting_verify)
    with TestClient(app) as client:
        call_count = 0
        client.post("/auth/login", json={"username": "ghost", "password": "x"})
        assert call_count == 1, "verify_password must be called for nonexistent users"


def test_client_ip_helper_handles_missing_client() -> None:
    from qarunner.api.routes import _client_ip

    class _Addr:
        host = "203.0.113.7"

    class _ReqWithClient:
        client = _Addr()

    class _ReqNoClient:
        client = None

    assert _client_ip(_ReqWithClient()) == "203.0.113.7"  # type: ignore[arg-type]
    assert _client_ip(_ReqNoClient()) == "unknown"  # type: ignore[arg-type]


def test_test_tree_and_markers_detailed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set up simulated test root folder structure
    tests_dir = tmp_path / "test_suites"
    tests_dir.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))

    suite_dir = tests_dir / "suite_xyz"
    suite_dir.mkdir()

    # Create directories and files
    (suite_dir / "test_active.py").write_text(
        """
import pytest

@pytest.mark.foo
def test_one():
    pass

@pytest.mark.bar()
class TestClass:
    @pytest.mark.nested
    def test_two(self):
        pass
""",
        encoding="utf-8",
    )

    # Create empty folders and internal pycache/hidden files to verify filtering
    (suite_dir / ".hidden_folder").mkdir()
    (suite_dir / "__pycache__").mkdir()
    (suite_dir / "empty_folder").mkdir()
    (suite_dir / "empty_folder" / "unused.py").touch()

    # Create non-python and non-test python files to cover branch logic
    (suite_dir / "some_file.txt").touch()
    (suite_dir / ".hidden.py").touch()

    # Subdirectory with Python file
    sub_dir = suite_dir / "sub_folder"
    sub_dir.mkdir()
    (sub_dir / "test_sub.py").write_text(
        """
import pytest
@pytest.mark.sub_marker
def test_three():
    pass
""",
        encoding="utf-8",
    )

    # Valid subdirectory with python file and various decorators to cover AST branch branches
    valid_sub = suite_dir / "valid_sub_folder"
    valid_sub.mkdir()
    (valid_sub / "test_another.py").write_text(
        """
import pytest

def simple_decorator(f):
    return f

@simple_decorator
@foo.bar
@custom.mark.my_marker
@pytest.mark
@pytest.mark.another_marker
def test_four():
    pass
""",
        encoding="utf-8",
    )

    # Syntax error file to trigger AST exception
    (suite_dir / "test_bad.py").write_text(
        """
def parse_error_here(
""",
        encoding="utf-8",
    )

    # Mock Path.iterdir to throw an exception for sub_folder to cover except Exception in walk_dir
    original_iterdir = Path.iterdir

    def mock_iterdir(self: Path):
        if self.name == "sub_folder":
            raise OSError("Access Denied")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", mock_iterdir)

    container = _make_container()
    app = create_app(container)

    with TestClient(app) as client:
        # A. GET test tree
        resp_tree = client.get("/tests/suite_xyz/tree")
        assert resp_tree.status_code == 200
        tree = resp_tree.json()

        # Verify node structures
        names = {n["name"] for n in tree}
        assert "test_active.py" in names
        assert "valid_sub_folder" in names
        assert "sub_folder" not in names  # its iterdir raised, so it was skipped
        # Hidden and pycache and bad parsing/empty should be handled or skipped
        assert "empty_folder" not in names
        assert ".hidden_folder" not in names

        # B. GET test tree unsafe path (via safe_subpath monkeypatch)
        from qarunner.errors import UnsafePath

        def mock_unsafe_subpath(root, relative):
            raise UnsafePath("Unsafe path detected")

        monkeypatch.setattr("qarunner.core.paths.safe_subpath", mock_unsafe_subpath)

        resp_unsafe = client.get("/tests/any_suite/tree")
        assert resp_unsafe.status_code == 400

        # C. GET test tree nonexistent suite (restore safe_subpath first)
        monkeypatch.undo()  # restore the real safe_subpath for the calls below
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))
        resp_missing = client.get("/tests/non_existent/tree")
        assert resp_missing.status_code == 404

        # D. GET markers
        resp_markers = client.get("/tests/suite_xyz/markers")
        assert resp_markers.status_code == 200
        markers = resp_markers.json()
        assert "foo" in markers
        assert "bar" in markers
        assert "nested" in markers
        assert "another_marker" in markers

        # E. GET markers unsafe path
        monkeypatch.setattr("qarunner.core.paths.safe_subpath", mock_unsafe_subpath)
        resp_markers_unsafe = client.get("/tests/any_suite/markers")
        assert resp_markers_unsafe.status_code == 400

        # F. GET markers missing suite (should return [])
        monkeypatch.undo()
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))
        resp_markers_missing = client.get("/tests/non_existent/markers")
        assert resp_markers_missing.status_code == 200
        assert resp_markers_missing.json() == []


@pytest.mark.parametrize("endpoint", ["tree", "markers"])
def test_test_suite_path_errors_hide_tests_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    tests_dir = tmp_path / "test_suites"
    tests_dir.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get(f"/tests/%2E%2E/{endpoint}")

    assert resp.status_code == 400
    assert str(tests_dir) not in resp.text


@pytest.mark.parametrize("endpoint", ["tree", "markers"])
def test_test_suite_scans_forbidden_for_non_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    tests_dir = tmp_path / "test_suites"
    tests_dir.mkdir()
    suite_dir = tests_dir / "private_suite"
    suite_dir.mkdir()
    (suite_dir / "test_private.py").write_text(
        "import pytest\n\n@pytest.mark.private_marker\ndef test_private():\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))
    container = _make_container()
    _save_suite_in_store(container.store, name="private_suite", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.get(f"/tests/private_suite/{endpoint}")

    assert resp.status_code == 403
    assert "test_private.py" not in resp.text
    assert "private_marker" not in resp.text


def test_test_suite_scans_do_not_follow_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_dir = tmp_path / "test_suites"
    tests_dir.mkdir()
    suite_dir = tests_dir / "suite"
    suite_dir.mkdir()
    (suite_dir / "test_inside.py").write_text(
        "import pytest\n\n@pytest.mark.inside\ndef test_inside():\n    pass\n",
        encoding="utf-8",
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_test = outside / "test_secret.py"
    outside_test.write_text(
        "import pytest\n\n@pytest.mark.external_secret\ndef test_secret():\n    pass\n",
        encoding="utf-8",
    )
    (suite_dir / "escape_dir").symlink_to(outside, target_is_directory=True)
    (suite_dir / "test_secret_link.py").symlink_to(outside_test)
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        tree_resp = client.get("/tests/suite/tree")
        markers_resp = client.get("/tests/suite/markers")

    assert tree_resp.status_code == 200
    tree_text = str(tree_resp.json())
    assert "test_inside.py" in tree_text
    assert "escape_dir" not in tree_text
    assert "test_secret_link.py" not in tree_text
    assert "test_secret.py" not in tree_text

    assert markers_resp.status_code == 200
    assert "inside" in markers_resp.json()
    assert "external_secret" not in markers_resp.json()


def test_get_tree_includes_playwright_specs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playwright suites must expose ``*.spec.ts``/``*.test.ts`` files in the tree.

    The walker historically surfaced only pytest ``.py`` files, leaving Playwright
    suites (e.g. my-e2e-suite) with an empty tree — the UI could not browse
    or select any test. JS/TS test files must appear; non-test files (config) and
    folders without any test file stay excluded.
    """
    tests_dir = tmp_path / "external_tests"
    tests_dir.mkdir()
    suite_dir = tests_dir / "my-e2e-suite"
    suite_dir.mkdir()
    (suite_dir / "smoke.spec.ts").write_text("test('x', () => {});", encoding="utf-8")
    specs = suite_dir / "specs"
    specs.mkdir()
    (specs / "messaging.spec.ts").write_text("test('y', () => {});", encoding="utf-8")
    # Non-test source files must NOT be surfaced (filter is specific, not "all .ts").
    (suite_dir / "playwright.config.ts").write_text("export default {};", encoding="utf-8")
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/tests/my-e2e-suite/tree")

    assert resp.status_code == 200
    tree = resp.json()
    top = {n["name"] for n in tree}
    assert "smoke.spec.ts" in top
    assert "specs" in top  # folder retained because it contains a spec
    assert "playwright.config.ts" not in top  # config is not a test file
    specs_node = next(n for n in tree if n["name"] == "specs")
    assert {c["name"] for c in specs_node["children"]} == {"messaging.spec.ts"}


def test_get_markers_includes_playwright_title_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playwright suites use ``@tag`` in test titles, not ``pytest.mark`` decorators."""
    tests_dir = tmp_path / "external_tests"
    tests_dir.mkdir()
    suite_dir = tests_dir / "playwright_suite"
    suite_dir.mkdir()
    (suite_dir / "login.spec.ts").write_text(
        """
import { test, expect } from '@playwright/test';

test.describe('login @smoke', () => {
  test('admin can sign in @auth-flow @regression', async ({ page }) => {});
  test('plain title without tags', async ({ page }) => {});
});
""",
        encoding="utf-8",
    )
    (suite_dir / "test_string_only.py").write_text(
        """
def test_not_playwright_title():
    text = "test('not a Playwright title @notatag')"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/tests/playwright_suite/markers")

    assert resp.status_code == 200
    assert resp.json() == ["auth-flow", "regression", "smoke"]


def test_get_markers_does_not_follow_playwright_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_dir = tmp_path / "external_tests"
    tests_dir.mkdir()
    suite_dir = tests_dir / "playwright_suite"
    suite_dir.mkdir()
    (suite_dir / "inside.spec.ts").write_text(
        "import { test } from '@playwright/test';\n"
        "test('inside @inside', async ({ page }) => {});\n",
        encoding="utf-8",
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_spec = outside / "secret.spec.ts"
    outside_spec.write_text(
        "import { test } from '@playwright/test';\n"
        "test('secret @external-secret', async ({ page }) => {});\n",
        encoding="utf-8",
    )
    (suite_dir / "secret.spec.ts").symlink_to(outside_spec)
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/tests/playwright_suite/markers")

    assert resp.status_code == 200
    assert "inside" in resp.json()
    assert "external-secret" not in resp.json()


def test_create_profile_rejects_nonpositive_timeout() -> None:
    # P2-5: a non-positive timeout would expire immediately; reject at the edge.
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post(
            "/profiles",
            json={"name": "P", "tests_path": "tests/", "timeout": 0},
        )
    assert resp.status_code == 422


def test_create_profile_forbidden_for_other_users_registered_suite() -> None:
    container = _make_container()
    _save_suite_in_store(container.store, name="bob_suite", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.post(
            "/profiles",
            json={"name": "Bob Suite Profile", "tests_path": "bob_suite", "runner": "pytest"},
        )

    assert resp.status_code == 403
    assert "bob_suite" not in resp.text
    assert container.store._profiles == {}


def test_update_profile_forbidden_for_other_users_registered_suite() -> None:
    container = _make_container()
    container.store._profiles["profile-alice"] = TestProfile(
        id="profile-alice",
        name="Alice Profile",
        tests_path="alice_suite",
        created_by="alice",
        created_at=NOW,
    )
    _save_suite_in_store(container.store, name="bob_suite", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.put(
            "/profiles/profile-alice",
            json={"name": "Moved", "tests_path": "bob_suite", "runner": "pytest"},
        )

    assert resp.status_code == 403
    assert "bob_suite" not in resp.text
    assert container.store._profiles["profile-alice"].tests_path == "alice_suite"


def test_profile_crud_endpoints() -> None:
    container = _make_container()
    app = create_app(container)

    with TestClient(app) as client:
        # A. Create profile
        payload = {
            "name": "Integration Profile",
            "description": "Integration testing profile",
            "tests_path": "tests/unit",
            "runner": "playwright",
            "selected_files": ["test_routes.py"],
            "selected_markers": ["unit"],
            "extra_args": "-vv",
            "executor_mode": "subprocess",
            "timeout": 120,
            "env": {"API_BASE_URL": "https://api.example", "FEATURE_FLAG": "on"},
        }
        resp = client.post("/profiles", json=payload)
        assert resp.status_code == 201
        profile_id = resp.json()["id"]
        assert resp.json()["name"] == "Integration Profile"
        assert resp.json()["runner"] == "playwright"
        # TEST-2: env must survive the create round-trip verbatim (request schema
        # → ProfileService.create → store → profile_to_response). A regression
        # dropping env anywhere in that chain turns this red.
        assert resp.json()["env"] == {"API_BASE_URL": "https://api.example", "FEATURE_FLAG": "on"}

        # B. List profiles (no filter)
        resp_list = client.get("/profiles")
        assert resp_list.status_code == 200
        assert len(resp_list.json()) == 1

        # C. List profiles (with matching tests_path)
        resp_filtered_match = client.get("/profiles?tests_path=tests/unit")
        assert resp_filtered_match.status_code == 200
        assert len(resp_filtered_match.json()) == 1

        # D. List profiles (with mismatching tests_path)
        resp_filtered_mismatch = client.get("/profiles?tests_path=tests/integration")
        assert resp_filtered_mismatch.status_code == 200
        assert len(resp_filtered_mismatch.json()) == 0

        # E. Update profile
        update_payload = {
            "name": "Updated Profile",
            "description": "Updated desc",
            "tests_path": "tests/unit",
            "runner": "pytest",
            "selected_files": ["test_routes.py"],
            "selected_markers": ["unit"],
            "extra_args": "-v",
            "executor_mode": "docker",
            "timeout": 300,
            "env": {"API_BASE_URL": "https://api.updated"},
        }
        resp_update = client.put(f"/profiles/{profile_id}", json=update_payload)
        assert resp_update.status_code == 200
        assert resp_update.json()["name"] == "Updated Profile"
        assert resp_update.json()["runner"] == "pytest"
        assert resp_update.json()["executor_mode"] == "docker"
        # TEST-2: update replaces env wholesale — the new map is returned and the
        # create-time keys (FEATURE_FLAG) are gone. Catches an update path that
        # ignores req.env or merges instead of replacing.
        assert resp_update.json()["env"] == {"API_BASE_URL": "https://api.updated"}

        # F. Update nonexistent profile
        resp_update_err = client.put("/profiles/ghost-profile-id", json=update_payload)
        assert resp_update_err.status_code == 404

        # G. Delete profile
        resp_delete = client.delete(f"/profiles/{profile_id}")
        assert resp_delete.status_code == 200
        assert resp_delete.json()["status"] == "success"

        # H. Delete nonexistent profile
        resp_delete_err = client.delete("/profiles/ghost-profile-id")
        assert resp_delete_err.status_code == 404


def test_get_run_detailed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()

    # Create runs and test file locations
    _make_run_in_store(container.store, id="run_standard")
    _make_run_in_store(container.store, id="run_fallback")
    _make_run_in_store(container.store, id="run_exception")

    # 1. Standard stdout/stderr paths
    standard_dir = tmp_path / "run_standard"
    standard_dir.mkdir()
    (standard_dir / "stdout.log").write_text("standard stdout", encoding="utf-8")
    (standard_dir / "stderr.log").write_text("standard stderr", encoding="utf-8")

    # 2. Fallback stdout/stderr paths inside ./artifacts/run_fallback/
    fallback_dir = Path("./artifacts") / "run_fallback"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    try:
        (fallback_dir / "stdout.log").write_text("fallback stdout", encoding="utf-8")
        (fallback_dir / "stderr.log").write_text("fallback stderr", encoding="utf-8")

        app = create_app(container)
        with TestClient(app) as client:
            # Test standard run logs retrieval
            resp_std = client.get("/runs/run_standard")
            assert resp_std.status_code == 200
            assert resp_std.json()["stdout"] == "standard stdout"
            assert resp_std.json()["stderr"] == "standard stderr"

            # Test fallback run logs retrieval
            resp_fall = client.get("/runs/run_fallback")
            assert resp_fall.status_code == 200
            assert resp_fall.json()["stdout"] == "fallback stdout"
            assert resp_fall.json()["stderr"] == "fallback stderr"
    finally:
        import shutil

        shutil.rmtree("./artifacts", ignore_errors=True)

    # 3. Log reading that fails returns None instead of propagating. The log
    # paths are directories where files are expected, so the bounded reader
    # hits OSError on open().
    exception_dir = tmp_path / "run_exception"
    exception_dir.mkdir()
    (exception_dir / "stdout.log").mkdir()
    (exception_dir / "stderr.log").mkdir()

    with TestClient(app) as client:
        resp_exc = client.get("/runs/run_exception")
        assert resp_exc.status_code == 200
        assert resp_exc.json()["stdout"] is None
        assert resp_exc.json()["stderr"] is None


def test_get_run_truncates_large_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-8: get_run returns only the bounded, marked tail of a large log."""
    from qarunner.api import routes

    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    monkeypatch.setattr(routes, "_RUN_LOG_MAX_BYTES", 20)
    _make_run_in_store(container.store, id="run_big")
    big_dir = tmp_path / "run_big"
    big_dir.mkdir()
    (big_dir / "stdout.log").write_text("HEAD" + "x" * 1000 + "TAIL", encoding="utf-8")
    (big_dir / "stderr.log").write_text("small", encoding="utf-8")
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_big")
        assert resp.status_code == 200
        body = resp.json()
        assert "truncated" in body["stdout"]
        assert "TAIL" in body["stdout"]
        assert "HEAD" not in body["stdout"]
        assert body["stderr"] == "small"  # small log: unchanged, no marker


def test_get_run_does_not_follow_log_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    container = _make_container()
    _make_run_in_store(container.store, id="run_symlink")
    secret = tmp_path / "secret.log"
    secret.write_text("TOPSECRET", encoding="utf-8")
    run_dir = tmp_path / "artifacts" / "run_symlink"
    run_dir.mkdir(parents=True)
    (run_dir / "stdout.log").symlink_to(secret)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_symlink")

    assert resp.status_code == 200
    assert resp.json()["stdout"] is None
    assert "TOPSECRET" not in resp.text


def test_get_run_does_not_follow_log_dir_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(artifacts_root))
    container = _make_container()
    _make_run_in_store(container.store, id="run_dir_symlink")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "stdout.log").write_text("DIRSECRET", encoding="utf-8")
    artifacts_root.mkdir()
    (artifacts_root / "run_dir_symlink").symlink_to(outside_dir, target_is_directory=True)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_dir_symlink")

    assert resp.status_code == 200
    assert resp.json()["stdout"] is None
    assert "DIRSECRET" not in resp.text


def test_get_run_does_not_read_logs_outside_artifacts_root_for_traversal_run_id(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    artifacts_root = workspace / "artifacts"
    artifacts_root.mkdir(parents=True)
    outside_stdout = workspace / "stdout.log"
    outside_stdout.write_text("PARENTSECRET", encoding="utf-8")
    container = _make_container(settings=Settings(artifacts_root=str(artifacts_root)))
    _make_run_in_store(container.store, id="..", created_by="alice")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.get("/runs/%2E%2E")

    assert resp.status_code == 200
    assert resp.json()["stdout"] is None
    assert "PARENTSECRET" not in resp.text


def test_get_run_unreadable_log_returns_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(artifacts_root))
    container = _make_container()
    _make_run_in_store(container.store, id="run_unreadable")
    run_dir = artifacts_root / "run_unreadable"
    run_dir.mkdir(parents=True)
    stdout_file = run_dir / "stdout.log"
    stdout_file.write_text("SHOULD_NOT_LEAK", encoding="utf-8")

    import builtins

    original_open = builtins.open

    def _raise_on_stdout(path: object, *args: object, **kwargs: object):
        if Path(path) == stdout_file:
            raise OSError("read failed")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raise_on_stdout)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_unreadable")

    assert resp.status_code == 200
    assert resp.json()["stdout"] is None
    assert "SHOULD_NOT_LEAK" not in resp.text


def test_get_run_log_resolve_error_returns_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(artifacts_root))
    container = _make_container()
    _make_run_in_store(container.store, id="run_resolve_error")
    run_dir = artifacts_root / "run_resolve_error"
    run_dir.mkdir(parents=True)
    stdout_file = run_dir / "stdout.log"
    stdout_file.write_text("SHOULD_NOT_LEAK", encoding="utf-8")

    original_resolve = Path.resolve

    def _raise_on_stdout_resolve(self: Path, *args: object, **kwargs: object):
        if self == stdout_file:
            raise OSError("resolve failed")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _raise_on_stdout_resolve)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_resolve_error")

    assert resp.status_code == 200
    assert resp.json()["stdout"] is None
    assert "SHOULD_NOT_LEAK" not in resp.text


def test_stream_run_logs_missing_and_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()

    # Mock asyncio.sleep to return immediately so tests finish instantly
    import asyncio

    async def mock_sleep(delay):
        return

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    # A. Stream missing run from HTTP level (before starting generator)
    app = create_app(container)
    with TestClient(app) as client:
        resp_missing = client.get("/runs/missing_run_id/stream")
        assert resp_missing.status_code == 404

    # B. Event generator: file doesn't exist and run is already completed/failed/timeout
    # So it should return "Log file not found" immediately.
    _make_run_in_store(container.store, id="run_no_log", status=RunStatus.COMPLETED)
    with TestClient(app) as client:
        resp = client.get("/runs/run_no_log/stream")
        assert resp.status_code == 200
        assert "[System] Log file not found." in resp.text

    # C. File exists, read to end, then status flips to completed and the rest is flushed
    _make_run_in_store(container.store, id="run_flush", status=RunStatus.RUNNING)
    run_dir = tmp_path / "run_flush"
    run_dir.mkdir()
    log_file = run_dir / "stdout.log"
    log_file.write_text("line1\n", encoding="utf-8")

    # Walk the run through an explicit status timeline rather than branching on a
    # magic call number (TEST-2: drop call_count coupling). The route's access
    # check and the first follow-loop poll see RUNNING — the latter exercises the
    # still-running wait branch — then the next poll reports completion with a
    # final line appended. Overflowing the iterator stays COMPLETED, so the test
    # no longer breaks if the route polls store.get a different number of times.
    statuses = iter([RunStatus.RUNNING, RunStatus.RUNNING, RunStatus.COMPLETED])
    original_get = container.store.get

    async def mock_get(run_id: str):
        run_obj = await original_get(run_id)
        if run_id != "run_flush":
            return run_obj
        status = next(statuses, RunStatus.COMPLETED)
        if status is RunStatus.COMPLETED:
            log_file.write_text("line1\nline_final\n", encoding="utf-8")
        return run_obj.model_copy(update={"status": status})

    monkeypatch.setattr(container.store, "get", mock_get)

    with TestClient(app) as client:
        resp = client.get("/runs/run_flush/stream")
        assert resp.status_code == 200
        assert "data: line1" in resp.text
        assert "data: line_final" in resp.text

    # D. Event generator: exception during store.get inside the loop
    _make_run_in_store(container.store, id="run_loop_exc", status=RunStatus.RUNNING)
    loop_exc_dir = tmp_path / "run_loop_exc"
    loop_exc_dir.mkdir()
    (loop_exc_dir / "stdout.log").write_text("line1\n", encoding="utf-8")

    call_get_count = 0

    async def mock_get_exc(run_id: str):
        nonlocal call_get_count
        if run_id == "run_loop_exc":
            call_get_count += 1
            if call_get_count > 1:
                raise ValueError("Store failure")
        return await original_get(run_id)

    monkeypatch.setattr(container.store, "get", mock_get_exc)

    with TestClient(app) as client:
        resp = client.get("/runs/run_loop_exc/stream")
        assert resp.status_code == 200
        # Should stream the first line and then terminate gracefully upon exception
        assert "data: line1" in resp.text

    # E. File missing, run RUNNING: the wait loop runs all 50 iterations and exits
    _make_run_in_store(container.store, id="run_timeout_loop", status=RunStatus.RUNNING)
    with TestClient(app) as client:
        resp = client.get("/runs/run_timeout_loop/stream")
        assert resp.status_code == 200
        assert "[System] Log file not found." in resp.text

    # F. Event generator: RunNotFound exception during store.get inside the initial wait loop
    _make_run_in_store(container.store, id="run_not_found_loop", status=RunStatus.RUNNING)
    original_get_fn = container.store.get
    get_count = 0

    async def mock_get_not_found(run_id: str):
        nonlocal get_count
        if run_id == "run_not_found_loop":
            get_count += 1
            if get_count > 1:
                raise RunNotFound(run_id)
        return await original_get_fn(run_id)

    monkeypatch.setattr(container.store, "get", mock_get_not_found)

    with TestClient(app) as client:
        resp = client.get("/runs/run_not_found_loop/stream")
        assert resp.status_code == 200
        assert "[System] Log file not found." in resp.text


def test_stream_disconnect_initial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CONC-1: a client disconnect during the initial wait aborts the generator."""
    from unittest.mock import AsyncMock

    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    _make_run_in_store(container.store, id="run_disc_init", status=RunStatus.RUNNING)
    # No log file exists; without the disconnect check the initial loop would spin.
    monkeypatch.setattr("starlette.requests.Request.is_disconnected", AsyncMock(return_value=True))
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_disc_init/stream")
        assert resp.status_code == 200
        # Generator returned immediately; the "not found" message was never reached.
        assert "[System] Log file not found." not in resp.text


def test_stream_disconnect_midstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CONC-1: a disconnect during follow breaks the read loop (no orphaned handle)."""
    from unittest.mock import AsyncMock

    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    _make_run_in_store(container.store, id="run_disc_mid", status=RunStatus.RUNNING)
    run_dir = tmp_path / "run_disc_mid"
    run_dir.mkdir()
    (run_dir / "stdout.log").write_text("line1\nline2\n", encoding="utf-8")
    # First check (initial loop) passes; second (read loop) reports a disconnect.
    monkeypatch.setattr(
        "starlette.requests.Request.is_disconnected",
        AsyncMock(side_effect=[False, True]),
    )
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_disc_mid/stream")
        assert resp.status_code == 200
        # Broke out before emitting any log line.
        assert "data: line1" not in resp.text


def test_stream_max_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CONC-1: the follow loop stops once the max-duration cap is exceeded."""
    from qarunner.api import routes

    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    monkeypatch.setattr(routes, "_SSE_MAX_FOLLOW_SECONDS", -1.0)
    _make_run_in_store(container.store, id="run_maxdur", status=RunStatus.RUNNING)
    run_dir = tmp_path / "run_maxdur"
    run_dir.mkdir()
    (run_dir / "stdout.log").write_text("line1\n", encoding="utf-8")
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_maxdur/stream")
        assert resp.status_code == 200
        assert "max duration reached" in resp.text


def test_stream_tail_truncation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CONC-1: the final flush is byte-bounded so a huge tail isn't read at once."""
    import asyncio

    from qarunner.api import routes

    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()
    monkeypatch.setattr(routes, "_SSE_MAX_TAIL_BYTES", 4)

    async def _instant(delay):
        return

    monkeypatch.setattr(asyncio, "sleep", _instant)
    _make_run_in_store(container.store, id="run_tail", status=RunStatus.RUNNING)
    run_dir = tmp_path / "run_tail"
    run_dir.mkdir()
    log_file = run_dir / "stdout.log"
    log_file.write_text("seed\n", encoding="utf-8")

    # Status timeline instead of a magic call number (TEST-2): the access check
    # sees RUNNING, the first follow-loop poll goes terminal and appends a long
    # tail so the byte-bounded final flush reads only its first 4 bytes.
    statuses = iter([RunStatus.RUNNING, RunStatus.COMPLETED])
    original_get = container.store.get

    async def mock_get(run_id: str):
        run_obj = await original_get(run_id)
        if run_id != "run_tail":
            return run_obj
        status = next(statuses, RunStatus.COMPLETED)
        if status is RunStatus.COMPLETED:
            log_file.write_text("seed\nABCDEFGHIJKLMNOP\n", encoding="utf-8")
        return run_obj.model_copy(update={"status": status})

    monkeypatch.setattr(container.store, "get", mock_get)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_tail/stream")
        assert resp.status_code == 200
        assert "data: seed" in resp.text
        assert "ABCD" in resp.text  # only the first 4 bytes of the tail
        assert "EFGH" not in resp.text


def test_lock_run_nonexistent() -> None:
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.put("/runs/ghost_run/lock", json={"locked": True})
        assert resp.status_code == 404


def test_cleanup_runs_rmtree_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    container = _make_container()

    from datetime import UTC, timedelta

    old_date = datetime.now(UTC) - timedelta(days=40)

    # 1. Old unlocked run that we'll attempt to delete, which raises exception
    run_old = Run(
        id="run-old-exc",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=old_date,
        finished_at=old_date,
        locked=False,
    )

    # 2. Old unlocked run whose directory does NOT exist on disk (covers 505->503 branch!)
    run_no_dir = Run(
        id="run-no-dir-on-disk",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=old_date,
        finished_at=old_date,
        locked=False,
    )

    # Create directory on disk ONLY for the first run
    dir_old = tmp_path / "run-old-exc"
    dir_old.mkdir()
    (dir_old / "stdout.log").write_text("logs")

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save(run_old))
    loop.run_until_complete(container.store.save(run_no_dir))
    loop.close()

    # Mock shutil.rmtree to raise an exception
    import shutil

    def mock_rmtree(path, *args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(shutil, "rmtree", mock_rmtree)

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs/cleanup?retention_days=30")
        assert resp.status_code == 200
        # Exception is caught and ignored, cleaned count should be 0 because it failed
        assert resp.json()["cleaned_runs"] == 0
        assert dir_old.exists()


def test_preview_schedule_detailed() -> None:
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        # A. Invalid timezone
        resp_tz = client.get("/schedules/preview?expression=*/5 * * * *&timezone=Invalid/TZ")
        assert resp_tz.status_code == 400
        assert "Invalid timezone" in resp_tz.json()["detail"]

        # B. Invalid cron syntax (raises ValueError/Exception in parser)
        resp_cron = client.get("/schedules/preview?expression=five_minutes&timezone=UTC")
        assert resp_cron.status_code == 400
        assert "Invalid cron expression" in resp_cron.json()["detail"]


def test_preview_schedule_applies_dst_timezone() -> None:
    """TEST-2: the requested timezone is actually applied, not silently UTC.

    America/New_York is a DST-observing zone, so its fire times must carry the
    DST-adjusted offset (EDT -4h or EST -5h) and never +00:00. Asserting the
    offset is the deterministic way to catch a "timezone ignored" regression:
    the base time is ``datetime.now()`` so no preview ever actually crosses a
    DST boundary, but the offset proves the zone reached croniter.
    """
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/schedules/preview?expression=*/5 * * * *&timezone=America/New_York")
        assert resp.status_code == 200
        times = [datetime.fromisoformat(t) for t in resp.json()["next_runs"]]
        assert len(times) == 5
        for t in times:
            offset = t.utcoffset()
            assert offset is not None  # timezone-aware
            assert offset in (timedelta(hours=-4), timedelta(hours=-5))  # NY, not UTC
        # Absolute ordering holds year-round even across a transition; exact
        # spacing is asserted in the UTC case to stay clear of the fall-back hour.
        for earlier, later in pairwise(times):
            assert later > earlier


# ── POST /schedules/{id}/trigger (P2-6) ──────────────────────────────────


def _seed_schedule_with_profile(
    container: Container,
    *,
    schedule_owner: str = "test_user",
    profile_owner: str | None = None,
    profile_id: str | None = "profile-x",
    schedule_id: str = "sched-x",
) -> None:
    import asyncio

    loop = asyncio.new_event_loop()
    if profile_id is not None:
        loop.run_until_complete(
            container.store.save_profile(
                TestProfile(
                    id=profile_id,
                    name="P",
                    tests_path="tests/",
                    created_by=profile_owner or schedule_owner,
                    created_at=NOW,
                )
            )
        )
    loop.run_until_complete(
        container.store.save_schedule(
            TestSchedule(
                id=schedule_id,
                name="S",
                profile_id=profile_id or "ghost-profile",
                cron_expression="0 2 * * *",
                created_by=schedule_owner,
                created_at=NOW,
            )
        )
    )
    loop.close()


def test_trigger_schedule_owner_creates_run() -> None:
    container = _make_container()
    _seed_schedule_with_profile(container)
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post("/schedules/sched-x/trigger")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    # The run is owned by the user who triggered it, not "system:schedule".
    assert body["created_by"] == "test_user"
    assert any(
        r.created_by == "test_user"
        for r in container.store._runs.values()  # type: ignore[attr-defined]
    )


def test_trigger_schedule_forbidden_for_non_owner() -> None:
    container = _make_container()
    _seed_schedule_with_profile(container, schedule_owner="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/schedules/sched-x/trigger")
    assert resp.status_code == 403


def test_trigger_schedule_forbidden_when_bound_profile_has_different_owner() -> None:
    container = _make_container()
    _seed_schedule_with_profile(container, schedule_owner="alice", profile_owner="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/schedules/sched-x/trigger")
    assert resp.status_code == 403
    assert "profile-x" not in resp.text
    assert container.store._runs == {}


def test_trigger_schedule_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post("/schedules/ghost/trigger")
    assert resp.status_code == 404


def test_trigger_schedule_missing_profile_409() -> None:
    container = _make_container()
    _seed_schedule_with_profile(container, profile_id=None)
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post("/schedules/sched-x/trigger")
    assert resp.status_code == 409


# ── POST /profiles/{id}/trigger ─────────────────────────────────────────


def _seed_trigger_profile(
    container: Container,
    *,
    profile_owner: str = "test_user",
    profile_id: str = "profile-x",
) -> None:
    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(
        container.store.save_profile(
            TestProfile(
                id=profile_id,
                name="Daily Playwright",
                tests_path="suite/",
                runner="playwright",
                selected_files=["specs/smoke.spec.ts"],
                selected_markers=["smoke"],
                extra_args="--headed",
                executor_mode="docker",
                timeout=120,
                created_by=profile_owner,
                created_at=NOW,
                env={"BASE_URL": "http://app"},
            )
        )
    )
    loop.close()


def test_trigger_profile_creates_profile_bound_run_from_profile() -> None:
    container = _make_container()
    _seed_trigger_profile(container)
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post("/profiles/profile-x/trigger")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["runner"] == "playwright"
    assert body["profile_id"] == "profile-x"

    orch = container.orchestrator  # type: ignore[attr-defined]
    assert orch.last_profile_id == "profile-x"
    assert orch.last_req is not None
    assert orch.last_req.tests_path == "suite/"
    assert orch.last_req.runner == "playwright"
    assert orch.last_req.selected_files == ["specs/smoke.spec.ts"]
    assert orch.last_req.selected_markers == ["smoke"]
    assert orch.last_req.extra_args == "--headed"
    assert orch.last_req.timeout == 120
    assert orch.last_req.env == {"BASE_URL": "http://app"}


def test_trigger_profile_with_unregistered_tests_path_skips_suite_check() -> None:
    """A profile whose tests_path doesn't resolve to a registered suite name
    (e.g. a bare "." — legal input, just not a suite reference) has nothing
    for the owner-scope suite check to look up, so it's skipped rather than
    treated as a 403 or 404."""
    container = _make_container()
    _seed_trigger_profile(container)
    profile = container.store._profiles["profile-x"]  # type: ignore[attr-defined]
    container.store._profiles["profile-x"] = profile.model_copy(  # type: ignore[attr-defined]
        update={"tests_path": "."}
    )
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post("/profiles/profile-x/trigger")
    assert resp.status_code == 202


def test_trigger_profile_forbidden_for_non_owner() -> None:
    container = _make_container()
    _seed_trigger_profile(container, profile_owner="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/profiles/profile-x/trigger")
    assert resp.status_code == 403


def test_trigger_profile_nonexistent_404() -> None:
    container = _make_container()
    app = create_app(container)
    _override_user(app, "test_user", UserRole.ADMIN)
    with TestClient(app) as client:
        resp = client.post("/profiles/ghost/trigger")
    assert resp.status_code == 404


# Run creation guards (subprocess gate + in-flight cap) must apply on every path
# that reaches the orchestrator, not just POST /runs — else /rerun and /trigger
# become bypasses.


def test_rerun_respects_inflight_cap() -> None:
    container = _make_container()
    container.settings.max_inflight_runs_per_user = 1
    # docker so the subprocess gate (checked first) doesn't mask the cap.
    _make_run_in_store(
        container.store,
        id="orig",
        status=RunStatus.COMPLETED,
        created_by="normal_user",
        executor_mode="docker",
    )
    _make_run_in_store(
        container.store, id="if-1", status=RunStatus.RUNNING, created_by="normal_user"
    )
    app = create_app(container)
    _override_user(app, "normal_user", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/runs/orig/rerun")
    assert resp.status_code == 429


def test_trigger_schedule_respects_inflight_cap() -> None:
    container = _make_container()
    container.settings.max_inflight_runs_per_user = 1
    _seed_schedule_with_profile(container, schedule_owner="normal_user")
    _make_run_in_store(
        container.store, id="if-1", status=RunStatus.RUNNING, created_by="normal_user"
    )
    app = create_app(container)
    _override_user(app, "normal_user", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/schedules/sched-x/trigger")
    assert resp.status_code == 429


def test_trigger_profile_respects_inflight_cap() -> None:
    container = _make_container()
    container.settings.max_inflight_runs_per_user = 1
    _seed_trigger_profile(container, profile_owner="normal_user")
    _make_run_in_store(
        container.store, id="if-1", status=RunStatus.RUNNING, created_by="normal_user"
    )
    app = create_app(container)
    _override_user(app, "normal_user", UserRole.USER)
    with TestClient(app) as client:
        resp = client.post("/profiles/profile-x/trigger")
    assert resp.status_code == 429


def test_schedule_exceptions_and_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _make_container()

    # Save a valid profile for validation checks
    profile = TestProfile(
        id="profile-valid",
        name="Valid Profile",
        tests_path="tests/",
        created_by="test_user",
        created_at=NOW,
    )
    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save_profile(profile))
    loop.close()

    # Mock croniter preview calculation to throw exception on specific cron expression "0 9 * * *"
    from croniter import croniter

    original_init = croniter.__init__

    def mock_init(self, expr, *args, **kwargs):
        self._is_mock_exc = expr == "0 9 * * *"
        original_init(self, expr, *args, **kwargs)

    original_get_next = croniter.get_next

    def mock_get_next(self, *args, **kwargs):
        if getattr(self, "_is_mock_exc", False):
            raise ValueError("Simulated error")
        return original_get_next(self, *args, **kwargs)

    monkeypatch.setattr(croniter, "__init__", mock_init)
    monkeypatch.setattr(croniter, "get_next", mock_get_next)

    app = create_app(container)
    with TestClient(app) as client:
        # 1. Create schedule - missing profile (400)
        payload = {
            "name": "Sched 1",
            "profile_id": "profile-ghost",
            "cron_expression": "0 2 * * *",
            "enabled": True,
            "timezone": "UTC",
        }
        resp = client.post("/schedules", json=payload)
        assert resp.status_code == 400
        assert "Profile profile-ghost not found" in resp.json()["detail"]

        # 2. Create schedule - invalid timezone (400)
        payload["profile_id"] = "profile-valid"
        payload["timezone"] = "Invalid/TZ"
        resp = client.post("/schedules", json=payload)
        assert resp.status_code == 400
        assert "Invalid timezone" in resp.json()["detail"]

        # 3. Create schedule - invalid cron expression (400)
        payload["timezone"] = "UTC"
        payload["cron_expression"] = "invalid_cron"
        resp = client.post("/schedules", json=payload)
        assert resp.status_code == 400
        assert "Invalid cron expression" in resp.json()["detail"]

        # 4. Create disabled schedule (tests disabled flow where next_run_at is None)
        payload["cron_expression"] = "0 2 * * *"
        payload["enabled"] = False
        resp = client.post("/schedules", json=payload)
        assert resp.status_code == 201
        assert resp.json()["next_run_at"] is None

        # 4b. Create schedule, exception on next_run_at calc (covers 582-583)
        payload["cron_expression"] = "0 9 * * *"
        payload["enabled"] = True
        resp_preview_exc = client.post("/schedules", json=payload)
        assert resp_preview_exc.status_code == 201
        assert resp_preview_exc.json()["next_run_at"] is None

        # Create a valid active schedule to test PUT routes on
        payload["cron_expression"] = "0 2 * * *"
        payload["enabled"] = True
        resp_active = client.post("/schedules", json=payload)
        assert resp_active.status_code == 201
        sched_id = resp_active.json()["id"]

        # 5. Update nonexistent schedule (404)
        update_payload = {
            "name": "Sched Updated",
            "profile_id": "profile-valid",
            "cron_expression": "0 3 * * *",
            "enabled": True,
            "timezone": "UTC",
        }
        resp = client.put("/schedules/ghost-sched-id", json=update_payload)
        assert resp.status_code == 404

        # 6. Update schedule - nonexistent profile (400)
        update_payload["profile_id"] = "profile-ghost"
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 400

        # 7. Update schedule - invalid timezone (400)
        update_payload["profile_id"] = "profile-valid"
        update_payload["timezone"] = "Invalid/TZ"
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 400

        # 8. Update schedule - invalid cron (400)
        update_payload["timezone"] = "UTC"
        update_payload["cron_expression"] = "invalid_cron"
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 400

        # 9. Update schedule - set enabled=False (removes from apscheduler)
        update_payload["cron_expression"] = "0 3 * * *"
        update_payload["enabled"] = False
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # 9b. Update schedule - set enabled=True (calls container.scheduler.upsert)
        update_payload["enabled"] = True
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # 9c. Update schedule, exception on next_run_at calc (covers 668-672)
        update_payload["cron_expression"] = "0 9 * * *"
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 200
        assert resp.json()["next_run_at"] is None

        # 10. Delete nonexistent schedule (404)
        resp = client.delete("/schedules/ghost-sched-id")
        assert resp.status_code == 404


def test_link_test_suite_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))

    # Path to link
    local_project = tmp_path / "my-local-project"
    local_project.mkdir()

    container = _make_container()
    app = create_app(container)

    with TestClient(app) as client:
        # 1. Success case
        resp = client.post("/tests/link", json={"path": str(local_project)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["suite_name"] == "my-local-project"
        assert body["is_accessible"] is True
        assert "Successfully linked" in body["message"]

        # Check if symlink exists
        symlink_path = tests_root / "my-local-project"
        assert symlink_path.is_symlink()
        assert symlink_path.resolve() == local_project.resolve()

        # 2. Link again (tests overwrite/clear of existing symlink)
        resp2 = client.post("/tests/link", json={"path": str(local_project)})
        assert resp2.status_code == 200

        # 3. Link non-existent directory (tests is_accessible=False branch)
        fake_project = tmp_path / "non-existent-project"
        resp3 = client.post("/tests/link", json={"path": str(fake_project)})
        assert resp3.status_code == 200
        body3 = resp3.json()
        assert body3["success"] is True
        assert body3["is_accessible"] is False
        assert "is not accessible" in body3["message"]

        # 4. Invalid path empty suite_name error (e.g., path is root "/")
        resp_invalid = client.post("/tests/link", json={"path": "/"})
        assert resp_invalid.status_code == 400
        assert "unable to extract directory name" in resp_invalid.json()["detail"]

        # 5. tests_root.mkdir exception
        # Make is_dir return False for tests_root, and mock mkdir to raise exception
        monkeypatch.setattr(Path, "is_dir", lambda self: self != tests_root)

        def mock_mkdir_err(*args, **kwargs):
            raise OSError("mkdir failed")

        monkeypatch.setattr(Path, "mkdir", mock_mkdir_err)
        resp_mkdir = client.post("/tests/link", json={"path": str(local_project)})
        assert resp_mkdir.status_code == 500
        assert "Failed to create tests_root" in resp_mkdir.json()["detail"]
        monkeypatch.undo()
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))

        # 6. Failed to clear existing (unlink error)
        def mock_unlink_err(*args, **kwargs):
            raise OSError("unlink failed")

        monkeypatch.setattr(Path, "unlink", mock_unlink_err)
        # Force is_symlink to return True for my-local-project to trigger unlink
        original_is_symlink = Path.is_symlink

        def fake_is_symlink(self):
            return self.name == "my-local-project" or original_is_symlink(self)

        monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
        resp_unlink = client.post("/tests/link", json={"path": str(local_project)})
        assert resp_unlink.status_code == 500
        assert "Failed to clear existing" in resp_unlink.json()["detail"]
        monkeypatch.undo()
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))

        # 7. Failed to clear existing (rmtree error for existing directory)
        # Create a real folder under tests_root where symlink should go
        fake_dir = tests_root / "fake_dir"
        fake_dir.mkdir(exist_ok=True)

        def mock_rmtree_err(*args, **kwargs):
            raise OSError("rmtree failed")

        monkeypatch.setattr("shutil.rmtree", mock_rmtree_err)
        resp_rmtree = client.post("/tests/link", json={"path": "/some/path/fake_dir"})
        assert resp_rmtree.status_code == 500
        assert "Failed to clear existing" in resp_rmtree.json()["detail"]
        monkeypatch.undo()
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))

        # 8. Failed to create symlink (os.symlink error)
        def mock_symlink_err(*args, **kwargs):
            raise OSError("symlink failed")

        monkeypatch.setattr("os.symlink", mock_symlink_err)
        resp_symlink = client.post("/tests/link", json={"path": str(local_project)})
        assert resp_symlink.status_code == 500
        assert "Failed to create symlink" in resp_symlink.json()["detail"]
        monkeypatch.undo()
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))

        # 9. Successful rmtree clear branch (when existing entry is a real directory)
        real_dir = tests_root / "real-dir-to-link"
        real_dir.mkdir(exist_ok=True)
        target_dir = tmp_path / "real-dir-to-link"
        target_dir.mkdir(exist_ok=True)
        resp_dir_ok = client.post("/tests/link", json={"path": str(target_dir)})
        assert resp_dir_ok.status_code == 200
        assert (tests_root / "real-dir-to-link").is_symlink()


def test_link_writes_local_suite_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Linking a local dir must register a ``local`` suite owned by the caller."""
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    local = tmp_path / "proj"
    local.mkdir()

    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/tests/link", json={"path": str(local)})

    assert resp.status_code == 200
    suite = container.store._suites["proj"]  # type: ignore[attr-defined]
    assert suite.source == "local"
    assert suite.created_by == "test_user"
    assert suite.repo_url is None
    assert suite.ref is None


def test_link_forbidden_non_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-owner must not overwrite/hijack another user's suite via /tests/link.

    Without an owner check this bypasses delete_test_suite's guard: it would
    rmtree the victim's directory and re-point the record at the attacker.
    """
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", created_by="bob")
    victim_dir = tests_root / "repo"
    victim_dir.mkdir()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    attacker_target = tmp_path / "evil" / "repo"
    attacker_target.mkdir(parents=True)

    with TestClient(app) as client:
        resp = client.post("/tests/link", json={"path": str(attacker_target)})

    assert resp.status_code == 403
    assert victim_dir.exists()  # victim's suite not deleted
    assert container.store._suites["repo"].created_by == "bob"  # type: ignore[attr-defined]


def test_link_overwrite_unregistered_dir_non_admin_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unregistered directory already on disk is admin-only to overwrite via
    /tests/link — mirrors delete_test_suite's N3 rule for manually placed dirs."""
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    existing = tests_root / "manual"
    existing.mkdir()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    target = tmp_path / "src" / "manual"
    target.mkdir(parents=True)

    with TestClient(app) as client:
        resp = client.post("/tests/link", json={"path": str(target)})

    assert resp.status_code == 403
    assert existing.exists()


def test_link_symlink_failure_hides_tests_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    local = tmp_path / "proj"
    local.mkdir()

    def _fail_symlink(src: str, dst: Path) -> None:
        raise OSError(f"cannot link {src} -> {dst}")

    monkeypatch.setattr("os.symlink", _fail_symlink)
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/tests/link", json={"path": str(local)})

    assert resp.status_code == 500
    assert "Failed to create symlink" in resp.json()["detail"]
    assert str(tests_root) not in resp.text


def test_link_clear_existing_failure_hides_tests_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    existing = tests_root / "proj"
    existing.mkdir()
    local = tmp_path / "target" / "proj"
    local.mkdir(parents=True)

    def _fail_rmtree(path: Path) -> None:
        raise OSError(f"cannot remove {path}")

    monkeypatch.setattr("shutil.rmtree", _fail_rmtree)
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/tests/link", json={"path": str(local)})

    assert resp.status_code == 500
    assert "Failed to clear existing" in resp.json()["detail"]
    assert str(tests_root) not in resp.text
    assert existing.exists()


def test_link_tests_root_mkdir_failure_hides_tests_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    local = tmp_path / "proj"
    local.mkdir()

    original_mkdir = Path.mkdir

    def _fail_tests_root_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == tests_root:
            raise OSError(f"cannot create {self}")
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _fail_tests_root_mkdir)
    container = _make_container()
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/tests/link", json={"path": str(local)})

    assert resp.status_code == 500
    assert "Failed to create tests_root" in resp.json()["detail"]
    assert str(tests_root) not in resp.text


# ── External test suites: git clone / pull / delete (stage 2) ────────────


def _git_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    """Build a fake asyncio subprocess for git (communicate/wait/kill mocked)."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    return proc


def _patch_git(monkeypatch: pytest.MonkeyPatch, *procs: MagicMock) -> AsyncMock:
    """Patch ``asyncio.create_subprocess_exec`` to yield *procs* in call order."""
    mock = AsyncMock(side_effect=list(procs))
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock)
    return mock


def _forbid_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert git is never spawned (for paths that must reject before cloning)."""
    mock = AsyncMock(side_effect=AssertionError("git must not be invoked"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock)


def _save_suite_in_store(store: object, **overrides: object) -> TestSuite:
    base: dict[str, object] = dict(
        name="repo",
        source="git",
        repo_url="https://example.com/org/repo.git",
        ref="main",
        credential_ref=None,
        created_by="test_user",
        created_at=NOW,
    )
    base.update(overrides)
    suite = TestSuite(**base)  # type: ignore[arg-type]

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(store.save_suite(suite))  # type: ignore[attr-defined]
    loop.close()
    return suite


def test_clone_success_records_git_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _seed_encrypted_credential(container, cred_id="cred-1", owner="test_user", secret="ghp_x")
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0))

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={
                "url": "https://example.com/org/demo.git",
                "name": "my-suite",
                "ref": "v1.0",
                "credential_ref": "cred-1",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["suite_name"] == "my-suite"

    suite = container.store._suites["my-suite"]  # type: ignore[attr-defined]
    assert suite.source == "git"
    assert suite.repo_url == "https://example.com/org/demo.git"
    assert suite.ref == "v1.0"
    assert suite.credential_ref == "cred-1"
    assert suite.created_by == "test_user"

    # argv is parametrised (no shell): git clone --depth 1 -b v1.0 -- <url> <dir>
    args = mock.call_args.args
    assert args[0] == "git"
    assert args[1] == "clone"
    assert "--depth" in args and "1" in args
    assert "-b" in args and "v1.0" in args
    assert "--" in args
    assert "https://example.com/org/demo.git" in args
    assert str(tests_root / "my-suite") in args


def test_clone_save_failure_rolls_back_cloned_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-5: if ``save_suite`` fails after a successful clone, the on-disk working
    tree is removed (no orphan) and the caller gets a 503 — never a directory
    that exists on disk but is unknown to the store."""
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)

    # Emulate ``git clone`` materialising the working tree on disk (the mock
    # otherwise never touches the filesystem, so there'd be nothing to roll back).
    def _git_materialises_dir(*_args: object, **_kwargs: object) -> MagicMock:
        (tests_root / "orphan").mkdir(parents=True, exist_ok=True)
        return _git_proc(0)

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", AsyncMock(side_effect=_git_materialises_dir)
    )

    async def _boom(_suite: object) -> None:
        raise RuntimeError("db write failed")

    monkeypatch.setattr(container.store, "save_suite", _boom)

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={
                "url": "https://example.com/org/demo.git",
                "name": "orphan",
                "ref": "v1.0",
            },
        )

    assert resp.status_code == 503
    # The cloned directory must be gone — no orphan left behind.
    assert not (tests_root / "orphan").exists()
    # And nothing half-registered in the store.
    assert "orphan" not in container.store._suites  # type: ignore[attr-defined]


def _seed_encrypted_credential(
    container: Container, *, cred_id: str, owner: str, secret: str
) -> None:
    import asyncio

    from qarunner.core.credentials import CredentialCipher

    cipher = CredentialCipher(container.settings.secret_key)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(
        container.store.save_credential(
            Credential(
                id=cred_id,
                name="gh",
                type="https_token",
                created_by=owner,
                created_at=NOW,
            ),
            cipher.encrypt(secret),
        )
    )
    loop.close()


def test_clone_with_credential_injects_token_off_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-1: the token reaches git via a GIT_ASKPASS env, never via argv/URL."""
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _seed_encrypted_credential(
        container, cred_id="cred-1", owner="test_user", secret="ghp_secrettoken"
    )
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0))
    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={
                "url": "https://example.com/org/repo.git",
                "name": "s",
                "ref": "v1.0",
                "credential_ref": "cred-1",
            },
        )
    assert resp.status_code == 200
    # The token must never appear in the git argv.
    argv = mock.call_args.args
    assert all("ghp_secrettoken" not in str(a) for a in argv)
    # It rides the env for a throwaway askpass helper; interactive prompts off.
    env = mock.call_args.kwargs.get("env") or {}
    assert env.get("GIT_TERMINAL_PROMPT") == "0"
    assert env.get("QARUNNER_GIT_PASS") == "ghp_secrettoken"
    assert "GIT_ASKPASS" in env


def test_clone_credential_deleted_midflight_degrades_to_no_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU: a credential passes the existence/owner check, then is deleted
    before its secret is read — degrade to an unauthenticated clone, not a 500."""
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _seed_encrypted_credential(container, cred_id="cred-1", owner="test_user", secret="ghp_x")

    async def _gone(_credential_id: str) -> None:
        return None

    monkeypatch.setattr(container.store, "get_credential_secret", _gone)
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0))
    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={
                "url": "https://example.com/org/repo.git",
                "name": "s",
                "ref": "v1.0",
                "credential_ref": "cred-1",
            },
        )
    assert resp.status_code == 200
    # No secret resolvable → no auth env injected (parent env inherited).
    assert mock.call_args.kwargs.get("env") is None


def test_clone_unknown_credential_ref_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _forbid_git(monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={
                "url": "https://example.com/org/repo.git",
                "name": "s",
                "credential_ref": "ghost",
            },
        )
    assert resp.status_code == 400


def test_clone_with_others_credential_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _seed_encrypted_credential(container, cred_id="cred-bob", owner="bob", secret="ghp_bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    _forbid_git(monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={
                "url": "https://example.com/org/repo.git",
                "name": "s",
                "credential_ref": "cred-bob",
            },
        )
    assert resp.status_code == 403


def test_pull_injects_stored_credential_off_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    (tests_root / "s").mkdir(parents=True)
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _seed_encrypted_credential(
        container, cred_id="cred-1", owner="test_user", secret="ghp_pulltoken"
    )
    _save_suite_in_store(
        container.store,
        name="s",
        source="git",
        repo_url="https://example.com/org/repo.git",
        ref="main",
        credential_ref="cred-1",
    )
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0), _git_proc(0))  # fetch + reset
    with TestClient(app) as client:
        resp = client.post("/tests/s/pull")
    assert resp.status_code == 200
    fetch_call = mock.call_args_list[0]
    assert all("ghp_pulltoken" not in str(a) for a in fetch_call.args)
    env = fetch_call.kwargs.get("env") or {}
    assert env.get("QARUNNER_GIT_PASS") == "ghp_pulltoken"


def test_pull_with_others_recorded_credential_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    (tests_root / "s").mkdir(parents=True)
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _seed_encrypted_credential(container, cred_id="cred-bob", owner="bob", secret="ghp_bob")
    _save_suite_in_store(
        container.store,
        name="s",
        source="git",
        repo_url="https://example.com/org/repo.git",
        ref="main",
        credential_ref="cred-bob",
        created_by="alice",
    )
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/s/pull")

    assert resp.status_code == 403
    assert "cred-bob" not in resp.text
    assert "ghp_bob" not in resp.text


def test_pull_with_deleted_credential_falls_back_unauthenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suite whose credential was since deleted still pulls — just without auth
    (covers the lenient ``enc is None`` branch)."""
    tests_root = tmp_path / "external_tests"
    (tests_root / "s").mkdir(parents=True)
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(
        container.store,
        name="s",
        source="git",
        repo_url="https://example.com/org/repo.git",
        ref="main",
        credential_ref="gone",
    )
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0), _git_proc(0))
    with TestClient(app) as client:
        resp = client.post("/tests/s/pull")
    assert resp.status_code == 200
    # No resolvable credential → no auth env injected (parent env inherited).
    assert mock.call_args_list[0].kwargs.get("env") is None


def test_pull_credential_deleted_midflight_degrades_to_no_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    (tests_root / "s").mkdir(parents=True)
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _seed_encrypted_credential(container, cred_id="cred-1", owner="test_user", secret="ghp_x")
    _save_suite_in_store(
        container.store,
        name="s",
        source="git",
        repo_url="https://example.com/org/repo.git",
        ref="main",
        credential_ref="cred-1",
    )

    async def _gone(_credential_id: str) -> None:
        return None

    monkeypatch.setattr(container.store, "get_credential_secret", _gone)
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0), _git_proc(0))

    with TestClient(app) as client:
        resp = client.post("/tests/s/pull")

    assert resp.status_code == 200
    assert mock.call_args_list[0].kwargs.get("env") is None


def test_clone_records_default_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0), _git_proc(0, stdout=b"main\n"))

    with TestClient(app) as client:
        resp = client.post("/tests/clone", json={"url": "https://example.com/org/myrepo.git"})

    assert resp.status_code == 200
    assert resp.json()["suite_name"] == "myrepo"
    suite = container.store._suites["myrepo"]  # type: ignore[attr-defined]
    assert suite.ref == "main"

    # second git call resolves the default branch inside the cloned dir
    second = mock.call_args_list[1]
    assert "rev-parse" in second.args
    assert second.kwargs["cwd"] == str(tests_root / "myrepo")


def test_clone_default_branch_empty_records_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(0), _git_proc(0, stdout=b"   \n"))

    with TestClient(app) as client:
        resp = client.post("/tests/clone", json={"url": "https://example.com/org/blank.git"})

    assert resp.status_code == 200
    suite = container.store._suites["blank"]  # type: ignore[attr-defined]
    assert suite.ref is None


def test_clone_default_branch_revparse_failure_records_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(0), _git_proc(1, stderr=b"boom"))

    with TestClient(app) as client:
        resp = client.post("/tests/clone", json={"url": "https://example.com/org/detached.git"})

    assert resp.status_code == 200
    suite = container.store._suites["detached"]  # type: ignore[attr-defined]
    assert suite.ref is None


def test_clone_accepts_ssh_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(0))

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={"url": "git@example.com:org/repo.git", "ref": "dev"},
        )

    assert resp.status_code == 200
    assert resp.json()["suite_name"] == "repo"
    suite = container.store._suites["repo"]  # type: ignore[attr-defined]
    assert suite.source == "git"
    assert suite.ref == "dev"


def test_clone_name_without_git_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(0))

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone", json={"url": "https://example.com/org/plain", "ref": "dev"}
        )

    assert resp.status_code == 200
    assert resp.json()["suite_name"] == "plain"


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ext::sh -c whoami",
        "http://insecure/repo.git",
        "https://token@example.com/org/repo.git",
        "https://x-access-token:ghp_secret@example.com/org/repo.git",
    ],
)
def test_clone_rejects_unsupported_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_url: str
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/clone", json={"url": bad_url, "name": "x"})

    assert resp.status_code == 400


@pytest.mark.parametrize("bad_name", ["..", "a/b", ".hidden"])
def test_clone_rejects_unsafe_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_name: str
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={"url": "https://example.com/org/x.git", "name": bad_name},
        )

    assert resp.status_code == 400


def test_clone_conflict_existing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "dup").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={"url": "https://example.com/org/dup.git", "name": "dup"},
        )

    assert resp.status_code == 409


def test_clone_conflict_existing_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="dup")
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={"url": "https://example.com/org/dup.git", "name": "dup"},
        )

    assert resp.status_code == 409


def test_clone_git_failure_returns_502(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(1, stderr=b"fatal: repo not found"))

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={"url": "https://example.com/org/x.git", "name": "x"},
        )

    assert resp.status_code == 502
    assert "git clone failed" in resp.json()["detail"]
    assert "x" not in container.store._suites  # type: ignore[attr-defined]


def test_clone_failure_scrubs_server_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # git stderr must not leak the absolute suites-root path into the API detail.
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    leaked = f"fatal: could not create work tree dir '{tests_root}/x'".encode()
    _patch_git(monkeypatch, _git_proc(1, stderr=leaked))

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={"url": "https://example.com/org/x.git", "name": "x"},
        )

    assert resp.status_code == 502
    assert str(tests_root) not in resp.json()["detail"]
    assert "<suite>" in resp.json()["detail"]


def test_clone_timeout_returns_502(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    proc = _git_proc(0)
    proc.communicate = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))

    with TestClient(app) as client:
        resp = client.post(
            "/tests/clone",
            json={"url": "https://example.com/org/x.git", "name": "x"},
        )

    assert resp.status_code == 502
    proc.kill.assert_called_once()


def test_pull_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "repo").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", ref="main")
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0), _git_proc(0))

    with TestClient(app) as client:
        resp = client.post("/tests/repo/pull")

    assert resp.status_code == 200
    first = mock.call_args_list[0]
    assert "fetch" in first.args and "--depth" in first.args
    assert "origin" in first.args and "main" in first.args
    assert "--" in first.args  # refspec separated from options (defence in depth)
    assert first.kwargs["cwd"] == str(tests_root / "repo")
    second = mock.call_args_list[1]
    assert "reset" in second.args and "--hard" in second.args
    assert "FETCH_HEAD" in second.args


def test_pull_uses_head_when_ref_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "repo").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", ref=None)
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0), _git_proc(0))

    with TestClient(app) as client:
        resp = client.post("/tests/repo/pull")

    assert resp.status_code == 200
    assert "HEAD" in mock.call_args_list[0].args


def test_pull_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/ghost/pull")

    assert resp.status_code == 404


def test_pull_forbidden_non_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "repo").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/repo/pull")

    assert resp.status_code == 403


def test_pull_local_suite_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="local", repo_url=None, ref=None)
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/repo/pull")

    assert resp.status_code == 400
    assert "Only git suites" in resp.json()["detail"]


def test_pull_fetch_failure_502(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "repo").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo")
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(1, stderr=b"fatal: no remote"))

    with TestClient(app) as client:
        resp = client.post("/tests/repo/pull")

    assert resp.status_code == 502
    assert "git fetch failed" in resp.json()["detail"]


def test_pull_reset_failure_502(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "repo").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo")
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(0), _git_proc(1, stderr=b"fatal: reset"))

    with TestClient(app) as client:
        resp = client.post("/tests/repo/pull")

    assert resp.status_code == 502
    assert "git reset failed" in resp.json()["detail"]


def test_delete_git_suite_rmtree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="git")
    suite_dir = tests_root / "repo"
    suite_dir.mkdir()
    (suite_dir / "test_x.py").write_text("x")
    app = create_app(container)

    with TestClient(app) as client:
        resp = client.delete("/tests/repo")

    assert resp.status_code == 200
    assert not suite_dir.exists()
    assert "repo" not in container.store._suites  # type: ignore[attr-defined]


def test_delete_local_suite_unlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="proj", source="local", repo_url=None, ref=None)
    target = tmp_path / "proj-target"
    target.mkdir()
    link = tests_root / "proj"
    link.symlink_to(target)
    app = create_app(container)

    with TestClient(app) as client:
        resp = client.delete("/tests/proj")

    assert resp.status_code == 200
    assert not link.is_symlink()
    assert target.exists()  # symlink removed, target untouched
    assert "proj" not in container.store._suites  # type: ignore[attr-defined]


def test_delete_forbidden_non_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", created_by="bob")
    suite_dir = tests_root / "repo"
    suite_dir.mkdir()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.delete("/tests/repo")

    assert resp.status_code == 403
    assert suite_dir.exists()


def test_delete_unregistered_dir_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    suite_dir = tests_root / "manual"
    suite_dir.mkdir()
    app = create_app(container)

    with TestClient(app) as client:
        resp = client.delete("/tests/manual")

    assert resp.status_code == 200
    assert not suite_dir.exists()


def test_delete_unregistered_dir_non_admin_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    suite_dir = tests_root / "manual"
    suite_dir.mkdir()
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)

    with TestClient(app) as client:
        resp = client.delete("/tests/manual")

    assert resp.status_code == 403
    assert suite_dir.exists()


def test_delete_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)

    with TestClient(app) as client:
        resp = client.delete("/tests/ghost")

    assert resp.status_code == 404


def test_delete_orphan_record_no_fs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="git")
    app = create_app(container)

    with TestClient(app) as client:
        resp = client.delete("/tests/repo")

    assert resp.status_code == 200
    assert "repo" not in container.store._suites  # type: ignore[attr-defined]


# ── External test suites: npm ci dependency prep (stage 3) ───────────────


def test_prepare_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/ghost/prepare")

    assert resp.status_code == 404


def test_prepare_forbidden_non_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", created_by="bob")
    app = create_app(container)
    _override_user(app, "alice", UserRole.USER)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/repo/prepare")

    assert resp.status_code == 403


def test_prepare_local_suite_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="local", repo_url=None, ref=None)
    app = create_app(container)
    _forbid_git(monkeypatch)  # local suites reuse host deps; npm must not run

    with TestClient(app) as client:
        resp = client.post("/tests/repo/prepare")

    assert resp.status_code == 200
    assert "reuse host" in resp.json()["message"]


def test_prepare_git_no_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="git")
    app = create_app(container)
    _forbid_git(monkeypatch)

    with TestClient(app) as client:
        resp = client.post("/tests/repo/prepare")

    assert resp.status_code == 404


def test_prepare_git_no_package_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    (tests_root / "repo").mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="git")
    app = create_app(container)
    _forbid_git(monkeypatch)  # no package.json → nothing to install

    with TestClient(app) as client:
        resp = client.post("/tests/repo/prepare")

    assert resp.status_code == 200
    assert "nothing to prepare" in resp.json()["message"]


def test_prepare_git_npm_ci_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    suite_dir = tests_root / "repo"
    suite_dir.mkdir()
    (suite_dir / "package.json").write_text('{"name": "x"}')
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="git")
    app = create_app(container)
    mock = _patch_git(monkeypatch, _git_proc(0))

    with TestClient(app) as client:
        resp = client.post("/tests/repo/prepare")

    assert resp.status_code == 200
    assert "Dependencies installed" in resp.json()["message"]
    # SEC: --ignore-scripts is mandatory. A git suite is cloned from an arbitrary
    # repo, so its package.json lifecycle scripts (postinstall etc.) are untrusted;
    # plain `npm ci` would execute them in the platform process (RCE bypassing the
    # docker executor isolation).
    assert mock.call_args.args == ("npm", "ci", "--ignore-scripts")
    assert mock.call_args.kwargs["cwd"] == str(suite_dir)


def test_prepare_git_npm_ci_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests_root = tmp_path / "external_tests"
    tests_root.mkdir()
    suite_dir = tests_root / "repo"
    suite_dir.mkdir()
    (suite_dir / "package.json").write_text('{"name": "x"}')
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_root))
    container = _make_container()
    _save_suite_in_store(container.store, name="repo", source="git")
    app = create_app(container)
    _patch_git(monkeypatch, _git_proc(1, stderr=b"npm ERR! lockfile"))

    with TestClient(app) as client:
        resp = client.post("/tests/repo/prepare")

    assert resp.status_code == 502
    assert "npm ci failed" in resp.json()["detail"]
