"""Tests for api.app — create_app factory and lifespan."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI
from starlette.testclient import TestClient

from qarunner.api.app import create_app
from qarunner.api.deps import Container
from qarunner.config import Settings
from qarunner.errors import RunNotFound
from qarunner.models import Run
from tests.fakes.fake_schedule_port import FakeSchedulePort
from tests.fakes.fake_scheduler import FakeScheduler


@dataclass
class _FakeStore:
    _runs: dict[str, Run] = field(default_factory=dict)
    initialized: bool = False
    closed: bool = False

    async def save(self, run: Run) -> None:
        self._runs[run.id] = run

    async def get(self, run_id: str) -> Run:
        raise RunNotFound(run_id)

    async def list(self) -> list[Run]:
        return []

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True

    async def list_schedules(self, profile_id: str | None = None) -> list:
        return []

    async def get_schedule(self, schedule_id: str) -> None:
        return None

    async def save_schedule(self, schedule: any) -> None:
        pass

    async def get_profile(self, profile_id: str) -> None:
        return None


@dataclass
class _FakeOrch:
    store: _FakeStore

    async def drain(self, timeout: float | None = None) -> None:
        return None


def test_create_app_returns_fastapi() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "qarunner"


def test_create_app_with_container_injects_it() -> None:
    container = Container(
        orchestrator=None,
        store=None,
        task_scheduler=None,
        scheduler=None,
        schedule_service=None,
        profile_service=None,
        login_throttle=None,
        settings=Settings(),
    )  # type: ignore[arg-type]
    app = create_app(container)
    assert app.state.container is container


def test_create_app_adds_cors_when_origins_configured() -> None:
    container = Container(
        orchestrator=None,
        store=None,
        task_scheduler=None,
        scheduler=None,
        schedule_service=None,
        profile_service=None,
        login_throttle=None,
        settings=Settings(cors_origins="http://localhost:5173, https://qa.example.com"),
    )  # type: ignore[arg-type]
    app = create_app(container)
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert cors.kwargs["allow_origins"] == ["http://localhost:5173", "https://qa.example.com"]


def test_create_app_without_container() -> None:
    app = create_app()
    assert not hasattr(app.state, "container")


def test_lifespan_runs_without_error() -> None:
    from datetime import UTC, datetime

    from qarunner.api.deps import get_current_user
    from qarunner.models import User, UserRole

    async def mock_get_current_user() -> User:
        return User(username="test_user", role=UserRole.ADMIN, created_at=datetime.now(UTC))

    store = _FakeStore()
    orch = _FakeOrch(store=store)
    container = Container(
        orchestrator=orch,
        store=store,
        task_scheduler=FakeScheduler(),
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
        login_throttle=None,
        settings=Settings(),
    )  # type: ignore[arg-type]
    app = create_app(container)
    app.dependency_overrides[get_current_user] = mock_get_current_user
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200
    assert store.initialized is True
    assert store.closed is True


def test_lifespan_skips_cookie_secure_warning_when_enabled(caplog) -> None:
    """BUG-12: the plaintext-cookie startup warning only fires when
    cookie_secure is actually False — a properly configured HTTPS
    deployment shouldn't see it."""
    from datetime import UTC, datetime

    from qarunner.api.deps import get_current_user
    from qarunner.models import User, UserRole

    async def mock_get_current_user() -> User:
        return User(username="test_user", role=UserRole.ADMIN, created_at=datetime.now(UTC))

    store = _FakeStore()
    orch = _FakeOrch(store=store)
    container = Container(
        orchestrator=orch,
        store=store,
        task_scheduler=FakeScheduler(),
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
        login_throttle=None,
        settings=Settings(cookie_secure=True),
    )  # type: ignore[arg-type]
    app = create_app(container)
    app.dependency_overrides[get_current_user] = mock_get_current_user
    with caplog.at_level("WARNING", logger="qarunner.api.app"), TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200
    assert not any("plaintext HTTP" in rec.getMessage() for rec in caplog.records)


def test_lifespan_without_container_creates_one(monkeypatch) -> None:
    """When no container is injected, lifespan creates a real one."""
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from qarunner.api.deps import get_current_user
    from qarunner.models import User, UserRole

    async def mock_get_current_user() -> User:
        return User(username="test_user", role=UserRole.ADMIN, created_at=datetime.now(UTC))

    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("QARUNNER_DB_PATH", str(Path(td) / "test.db"))
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(Path(td) / "tests"))
        monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(Path(td) / "artifacts"))
        app = create_app()
        app.dependency_overrides[get_current_user] = mock_get_current_user
        with TestClient(app) as client:
            resp = client.get("/runs")
        assert resp.status_code == 200
        assert resp.json() == {"runs": []}


def test_static_files_mounting(tmp_path, monkeypatch) -> None:
    """Test that StaticFiles mounts index.html if QARUNNER_STATIC_ROOT is set."""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    index_html = dist_dir / "index.html"
    index_html.write_text("Hello React", encoding="utf-8")

    monkeypatch.setenv("QARUNNER_STATIC_ROOT", str(dist_dir))

    # Provide a mock store so create_app's lifespan doesn't try to open real sqlite
    store = _FakeStore()
    orch = _FakeOrch(store=store)
    container = Container(
        orchestrator=orch,
        store=store,
        task_scheduler=FakeScheduler(),
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
        login_throttle=None,
        settings=Settings(),
    )  # type: ignore[arg-type]

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text == "Hello React"


