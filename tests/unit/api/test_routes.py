"""Tests for api.routes — HTTP endpoints via TestClient."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from qarunner.api.app import create_app as _real_create_app
from qarunner.api.deps import Container, get_current_admin, get_current_user
from qarunner.errors import RunNotFound, UnknownRunner, UnsafePath
from qarunner.models import (
    ReportRef,
    Run,
    RunRequest,
    RunStatus,
    User,
    UserRole,
    TestProfile,
    TestSchedule,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def create_app(container: Container | None = None) -> FastAPI:
    app = _real_create_app(container)

    async def mock_get_current_user() -> User:
        return User(username="test_user", role=UserRole.ADMIN, created_at=datetime.now())

    async def mock_get_current_admin() -> User:
        return User(username="test_user", role=UserRole.ADMIN, created_at=datetime.now())

    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[get_current_admin] = mock_get_current_admin
    return app


# ── fakes local to this test module ────────────────────────────────────


@dataclass
class FakeStore:
    """In-memory store for route tests."""

    _runs: dict[str, Run] = field(default_factory=dict)
    _profiles: dict[str, TestProfile] = field(default_factory=dict)
    _schedules: dict[str, TestSchedule] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._profiles = {}
        self._schedules = {}

    async def save(self, run: Run) -> None:
        self._runs[run.id] = run

    async def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFound(run_id) from None

    async def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    async def lock_run(self, run_id: str, locked: bool) -> None:
        if run_id in self._runs:
            run = self._runs[run_id]
            self._runs[run_id] = run.model_copy(update={"locked": locked})

    async def get_old_unlocked_runs(self, retention_days: int) -> list[Run]:
        from datetime import timedelta
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        res = []
        for r in self._runs.values():
            if r.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.TIMEOUT) and not r.locked:
                if r.finished_at and r.finished_at <= cutoff:
                    res.append(r)
        return res

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

    async def create(self, req: RunRequest, created_by: str) -> Run:
        if self.raise_unknown_runner:
            raise UnknownRunner(req.runner)
        if self.raise_unsafe_path:
            raise UnsafePath(f"{req.tests_path!r} resolves outside root")
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
            created_at=NOW,
        )
        await self.store.save(run)
        return run


def _make_container(**orch_kwargs: object) -> Container:
    store = FakeStore()
    orchestrator = FakeOrchestrator(store=store, **orch_kwargs)  # type: ignore[arg-type]
    return Container(orchestrator=orchestrator, store=store)  # type: ignore[arg-type]


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
    container = _make_container(raise_unsafe_path=True)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs", json={"tests_path": "../../etc", "runner": "pytest"})
    assert resp.status_code == 400


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

    container = _make_container()
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
        assert len(resp.json()["next_runs"]) == 5

        # B. Test preview schedule with invalid expression
        resp_err = client.get("/schedules/preview?expression=invalid_expr&timezone=UTC")
        assert resp_err.status_code == 400

        # C. Create schedule
        sched_payload = {
            "name": "Nightly Regression",
            "profile_id": "profile-abc",
            "cron_expression": "0 2 * * *",
            "enabled": True,
            "timezone": "America/New_York"
        }
        resp_create = client.post("/schedules", json=sched_payload)
        assert resp_create.status_code == 201
        sched_id = resp_create.json()["id"]
        assert resp_create.json()["name"] == "Nightly Regression"
        assert resp_create.json()["timezone"] == "America/New_York"

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
            "timezone": "America/Los_Angeles"
        }
        resp_update = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp_update.status_code == 200
        assert resp_update.json()["name"] == "Daily Regression Updated"
        assert resp_update.json()["cron_expression"] == "0 3 * * *"
        assert resp_update.json()["enabled"] is False
        assert resp_update.json()["timezone"] == "America/Los_Angeles"

        # G. Delete schedule
        resp_del = client.delete(f"/schedules/{sched_id}")
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "success"

        # H. Get deleted schedule (should be 404)
        resp_get_deleted = client.get(f"/schedules/{sched_id}")
        assert resp_get_deleted.status_code == 404


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


def test_cleanup_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))

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
    loop.close()

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.post("/runs/cleanup?retention_days=30")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["cleaned_runs"] == 1

        # Check physical existence on disk
        assert not dir_old.exists()
        assert dir_locked.exists()


def test_stream_run_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))

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
    (run_dir / "stdout.log").write_text("line1\nline2\n")

    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(container.store.save(run))
    loop.close()

    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run-stream/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        lines = resp.text.split("\n\n")
        assert "data: line1" in lines
        assert "data: line2" in lines


