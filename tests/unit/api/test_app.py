"""Tests for api.app — create_app factory and lifespan."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI
from starlette.testclient import TestClient

from qarunner.api.app import create_app
from qarunner.api.deps import Container
from qarunner.errors import RunNotFound
from qarunner.models import Run
from tests.fakes.fake_schedule_port import FakeSchedulePort


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


def test_create_app_returns_fastapi() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "qarunner"


def test_create_app_with_container_injects_it() -> None:
    container = Container(
        orchestrator=None,
        store=None,
        scheduler=None,
        schedule_service=None,
        profile_service=None,
    )  # type: ignore[arg-type]
    app = create_app(container)
    assert app.state.container is container


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
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
    )  # type: ignore[arg-type]
    app = create_app(container)
    app.dependency_overrides[get_current_user] = mock_get_current_user
    with TestClient(app) as client:
        resp = client.get("/runs")
    assert resp.status_code == 200
    assert store.initialized is True
    assert store.closed is True


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
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
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
        scheduler=FakeSchedulePort(),
        schedule_service=None,
        profile_service=None,
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