def test_static_files_not_mounted_if_no_dir(monkeypatch) -> None:
    """Test that static files are not mounted if the directory does not exist."""
    monkeypatch.setenv("QARUNNER_STATIC_ROOT", "/nonexistent/static/path")

    store = _FakeStore()
    orch = _FakeOrch(store=store)
    container = Container(
        orchestrator=orch,
        store=store,
        task_scheduler=FakeScheduler(),
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
        login_throttle=None,
        settings=Settings(),
    )  # type: ignore[arg-type]

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/")
    # Should 404 since root is not mounted
    assert resp.status_code == 404


def test_lifespan_recovers_interrupted_runs(monkeypatch) -> None:
    """Startup marks runs left QUEUED/RUNNING by a previous process as FAILED."""
    import asyncio
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from qarunner.adapters.sqlite_store import SqliteStore
    from qarunner.models import Run, RunStatus

    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        monkeypatch.setenv("QARUNNER_DB_PATH", db_path)
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(Path(td) / "tests"))
        monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(Path(td) / "artifacts"))

        async def seed() -> None:
            import socket

            store = SqliteStore(db_path)
            await store.initialize()
            await store.save(
                Run(
                    id="orphan-1",
                    status=RunStatus.RUNNING,
                    runner="pytest",
                    created_by="system",
                    tests_path="x",
                    created_at=datetime.now(UTC),
                    worker_node_id=socket.gethostname(),
                )
            )
            await store.close()

        asyncio.run(seed())

        # Entering the TestClient context triggers lifespan startup (recovery).
        app = create_app()
        with TestClient(app):
            pass

        async def fetch() -> Run:
            store = SqliteStore(db_path)
            await store.initialize()
            run = await store.get("orphan-1")
            await store.close()
            return run

        recovered = asyncio.run(fetch())
        assert recovered.status == RunStatus.FAILED
        assert recovered.error == "interrupted by server restart"
        assert recovered.finished_at is not None


def test_lifespan_skips_recovery_when_disabled(monkeypatch) -> None:
    """CONC-2: with crash_recovery_on_startup off, startup leaves in-flight runs alone.

    A late-starting replica must not fail runs still executing in its siblings, so
    the single-instance recovery is gated behind a setting that multi-replica
    deployments disable.
    """
    import asyncio
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from qarunner.adapters.sqlite_store import SqliteStore
    from qarunner.models import Run, RunStatus

    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        monkeypatch.setenv("QARUNNER_DB_PATH", db_path)
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(Path(td) / "tests"))
        monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(Path(td) / "artifacts"))
        monkeypatch.setenv("QARUNNER_CRASH_RECOVERY_ON_STARTUP", "false")

        async def seed() -> None:
            store = SqliteStore(db_path)
            await store.initialize()
            await store.save(
                Run(
                    id="orphan-1",
                    status=RunStatus.RUNNING,
                    runner="pytest",
                    created_by="system",
                    tests_path="x",
                    created_at=datetime.now(UTC),
                )
            )
            await store.close()

        asyncio.run(seed())

        app = create_app()
        with TestClient(app):
            pass

        async def fetch() -> Run:
            store = SqliteStore(db_path)
            await store.initialize()
            run = await store.get("orphan-1")
            await store.close()
            return run

        result = asyncio.run(fetch())
        assert result.status == RunStatus.RUNNING


async def test_lifespan_drains_inflight_runs_before_closing_store() -> None:
    """DATA-4: shutdown waits for in-flight executions to persist before close.

    Drives the real lifespan with a task that saves *after* shutdown begins; the
    drain step must let that save land against an open store, so the recorded
    order is save-then-close (not close-then-AssertionError).
    """
    import asyncio
    from datetime import UTC, datetime

    from qarunner.adapters.asyncio_scheduler import AsyncioScheduler
    from qarunner.api.app import lifespan
    from qarunner.models import Run, RunStatus

    events: list[str] = []

    class _OrderStore(_FakeStore):
        async def save(self, run: Run) -> None:
            events.append("save")

        async def close(self) -> None:
            events.append("close")

        async def dequeue_next_queued(self) -> str | None:
            return None  # no queued runs in this test

    @dataclass
    class _DrainOrch:
        store: _OrderStore
        scheduler: AsyncioScheduler

        async def drain(self, timeout: float | None = None) -> None:
            await self.scheduler.drain(timeout)

    store = _OrderStore()
    sched = AsyncioScheduler(store=store, run_fn=lambda _: asyncio.sleep(0))
    container = Container(
        orchestrator=_DrainOrch(store=store, scheduler=sched),
        store=store,
        task_scheduler=sched,
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
        login_throttle=None,
        settings=Settings(),
    )  # type: ignore[arg-type]
    app = FastAPI()
    app.state.container = container

    async def slow_save() -> None:
        await asyncio.sleep(0.05)  # still in flight when shutdown begins
        await store.save(
            Run(
                id="x",
                status=RunStatus.RUNNING,
                runner="pytest",
                created_by="system",
                tests_path="x",
                created_at=datetime.now(UTC),
            )
        )

    async with lifespan(app):
        # Manually track an inflight task so drain() has something to wait for.
        task = asyncio.create_task(slow_save())
        sched._tasks.add(task)
    # Context exit ran shutdown: drain awaited the in-flight save, then closed.
    assert events == ["save", "close"]
