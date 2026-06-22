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
    _users: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._profiles = {}
        self._schedules = {}
        self._users = {}
        from qarunner.core.auth import hash_password
        self._users["test_user"] = {
            "username": "test_user",
            "password_hash": hash_password("test_pass"),
            "role": "admin",
            "created_at": "2026-06-20T16:00:00Z"
        }

    async def get_user(self, username: str) -> dict | None:
        return self._users.get(username)

    async def create_user(self, username: str, password_hash: str, role: str) -> None:
        from datetime import UTC, datetime
        self._users[username] = {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "created_at": datetime.now(UTC).isoformat()
        }

    async def list_users(self) -> list[dict]:
        return [
            {
                "username": u["username"],
                "role": u["role"],
                "created_at": u["created_at"]
            }
            for u in sorted(self._users.values(), key=lambda x: x["username"])
        ]

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
    container = _make_container()
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
    container = _make_container()
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
    container = _make_container()
    _report_run(container.store, "rd", report_dir)
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/rd/report/nonexistent.js")
    assert resp.status_code == 404


# ── object-level authorization (SEC-4) ─────────────────────────────────


def _override_user(app: FastAPI, username: str, role: UserRole) -> None:
    async def _u() -> User:
        return User(username=username, role=role, created_at=NOW)

    app.dependency_overrides[get_current_user] = _u


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
        lines = resp.text.split("\n\n")
        assert "data: line1" in lines
        assert "data: line2" in lines


def test_user_registration_and_login() -> None:
    from qarunner.core.auth import hash_password
    container = _make_container()
    app = create_app(container)

    with TestClient(app) as client:
        # A. Register user successfully
        payload = {
            "username": "new_guy",
            "password": "secret_password",
            "role": "user"
        }
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
        resp_login = client.post("/auth/login", json={"username": "new_guy", "password": "secret_password"})
        assert resp_login.status_code == 200
        assert "access_token" in resp_login.json()

        # E. Login with incorrect password
        resp_bad_pw = client.post("/auth/login", json={"username": "new_guy", "password": "wrong_password"})
        assert resp_bad_pw.status_code == 401

        # F. Login with nonexistent user
        resp_bad_user = client.post("/auth/login", json={"username": "ghost", "password": "some_password"})
        assert resp_bad_user.status_code == 401

        # G. Get me
        resp_me = client.get("/auth/me")
        assert resp_me.status_code == 200
        assert resp_me.json()["username"] == "test_user"


def test_test_tree_and_markers_detailed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set up simulated test root folder structure
    tests_dir = tmp_path / "test_suites"
    tests_dir.mkdir()
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", str(tests_dir))

    suite_dir = tests_dir / "suite_xyz"
    suite_dir.mkdir()

    # Create directories and files
    (suite_dir / "test_active.py").write_text("""
import pytest

@pytest.mark.foo
def test_one():
    pass

@pytest.mark.bar()
class TestClass:
    @pytest.mark.nested
    def test_two(self):
        pass
""", encoding="utf-8")

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
    (sub_dir / "test_sub.py").write_text("""
import pytest
@pytest.mark.sub_marker
def test_three():
    pass
""", encoding="utf-8")

    # Valid subdirectory with python file and various decorators to cover AST branch branches
    valid_sub = suite_dir / "valid_sub_folder"
    valid_sub.mkdir()
    (valid_sub / "test_another.py").write_text("""
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
""", encoding="utf-8")

    # Syntax error file to trigger AST exception
    (suite_dir / "test_bad.py").write_text("""
def parse_error_here(
""", encoding="utf-8")

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
        assert "sub_folder" not in names  # Because its iterdir raised an exception and was skipped!
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
        monkeypatch.undo()  # Undo route-level subpath mocking to allow normal safe_subpath execution
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


def test_profile_crud_endpoints() -> None:
    container = _make_container()
    app = create_app(container)

    with TestClient(app) as client:
        # A. Create profile
        payload = {
            "name": "Integration Profile",
            "description": "Integration testing profile",
            "tests_path": "tests/unit",
            "selected_files": ["test_routes.py"],
            "selected_markers": ["unit"],
            "extra_args": "-vv",
            "executor_mode": "subprocess",
            "timeout": 120
        }
        resp = client.post("/profiles", json=payload)
        assert resp.status_code == 201
        profile_id = resp.json()["id"]
        assert resp.json()["name"] == "Integration Profile"

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
            "selected_files": ["test_routes.py"],
            "selected_markers": ["unit"],
            "extra_args": "-v",
            "executor_mode": "docker",
            "timeout": 300
        }
        resp_update = client.put(f"/profiles/{profile_id}", json=update_payload)
        assert resp_update.status_code == 200
        assert resp_update.json()["name"] == "Updated Profile"
        assert resp_update.json()["executor_mode"] == "docker"

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
    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))

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

    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
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


