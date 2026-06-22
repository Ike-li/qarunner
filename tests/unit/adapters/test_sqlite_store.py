"""Tests for SqliteStore adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qarunner.adapters.sqlite_store import SqliteStore
from qarunner.errors import RunNotFound
from qarunner.models import (
    ReportRef,
    Run,
    RunStatus,
    TestProfile,
    TestSchedule,
    TestSummary,
)


@pytest.fixture
async def store(tmp_path: object) -> SqliteStore:
    s = SqliteStore(":memory:")
    await s.initialize()
    yield s  # type: ignore[misc]
    await s.close()


def _make_run(**kwargs: object) -> Run:
    defaults: dict[str, object] = {
        "id": "run-001",
        "status": RunStatus.QUEUED,
        "runner": "pytest",
        "created_by": "test_user",
        "tests_path": "tests/",
        "args": ["-v"],
        "allure_enabled": True,
        "timeout": 300,
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return Run(**defaults)  # type: ignore[arg-type]


async def test_save_and_get(store: SqliteStore) -> None:
    run = _make_run()
    await store.save(run)
    retrieved = await store.get("run-001")
    assert retrieved.id == "run-001"
    assert retrieved.status == RunStatus.QUEUED
    assert retrieved.runner == "pytest"
    assert retrieved.created_by == "test_user"
    assert retrieved.tests_path == "tests/"
    assert retrieved.args == ["-v"]
    assert retrieved.allure_enabled is True
    assert retrieved.timeout == 300
    assert retrieved.created_at == datetime(2025, 1, 1, tzinfo=UTC)


async def test_get_missing_raises_run_not_found(store: SqliteStore) -> None:
    with pytest.raises(RunNotFound):
        await store.get("nonexistent-id")


async def test_list_empty(store: SqliteStore) -> None:
    result = await store.list()
    assert result == []


async def test_list_ordered_by_created_at_desc(store: SqliteStore) -> None:
    run1 = _make_run(id="run-001", created_at=datetime(2025, 1, 1, tzinfo=UTC))
    run2 = _make_run(id="run-002", created_at=datetime(2025, 6, 1, tzinfo=UTC))
    await store.save(run1)
    await store.save(run2)
    result = await store.list()
    assert [r.id for r in result] == ["run-002", "run-001"]


async def test_save_with_summary(store: SqliteStore) -> None:
    summary = TestSummary(
        total=10, passed=8, failed=1, skipped=1, error=0, duration_ms=5000
    )
    run = _make_run(summary=summary, status=RunStatus.COMPLETED)
    await store.save(run)
    retrieved = await store.get("run-001")
    assert retrieved.summary is not None
    assert retrieved.summary.total == 10
    assert retrieved.summary.passed == 8


async def test_save_with_report(store: SqliteStore) -> None:
    report = ReportRef(
        allure_results_dir="/tmp/results",
        allure_report_file="/tmp/results/report/index.html",
        html_generated=True,
    )
    run = _make_run(report=report, status=RunStatus.COMPLETED)
    await store.save(run)
    retrieved = await store.get("run-001")
    assert retrieved.report is not None
    assert retrieved.report.html_generated is True
    assert retrieved.report.allure_report_file == "/tmp/results/report/index.html"


async def test_save_with_started_and_finished(store: SqliteStore) -> None:
    started = datetime(2025, 1, 1, 0, 0, 1, tzinfo=UTC)
    finished = datetime(2025, 1, 1, 0, 0, 5, tzinfo=UTC)
    run = _make_run(
        status=RunStatus.COMPLETED,
        started_at=started,
        finished_at=finished,
        exit_code=0,
    )
    await store.save(run)
    retrieved = await store.get("run-001")
    assert retrieved.started_at == started
    assert retrieved.finished_at == finished
    assert retrieved.exit_code == 0


async def test_save_with_error(store: SqliteStore) -> None:
    run = _make_run(status=RunStatus.FAILED, error="something went wrong")
    await store.save(run)
    retrieved = await store.get("run-001")
    assert retrieved.error == "something went wrong"


async def test_upsert(store: SqliteStore) -> None:
    run = _make_run(status=RunStatus.QUEUED)
    await store.save(run)
    updated = _make_run(status=RunStatus.RUNNING)
    await store.save(updated)
    retrieved = await store.get("run-001")
    assert retrieved.status == RunStatus.RUNNING
    # Should still be only one row
    all_runs = await store.list()
    assert len(all_runs) == 1


async def test_close(store: SqliteStore) -> None:
    await store.close()
    # Second close is a no-op
    await store.close()


async def test_user_operations(store: SqliteStore) -> None:
    assert await store.get_user("nonexistent") is None

    await store.create_user("alice", "hashed_pwd", "user")
    user = await store.get_user("alice")
    assert user is not None
    assert user["username"] == "alice"
    assert user["password_hash"] == "hashed_pwd"
    assert user["role"] == "user"

    users = await store.list_users()
    assert len(users) >= 2
    usernames = [u["username"] for u in users]
    assert "alice" in usernames
    assert "admin" in usernames


async def test_schema_migration_adds_created_by(tmp_path) -> None:
    import aiosqlite
    db_file = tmp_path / "legacy.db"
    db_path = str(db_file)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                runner TEXT NOT NULL,
                tests_path TEXT NOT NULL,
                args_json TEXT NOT NULL,
                allure_enabled INTEGER NOT NULL,
                timeout INTEGER,
                summary TEXT,
                report TEXT,
                exit_code INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        await db.commit()

    store = SqliteStore(db_path)
    await store.initialize()

    async with store._connect() as db, db.execute("PRAGMA table_info(runs)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    assert "created_by" in columns

    await store.close()


async def test_initialize_already_has_users(tmp_path) -> None:
    db_file = tmp_path / "test_init.db"
    db_path = str(db_file)

    store = SqliteStore(db_path)
    await store.initialize()
    await store.close()

    store2 = SqliteStore(db_path)
    await store2.initialize()

    users = await store2.list_users()
    assert len(users) >= 1
    await store2.close()


async def test_profile_crud(store: SqliteStore) -> None:
    profile = TestProfile(
        id="profile-001",
        name="Regression Profile",
        description="Daily regression suite",
        tests_path="tests/",
        selected_files=["test_smoke.py"],
        selected_markers=["smoke"],
        extra_args="-v",
        executor_mode="subprocess",
        timeout=120,
        created_by="test_user",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_profile(profile)

    retrieved = await store.get_profile("profile-001")
    assert retrieved is not None
    assert retrieved.id == "profile-001"
    assert retrieved.name == "Regression Profile"
    assert retrieved.selected_files == ["test_smoke.py"]

    profiles = await store.list_profiles()
    assert len(profiles) == 1
    assert profiles[0].id == "profile-001"

    deleted = await store.delete_profile("profile-001")
    assert deleted is True

    retrieved2 = await store.get_profile("profile-001")
    assert retrieved2 is None


async def test_schedule_crud_and_cascade(store: SqliteStore) -> None:
    # 1. Create a profile
    profile = TestProfile(
        id="profile-002",
        name="Profile for schedule",
        tests_path="tests/",
        created_by="test_user",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_profile(profile)

    # 2. Save schedule
    schedule = TestSchedule(
        id="sched-001",
        name="Nightly Sched",
        profile_id="profile-002",
        cron_expression="0 2 * * *",
        enabled=True,
        timezone="America/New_York",
        last_run_at=datetime(2025, 1, 1, 2, 0, tzinfo=UTC),
        next_run_at=datetime(2025, 1, 2, 2, 0, tzinfo=UTC),
        created_by="test_user",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_schedule(schedule)

    # 3. Get schedule
    retrieved = await store.get_schedule("sched-001")
    assert retrieved is not None
    assert retrieved.id == "sched-001"
    assert retrieved.name == "Nightly Sched"
    assert retrieved.cron_expression == "0 2 * * *"
    assert retrieved.enabled is True
    assert retrieved.timezone == "America/New_York"
    assert retrieved.last_run_at == datetime(2025, 1, 1, 2, 0, tzinfo=UTC)
    assert retrieved.next_run_at == datetime(2025, 1, 2, 2, 0, tzinfo=UTC)

    # 4. List schedules
    schedules = await store.list_schedules()
    assert len(schedules) == 1
    assert schedules[0].id == "sched-001"

    schedules_by_profile = await store.list_schedules(profile_id="profile-002")
    assert len(schedules_by_profile) == 1

    schedules_by_empty_profile = await store.list_schedules(profile_id="nonexistent")
    assert len(schedules_by_empty_profile) == 0

    # 5. Cascade delete verification
    await store.delete_profile("profile-002")
    # FK ON + ON DELETE CASCADE: sched-001 is deleted automatically
    retrieved_after_cascade = await store.get_schedule("sched-001")
    assert retrieved_after_cascade is None


async def test_sqlite_store_migration_env_json(tmp_path) -> None:
    # 1. Create legacy database schema without 'env_json' column in test_profiles
    import aiosqlite
    db_path = tmp_path / "legacy.db"

    async with aiosqlite.connect(db_path) as db:
        # Create legacy test_profiles table
        await db.execute(
            """
            CREATE TABLE test_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                tests_path TEXT NOT NULL,
                selected_files TEXT NOT NULL,
                selected_markers TEXT NOT NULL,
                extra_args TEXT NOT NULL,
                executor_mode TEXT NOT NULL,
                timeout INTEGER,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Create users table so pre-populate admin has a table
        await db.execute(
            "CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, "
            "role TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        await db.commit()

    # 2. Instantiate SqliteStore and initialize (triggers self-migration)
    store = SqliteStore(str(db_path))
    await store.initialize()

    # 3. Verify env_json column now exists and can be queried
    profile = TestProfile(
        id="profile-migrated",
        name="Migrated Profile",
        tests_path="tests/",
        env={"FOO": "BAR"},
        created_by="test_user",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_profile(profile)
    retrieved = await store.get_profile("profile-migrated")
    assert retrieved is not None
    assert retrieved.env == {"FOO": "BAR"}

    await store.close()


async def test_sqlite_store_list_profiles_filter(store: SqliteStore) -> None:
    p1 = TestProfile(
        id="p1",
        name="Profile 1",
        tests_path="suite_a",
        created_by="test_user",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    p2 = TestProfile(
        id="p2",
        name="Profile 2",
        tests_path="suite_b",
        created_by="test_user",
        created_at=datetime(2025, 1, 2, tzinfo=UTC),
    )
    await store.save_profile(p1)
    await store.save_profile(p2)

    res = await store.list_profiles(tests_path="suite_a")
    assert len(res) == 1
    assert res[0].id == "p1"


async def test_sqlite_store_lock_run(store: SqliteStore) -> None:
    run = _make_run(id="run-lock-test")
    await store.save(run)

    # By default, run should not be locked
    retrieved = await store.get("run-lock-test")
    assert retrieved.locked is False

    # Lock run
    await store.lock_run("run-lock-test", True)
    retrieved = await store.get("run-lock-test")
    assert retrieved.locked is True

    # Unlock run
    await store.lock_run("run-lock-test", False)
    retrieved = await store.get("run-lock-test")
    assert retrieved.locked is False


async def test_sqlite_store_get_old_unlocked_runs(store: SqliteStore) -> None:
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)

    # 1. Finished, unlocked, 10 days old (should be returned)
    old_run = _make_run(
        id="run-old",
        status=RunStatus.COMPLETED,
        created_at=now - timedelta(days=10),
    )
    old_run = old_run.model_copy(update={"finished_at": now - timedelta(days=10)})
    await store.save(old_run)

    # 2. Finished, locked, 10 days old (should NOT be returned)
    old_locked_run = _make_run(
        id="run-old-locked",
        status=RunStatus.COMPLETED,
        created_at=now - timedelta(days=10),
    )
    old_locked_run = old_locked_run.model_copy(
        update={"finished_at": now - timedelta(days=10), "locked": True}
    )
    await store.save(old_locked_run)
    await store.lock_run("run-old-locked", True)

    # 3. Finished, unlocked, 2 days old (should NOT be returned)
    new_run = _make_run(
        id="run-new",
        status=RunStatus.COMPLETED,
        created_at=now - timedelta(days=2),
    )
    new_run = new_run.model_copy(update={"finished_at": now - timedelta(days=2)})
    await store.save(new_run)

    # Call get_old_unlocked_runs with 7 days retention
    unlocked_old = await store.get_old_unlocked_runs(7)
    assert len(unlocked_old) == 1
    assert unlocked_old[0].id == "run-old"


async def test_sqlite_store_delete_schedule(store: SqliteStore) -> None:
    # Save profile first
    profile = TestProfile(
        id="p-sched-del",
        name="Profile",
        tests_path="tests/",
        created_by="test_user",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_profile(profile)

    # Save schedule
    schedule = TestSchedule(
        id="sched-del",
        name="Nightly Sched",
        profile_id="p-sched-del",
        cron_expression="0 2 * * *",
        enabled=True,
        timezone="UTC",
        created_by="test_user",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_schedule(schedule)

    # Delete non-existent schedule
    res = await store.delete_schedule("nonexistent-sched")
    assert res is False

    # Delete existent schedule
    res = await store.delete_schedule("sched-del")
    assert res is True

    # Verify deleted
    retrieved = await store.get_schedule("sched-del")
    assert retrieved is None


async def test_mark_interrupted_runs(store: SqliteStore) -> None:
    await store.save(_make_run(id="q1", status=RunStatus.QUEUED))
    await store.save(_make_run(id="r1", status=RunStatus.RUNNING))
    await store.save(_make_run(id="c1", status=RunStatus.COMPLETED))
    await store.save(_make_run(id="f1", status=RunStatus.FAILED))

    count = await store.mark_interrupted_runs()
    assert count == 2

    q1 = await store.get("q1")
    assert q1.status == RunStatus.FAILED
    assert q1.error == "interrupted by server restart"
    assert q1.finished_at is not None
    assert (await store.get("r1")).status == RunStatus.FAILED
    # Terminal runs are untouched.
    assert (await store.get("c1")).status == RunStatus.COMPLETED
    assert (await store.get("f1")).status == RunStatus.FAILED


async def test_mark_interrupted_runs_returns_zero_when_none(store: SqliteStore) -> None:
    await store.save(_make_run(id="c1", status=RunStatus.COMPLETED))
    assert await store.mark_interrupted_runs() == 0