def test_stream_run_logs_missing_and_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))

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

    # C. Event generator: file exists, read to the end, then status becomes completed and remaining content is flushed
    run = _make_run_in_store(container.store, id="run_flush", status=RunStatus.RUNNING)
    run_dir = tmp_path / "run_flush"
    run_dir.mkdir()
    log_file = run_dir / "stdout.log"
    log_file.write_text("line1\n", encoding="utf-8")

    # We will simulate a background task or mock store.get to update status to COMPLETED and append to log file
    call_count = 0
    original_get = container.store.get
    async def mock_get(run_id: str):
        nonlocal call_count
        run_obj = await original_get(run_id)
        if run_id == "run_flush":
            call_count += 1
            if call_count == 2:
                # Still running, this triggers the sleep(0.2) branch on line 455 of routes.py!
                return run_obj.model_copy(update={"status": RunStatus.RUNNING})
            elif call_count >= 3:
                # Append a final line and change status to completed
                log_file.write_text("line1\nline_final\n", encoding="utf-8")
                return run_obj.model_copy(update={"status": RunStatus.COMPLETED})
        return run_obj

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

    # E. Event generator: file doesn't exist, run is RUNNING, wait loop runs all 50 iterations and exits
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

    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    _make_run_in_store(container.store, id="run_disc_init", status=RunStatus.RUNNING)
    # No log file exists; without the disconnect check the initial loop would spin.
    monkeypatch.setattr(
        "starlette.requests.Request.is_disconnected", AsyncMock(return_value=True)
    )
    app = create_app(container)
    with TestClient(app) as client:
        resp = client.get("/runs/run_disc_init/stream")
        assert resp.status_code == 200
        # Generator returned immediately; the "not found" message was never reached.
        assert "[System] Log file not found." not in resp.text


def test_stream_disconnect_midstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CONC-1: a disconnect during follow breaks the read loop (no orphaned handle)."""
    from unittest.mock import AsyncMock

    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
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

    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
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

    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))
    monkeypatch.setattr(routes, "_SSE_MAX_TAIL_BYTES", 4)

    async def _instant(delay):
        return

    monkeypatch.setattr(asyncio, "sleep", _instant)
    _make_run_in_store(container.store, id="run_tail", status=RunStatus.RUNNING)
    run_dir = tmp_path / "run_tail"
    run_dir.mkdir()
    log_file = run_dir / "stdout.log"
    log_file.write_text("seed\n", encoding="utf-8")

    call_count = 0
    original_get = container.store.get

    async def mock_get(run_id: str):
        # call #1 is the route-level access check; keep it RUNNING and unchanged.
        # On the follow loop's status check (#2) go terminal, leaving a long tail
        # so the byte-bounded final flush reads only its first 4 bytes.
        nonlocal call_count
        run_obj = await original_get(run_id)
        if run_id == "run_tail":
            call_count += 1
            if call_count >= 2:
                log_file.write_text("seed\nABCDEFGHIJKLMNOP\n", encoding="utf-8")
                return run_obj.model_copy(update={"status": RunStatus.COMPLETED})
        return run_obj

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
    container = _make_container()
    monkeypatch.setenv("QARUNNER_ARTIFACTS_ROOT", str(tmp_path))

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
        self._is_mock_exc = (expr == "0 9 * * *")
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
            "timezone": "UTC"
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

        # 4b. Create schedule with exception on next_run_at calculation (covers 582-583 exception catch!)
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
            "timezone": "UTC"
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

        # 9b. Update schedule - set enabled=True (calls add_or_update_schedule_job - covers 691!)
        update_payload["enabled"] = True
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # 9c. Update schedule with exception on next_run_at calculation (covers 668-672 exception catch!)
        update_payload["cron_expression"] = "0 9 * * *"
        resp = client.put(f"/schedules/{sched_id}", json=update_payload)
        assert resp.status_code == 200
        assert resp.json()["next_run_at"] is None

        # 10. Delete nonexistent schedule (404)
        resp = client.delete("/schedules/ghost-sched-id")
        assert resp.status_code == 404



