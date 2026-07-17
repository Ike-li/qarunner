"""Tests for SqliteStore adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from qarunner.adapters.sqlite_store import SqliteStore
from qarunner.errors import RunNotFound
from qarunner.models import (
    Credential,
    DiagnosisConfidence,
    FailureDiagnosis,
    ReportRef,
    RootCauseCategory,
    Run,
    RunStatus,
    TestCaseResult,
    TestProfile,
    TestSchedule,
    TestSuite,
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


def _make_suite(**kwargs: object) -> TestSuite:
    defaults: dict[str, object] = {
        "name": "my-e2e",
        "source": "git",
        "repo_url": "https://example.com/r.git",
        "ref": "main",
        "credential_ref": None,
        "created_by": "alice",
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return TestSuite(**defaults)  # type: ignore[arg-type]


async def test_suite_save_get(store: SqliteStore) -> None:
    await store.save_suite(_make_suite())
    got = await store.get_suite("my-e2e")
    assert got is not None
    assert got.source == "git"
    assert got.repo_url == "https://example.com/r.git"
    assert got.ref == "main"
    assert got.created_by == "alice"


async def test_suite_get_missing_returns_none(store: SqliteStore) -> None:
    assert await store.get_suite("nope") is None


async def test_suite_list(store: SqliteStore) -> None:
    await store.save_suite(_make_suite(name="a", source="local", repo_url=None, ref=None))
    await store.save_suite(_make_suite(name="b"))
    names = sorted(s.name for s in await store.list_suites())
    assert names == ["a", "b"]


async def test_suite_save_is_upsert(store: SqliteStore) -> None:
    await store.save_suite(_make_suite(ref="main"))
    await store.save_suite(_make_suite(ref="v2"))
    got = await store.get_suite("my-e2e")
    assert got is not None
    assert got.ref == "v2"
    assert len(await store.list_suites()) == 1


async def test_suite_delete(store: SqliteStore) -> None:
    await store.save_suite(_make_suite())
    assert await store.delete_suite("my-e2e") is True
    assert await store.get_suite("my-e2e") is None
    assert await store.delete_suite("my-e2e") is False


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


async def test_list_default_is_unbounded(store: SqliteStore) -> None:
    # Callers that must see every run (e.g. delete_user's cascade cleanup)
    # rely on list() with no limit= staying unbounded.
    for i in range(3):
        await store.save(_make_run(id=f"run-{i}", created_at=datetime(2025, 1, i + 1, tzinfo=UTC)))
    result = await store.list()
    assert len(result) == 3


async def test_list_respects_limit_newest_first(store: SqliteStore) -> None:
    for i in range(3):
        await store.save(_make_run(id=f"run-{i}", created_at=datetime(2025, 1, i + 1, tzinfo=UTC)))
    result = await store.list(limit=2)
    assert [r.id for r in result] == ["run-2", "run-1"]  # newest 2, DESC order preserved


async def test_list_limit_has_stable_id_tie_break_for_equal_timestamps(
    store: SqliteStore,
) -> None:
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    for run_id in ["run-tie-b", "run-tie-a", "run-tie-c"]:
        await store.save(_make_run(id=run_id, created_at=created_at))

    result = await store.list(limit=2)

    assert [run.id for run in result] == ["run-tie-c", "run-tie-b"]


async def test_save_with_summary(store: SqliteStore) -> None:
    summary = TestSummary(total=10, passed=8, failed=1, skipped=1, error=0, duration_ms=5000)
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


async def test_credential_crud(store: SqliteStore) -> None:
    cred = Credential(
        id="cred-1",
        name="gh-token",
        type="https_token",
        created_by="alice",
        created_at=datetime(2026, 6, 20, tzinfo=UTC),
    )
    # The encrypted secret is stored alongside the metadata but never lives on
    # the model — so a serialised Credential can't leak it.
    await store.save_credential(cred, "ENC(token-payload)")
    assert not hasattr(cred, "secret")

    got = await store.get_credential("cred-1")
    assert got is not None
    assert got.name == "gh-token"
    assert got.type == "https_token"
    assert got.created_by == "alice"

    # The ciphertext is fetched only via the dedicated secret accessor.
    assert await store.get_credential_secret("cred-1") == "ENC(token-payload)"

    assert [c.id for c in await store.list_credentials()] == ["cred-1"]

    # Misses report None rather than raising.
    assert await store.get_credential("ghost") is None
    assert await store.get_credential_secret("ghost") is None

    # Delete removes it; deleting again is a no-op.
    assert await store.delete_credential("cred-1") is True
    assert await store.get_credential("cred-1") is None
    assert await store.delete_credential("cred-1") is False


async def test_user_delete_and_update(store: SqliteStore) -> None:
    await store.create_user("dave", "h1", "user")

    # Updates against an existing row report a hit and persist.
    assert await store.update_password("dave", "h2") is True
    assert await store.update_role("dave", "admin") is True
    user = await store.get_user("dave")
    assert user is not None
    assert user["password_hash"] == "h2"
    assert user["role"] == "admin"

    # Updates against a missing user report no hit.
    assert await store.update_password("ghost", "x") is False
    assert await store.update_role("ghost", "admin") is False

    # Delete removes the row; deleting again is a no-op.
    assert await store.delete_user("dave") is True
    assert await store.get_user("dave") is None
    assert await store.delete_user("dave") is False


async def test_demote_if_not_last_admin_no_op_when_sole_admin(store: SqliteStore) -> None:
    """BUG-6: the seeded default admin is the only admin — demoting it must
    be refused so the platform can't lock itself out of every admin route."""
    admins = [u for u in await store.list_users() if u["role"] == "admin"]
    assert len(admins) == 1
    sole_admin = admins[0]["username"]

    demoted = await store.demote_if_not_last_admin(sole_admin, "user")

    assert demoted is False
    assert (await store.get_user(sole_admin))["role"] == "admin"


async def test_demote_if_not_last_admin_succeeds_with_second_admin(store: SqliteStore) -> None:
    await store.create_user("second-admin", "hashed", "admin")

    demoted = await store.demote_if_not_last_admin("second-admin", "user")

    assert demoted is True
    assert (await store.get_user("second-admin"))["role"] == "user"


async def test_cancel_if_inflight_transitions_queued_run(store: SqliteStore) -> None:
    await store.save(_make_run(id="run-inflight", status=RunStatus.QUEUED))

    changed = await store.cancel_if_inflight("run-inflight", "2025-01-01T00:00:05+00:00")

    assert changed is True
    assert (await store.get("run-inflight")).status == RunStatus.CANCELLED


async def test_cancel_if_inflight_is_no_op_on_terminal_run(store: SqliteStore) -> None:
    """A run that already finished (COMPLETED/FAILED/TIMEOUT) must not be
    silently overwritten with CANCELLED by a late cancel request."""
    await store.save(_make_run(id="run-done", status=RunStatus.COMPLETED))

    changed = await store.cancel_if_inflight("run-done", "2025-01-01T00:00:05+00:00")

    assert changed is False
    assert (await store.get("run-done")).status == RunStatus.COMPLETED


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


async def test_profile_list_has_stable_id_tie_break_for_equal_timestamps(
    store: SqliteStore,
) -> None:
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    for suffix in ["b", "a", "c"]:
        await store.save_profile(
            TestProfile(
                id=f"profile-tie-{suffix}",
                name=f"Profile {suffix}",
                tests_path="tests/tie",
                created_by="alice",
                created_at=created_at,
            )
        )

    expected = ["profile-tie-c", "profile-tie-b", "profile-tie-a"]

    assert [profile.id for profile in await store.list_profiles()] == expected
    assert [
        profile.id for profile in await store.list_profiles(tests_path="tests/tie")
    ] == expected


async def test_ai_diagnosis_roundtrip_upsert_and_cascade(store: SqliteStore) -> None:
    run = _make_run(id="run-ai")
    await store.save(run)
    assert await store.get_ai_diagnosis("run-ai") is None

    diagnosis = FailureDiagnosis(
        category=RootCauseCategory.ASSERTION,
        confidence=DiagnosisConfidence.HIGH,
        summary="an assertion failed",
        evidence=["expected 200"],
        is_likely_regression=True,
    )
    await store.save_ai_diagnosis(
        "run-ai", diagnosis, "anthropic", "claude-x", datetime(2026, 1, 1, tzinfo=UTC)
    )
    got = await store.get_ai_diagnosis("run-ai")
    assert got is not None
    assert got.category == RootCauseCategory.ASSERTION
    assert got.is_likely_regression is True
    assert got.evidence == ["expected 200"]

    # Upsert: re-generating replaces rather than duplicating.
    await store.save_ai_diagnosis(
        "run-ai",
        diagnosis.model_copy(update={"summary": "revised"}),
        "openai",
        "gpt-x",
        datetime(2026, 1, 2, tzinfo=UTC),
    )
    got2 = await store.get_ai_diagnosis("run-ai")
    assert got2 is not None
    assert got2.summary == "revised"

    # FK ON DELETE CASCADE: deleting the run drops its cached diagnosis.
    await store.delete_run("run-ai")
    assert await store.get_ai_diagnosis("run-ai") is None


async def test_get_ai_diagnosis_corrupt_json_returns_none(store: SqliteStore) -> None:
    """A corrupt cache row must degrade to None (never 500 the GET endpoint)."""
    run = _make_run(id="run-corrupt")
    await store.save(run)
    async with store._connect() as db:
        await db.execute(
            "INSERT INTO run_ai_diagnosis "
            "(run_id, diagnosis_json, provider, model, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("run-corrupt", "{not-valid-json", "anthropic", "m", "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()
    assert await store.get_ai_diagnosis("run-corrupt") is None


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


async def test_schedule_list_has_stable_id_tie_break_for_equal_timestamps(
    store: SqliteStore,
) -> None:
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    profile = TestProfile(
        id="schedule-tie-profile",
        name="Schedule Tie Profile",
        tests_path="tests/tie",
        created_by="alice",
        created_at=created_at,
    )
    await store.save_profile(profile)
    for suffix in ["b", "a", "c"]:
        await store.save_schedule(
            TestSchedule(
                id=f"schedule-tie-{suffix}",
                name=f"Schedule {suffix}",
                profile_id=profile.id,
                cron_expression="0 1 * * *",
                created_by="alice",
                created_at=created_at,
            )
        )

    expected = ["schedule-tie-c", "schedule-tie-b", "schedule-tie-a"]

    assert [schedule.id for schedule in await store.list_schedules()] == expected
    assert [
        schedule.id for schedule in await store.list_schedules(profile_id=profile.id)
    ] == expected


async def test_updating_a_profile_keeps_its_schedules(store: SqliteStore) -> None:
    """Re-saving a profile (the update path) must NOT drop its schedules.

    ``save_profile`` serves both create and update. A plain ``INSERT OR REPLACE``
    deletes the existing profile row before re-inserting, and the
    ``test_schedules`` FK ``ON DELETE CASCADE`` then silently removes every bound
    schedule — so merely renaming a profile wiped all its automation. A
    row-preserving upsert keeps the schedules; the cascade must fire only on a
    real ``delete_profile`` (covered above).
    """
    profile = TestProfile(
        id="profile-keep",
        name="original",
        tests_path="tests/",
        created_by="alice",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_profile(profile)
    schedule = TestSchedule(
        id="sched-keep",
        name="Nightly",
        profile_id="profile-keep",
        cron_expression="0 2 * * *",
        created_by="alice",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_schedule(schedule)
    assert await store.get_schedule("sched-keep") is not None

    # Update the profile in place (same id, new name).
    await store.save_profile(profile.model_copy(update={"name": "renamed"}))

    updated = await store.get_profile("profile-keep")
    assert updated is not None
    assert updated.name == "renamed"
    # The schedule must survive a profile update.
    assert await store.get_schedule("sched-keep") is not None


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


async def _user_version(store: SqliteStore) -> int:
    async with store._connect() as db, db.execute("PRAGMA user_version") as cursor:
        rows = await cursor.fetchall()
    return rows[0][0]


async def test_fresh_initialize_records_baseline_version(tmp_path) -> None:
    """ARCH-8: a fresh database is stamped with the baseline schema version."""
    from qarunner.adapters.sqlite_store import _BASELINE_VERSION, _MIGRATIONS

    store = SqliteStore(str(tmp_path / "versioned.db"))
    await store.initialize()
    expected_version = max([_BASELINE_VERSION] + [ver for ver, _ in _MIGRATIONS])
    assert await _user_version(store) == expected_version
    await store.close()


async def test_forward_migration_runs_once_and_records_version(tmp_path, monkeypatch) -> None:
    """ARCH-8: a versioned forward migration applies exactly once and bumps
    PRAGMA user_version, so re-initialising is a no-op rather than a crash.

    Without the version gate the ALTER would re-run on the second initialize and
    raise "duplicate column name"; the recorded version is what prevents it.
    """
    from qarunner.adapters import sqlite_store

    # Register a synthetic v2 migration beyond the v1 baseline.
    monkeypatch.setattr(
        sqlite_store,
        "_MIGRATIONS",
        ((2, ("ALTER TABLE runs ADD COLUMN arch8_probe TEXT NOT NULL DEFAULT 'x'",)),),
    )

    store = SqliteStore(str(tmp_path / "versioned.db"))
    await store.initialize()  # baseline (v1) then the forward migration (v2)

    async def _run_columns() -> list[str]:
        async with store._connect() as db, db.execute("PRAGMA table_info(runs)") as cursor:
            return [row[1] for row in await cursor.fetchall()]

    assert "arch8_probe" in await _run_columns()
    assert await _user_version(store) == 2

    # Re-initialise: the migration is version-gated, so the ALTER does not re-run
    # (a duplicate-column ALTER would raise) and the version is unchanged.
    await store.initialize()
    assert (await _run_columns()).count("arch8_probe") == 1
    assert await _user_version(store) == 2


async def test_migration_tolerates_legacy_preexisting_column(tmp_path) -> None:
    """ARCH-8: a legacy unversioned DB (user_version=0) may already carry a column
    a forward migration adds (its old per-startup ALTER scheme added it before
    versioning). The migration must tolerate the duplicate rather than crashing
    initialize with 'duplicate column name', which would brick startup."""
    import aiosqlite

    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as db:
        # Full baseline columns PLUS worker_node_id (added by migration 2).
        await db.execute(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, runner TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT 'system', tests_path TEXT NOT NULL,
                args_json TEXT NOT NULL DEFAULT '[]',
                allure_enabled INTEGER NOT NULL DEFAULT 1, timeout INTEGER,
                executor_mode TEXT NOT NULL DEFAULT 'subprocess', summary_json TEXT,
                report_json TEXT, exit_code INTEGER, error TEXT,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                env_json TEXT NOT NULL DEFAULT '{}', locked INTEGER NOT NULL DEFAULT 0,
                worker_node_id TEXT
            )
            """
        )
        await db.commit()

    store = SqliteStore(db_path)
    await store.initialize()  # must NOT raise duplicate-column

    from qarunner.adapters.sqlite_store import _BASELINE_VERSION, _MIGRATIONS

    expected = max([_BASELINE_VERSION] + [v for v, _ in _MIGRATIONS])
    assert await _user_version(store) == expected  # version still advanced past 2
    await store.close()


async def test_migration_reraises_non_duplicate_error(tmp_path, monkeypatch) -> None:
    """The duplicate-column tolerance must not swallow a genuine migration failure
    (e.g. a DDL against a missing table); such errors still propagate."""
    import aiosqlite

    from qarunner.adapters import sqlite_store

    monkeypatch.setattr(
        sqlite_store,
        "_MIGRATIONS",
        ((2, ("ALTER TABLE no_such_table ADD COLUMN x TEXT",)),),
    )
    store = SqliteStore(str(tmp_path / "versioned.db"))
    with pytest.raises(aiosqlite.OperationalError):
        await store.initialize()
    await store.close()

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


async def test_sqlite_store_delete_run(store: SqliteStore) -> None:
    run = _make_run(id="run-del")
    await store.save(run)

    # Deleting an existing run removes the row and reports a hit.
    assert await store.delete_run("run-del") is True
    with pytest.raises(RunNotFound):
        await store.get("run-del")

    # Deleting an already-absent run is a no-op that reports no hit.
    assert await store.delete_run("run-del") is False


async def test_save_does_not_clobber_concurrent_lock(store: SqliteStore) -> None:
    """A lifecycle save() must not overwrite a lock toggled meanwhile (BUG-4).

    ``orchestrator.execute`` reads a run at start (locked=False), holds it in
    memory, and re-saves the whole row at the terminal state. If a
    ``PUT /runs/{id}/lock`` flipped ``locked`` via its targeted UPDATE in
    between, a full-row INSERT OR REPLACE would clobber it back to False —
    silently unprotecting the run from cleanup deletion. ``save`` must preserve
    the stored ``locked`` (only creation and ``lock_run`` may set it).
    """
    run = _make_run(id="run-lock-race")
    assert run.locked is False
    await store.save(run)  # initial insert, as create() does

    # A concurrent lock arrives while execute still holds locked=False in memory.
    await store.lock_run("run-lock-race", True)

    # execute re-saves its stale in-memory run at the terminal state.
    await store.save(run.model_copy(update={"status": RunStatus.COMPLETED}))

    retrieved = await store.get("run-lock-race")
    assert retrieved.status == RunStatus.COMPLETED
    assert retrieved.locked is True  # the lock survived the lifecycle save


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


async def test_cleanup_claim_and_user_lock_are_mutually_exclusive(store: SqliteStore) -> None:
    now = datetime.now(UTC)
    run = _make_run(
        id="run-cleanup-claim",
        status=RunStatus.COMPLETED,
        created_at=now - timedelta(days=10),
    ).model_copy(update={"finished_at": now - timedelta(days=10)})
    await store.save(run)

    assert await store.claim_run_cleanup(run.id, retention_days=7) is True
    assert await store.claim_run_cleanup(run.id, retention_days=7) is False
    assert await store.lock_run(run.id, True) is False

    await store.finish_run_cleanup(run.id, cleaned=False)

    assert await store.lock_run(run.id, True) is True
    assert await store.claim_run_cleanup(run.id, retention_days=7) is False


async def test_finish_cleanup_narrowly_updates_report_and_releases_claim(
    store: SqliteStore,
) -> None:
    now = datetime.now(UTC)
    report = ReportRef(
        allure_results_dir="results", allure_report_file="report/index.html", html_generated=True
    )
    run = _make_run(
        id="run-cleanup-finish",
        status=RunStatus.COMPLETED,
        created_at=now - timedelta(days=10),
    ).model_copy(
        update={
            "finished_at": now - timedelta(days=10),
            "report": report,
            "error": "preserve-me",
        }
    )
    await store.save(run)

    assert await store.claim_run_cleanup(run.id, retention_days=7) is True
    await store.finish_run_cleanup(run.id, cleaned=False)
    assert (await store.get(run.id)).report == report
    assert await store.claim_run_cleanup(run.id, retention_days=7) is True

    await store.finish_run_cleanup(run.id, cleaned=True)

    persisted = await store.get(run.id)
    assert persisted.report is None
    assert persisted.error == "preserve-me"
    assert persisted.finished_at == run.finished_at
    assert await store.lock_run(run.id, True) is True


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
    assert count == 1  # only RUNNING (QUEUED survives restart)

    q1 = await store.get("q1")
    assert q1.status == RunStatus.QUEUED  # QUEUED survives restart (persistent queue)
    assert (await store.get("r1")).status == RunStatus.FAILED
    # Terminal runs are untouched.
    assert (await store.get("c1")).status == RunStatus.COMPLETED
    assert (await store.get("f1")).status == RunStatus.FAILED


async def test_mark_interrupted_runs_returns_zero_when_none(store: SqliteStore) -> None:
    await store.save(_make_run(id="c1", status=RunStatus.COMPLETED))
    assert await store.mark_interrupted_runs() == 0


async def test_mark_interrupted_runs_with_worker_node_id(store: SqliteStore) -> None:
    await store.save(_make_run(id="q1", status=RunStatus.QUEUED, worker_node_id="node-a"))
    await store.save(_make_run(id="q2", status=RunStatus.QUEUED, worker_node_id="node-b"))
    await store.save(_make_run(id="r1", status=RunStatus.RUNNING, worker_node_id="node-a"))
    await store.save(_make_run(id="r2", status=RunStatus.RUNNING, worker_node_id="node-b"))

    # Fail runs on node-a only
    count = await store.mark_interrupted_runs(worker_node_id="node-a")
    assert count == 1  # only RUNNING r1 (QUEUED q1 survives)

    # Verify node-a RUNNING became FAILED; QUEUED survives
    assert (await store.get("q1")).status == RunStatus.QUEUED  # survives restart
    assert (await store.get("r1")).status == RunStatus.FAILED

    # Verify node-b runs are unaffected
    assert (await store.get("q2")).status == RunStatus.QUEUED
    assert (await store.get("r2")).status == RunStatus.RUNNING


async def test_claim_schedule_run_leader_election(store: SqliteStore) -> None:
    """CONC-2: only the first caller for a given cron tick wins the claim."""
    from datetime import timedelta

    profile = TestProfile(
        id="p-claim",
        name="P",
        tests_path="tests/",
        created_by="u",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_profile(profile)
    schedule = TestSchedule(
        id="s-claim",
        name="S",
        profile_id="p-claim",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="u",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_schedule(schedule)

    tick = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    # First replica wins this tick; a second firing for the same tick loses.
    assert await store.claim_schedule_run("s-claim", tick) is True
    assert await store.claim_schedule_run("s-claim", tick) is False
    # An earlier tick (e.g. backward clock skew) also loses.
    assert await store.claim_schedule_run("s-claim", tick - timedelta(minutes=5)) is False
    # The next tick wins again.
    assert await store.claim_schedule_run("s-claim", tick + timedelta(minutes=5)) is True

    # last_run_at reflects the latest claimed tick, normalised to UTC.
    reloaded = await store.get_schedule("s-claim")
    assert reloaded is not None
    assert reloaded.last_run_at == tick + timedelta(minutes=5)


async def test_claim_schedule_run_unknown_schedule(store: SqliteStore) -> None:
    """CONC-2: claiming a schedule that does not exist updates nothing and loses."""
    tick = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert await store.claim_schedule_run("does-not-exist", tick) is False


async def test_save_schedule_preserves_concurrent_last_run_at(store: SqliteStore) -> None:
    """CONC-2/BUG-4: a full-row save (e.g. a PUT /schedules update re-writing a
    snapshot read before a cron tick) must NOT clobber last_run_at advanced
    concurrently by claim_schedule_run. A clobber rolls the claim back, so the
    same tick gets executed again on another replica."""
    profile = TestProfile(
        id="p-pres",
        name="P",
        tests_path="tests/",
        created_by="u",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_profile(profile)
    schedule = TestSchedule(
        id="s-pres",
        name="S",
        profile_id="p-pres",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        last_run_at=None,
        created_by="u",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_schedule(schedule)

    # A cron tick is claimed: leader election advances last_run_at to the tick.
    tick = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert await store.claim_schedule_run("s-pres", tick) is True

    # An update path re-saves a pre-claim snapshot (last_run_at still None) with
    # an unrelated field changed.
    stale_update = TestSchedule(
        id="s-pres",
        name="S-renamed",
        profile_id="p-pres",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        last_run_at=None,
        created_by="u",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await store.save_schedule(stale_update)

    reloaded = await store.get_schedule("s-pres")
    assert reloaded is not None
    assert reloaded.name == "S-renamed"  # the row-update is applied...
    assert reloaded.last_run_at == tick  # ...but the concurrent claim survives


async def test_count_inflight_runs(store: SqliteStore) -> None:
    """P2-7: count only queued/running runs for the given user — the basis for
    the per-user in-flight rate limit. Finished runs and other users don't count."""
    await store.save(_make_run(id="q", status=RunStatus.QUEUED, created_by="alice"))
    await store.save(_make_run(id="r", status=RunStatus.RUNNING, created_by="alice"))
    await store.save(_make_run(id="done", status=RunStatus.COMPLETED, created_by="alice"))
    await store.save(_make_run(id="other", status=RunStatus.RUNNING, created_by="bob"))

    assert await store.count_inflight_runs("alice") == 2
    assert await store.count_inflight_runs("bob") == 1
    assert await store.count_inflight_runs("nobody") == 0


async def test_concurrent_inflight_limited_creates_respect_owner_cap(
    store: SqliteStore,
) -> None:
    base = _make_run(id="limited-0", created_by="alice")

    created = await asyncio.gather(
        *(
            store.create_if_below_inflight_limit(
                base.model_copy(update={"id": f"limited-{index}"}),
                2,
            )
            for index in range(5)
        )
    )

    assert sum(created) == 2
    assert await store.count_inflight_runs("alice") == 2


async def test_enable_wal_tolerates_concurrent_lock() -> None:
    """CONC-2: a 'database is locked' during WAL conversion is tolerated, not raised."""
    from sqlite3 import OperationalError
    from unittest.mock import AsyncMock, MagicMock

    db = MagicMock()
    db.execute = AsyncMock(side_effect=OperationalError("database is locked"))
    await SqliteStore._enable_wal(db)  # another initializer is converting; must not raise
    db.execute.assert_awaited_once()


async def test_enable_wal_reraises_non_lock_error() -> None:
    """CONC-2: a genuine (non-lock) failure during WAL conversion is re-raised."""
    from sqlite3 import OperationalError
    from unittest.mock import AsyncMock, MagicMock

    db = MagicMock()
    db.execute = AsyncMock(side_effect=OperationalError("disk I/O error"))
    with pytest.raises(OperationalError):
        await SqliteStore._enable_wal(db)


async def test_safe_alter_tolerates_existing_column(tmp_path) -> None:
    """CONC-2: a concurrent first-start ALTER race is benign when the column exists."""
    import aiosqlite

    db_path = str(tmp_path / "race.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE t (id TEXT, foo TEXT)")
        await db.commit()
        # ALTER fails (duplicate column) but "foo" already exists -> tolerated.
        await SqliteStore._safe_alter(db, "t", "foo", "ALTER TABLE t ADD COLUMN foo TEXT")
        async with db.execute("PRAGMA table_info(t)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
    assert "foo" in cols


async def test_safe_alter_reraises_genuine_failure(tmp_path) -> None:
    """CONC-2: a failure that does not yield the target column is re-raised."""
    import aiosqlite

    db_path = str(tmp_path / "race2.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE t (id TEXT)")
        await db.commit()
        # ALTER errors (duplicate "id"), and the claimed column "bar" never appears.
        with pytest.raises(aiosqlite.OperationalError):
            await SqliteStore._safe_alter(db, "t", "bar", "ALTER TABLE t ADD COLUMN id TEXT")


async def test_concurrent_initialize_seeds_one_admin(tmp_path) -> None:
    """CONC-2: two processes initialising the same empty DB at once do not crash."""
    import asyncio

    db_path = str(tmp_path / "concurrent.db")
    s1 = SqliteStore(db_path)
    s2 = SqliteStore(db_path)
    # INSERT OR IGNORE + ALTER-race tolerance keep the concurrent first-start safe.
    await asyncio.gather(s1.initialize(), s2.initialize())
    try:
        users = await s1.list_users()
        admins = [u for u in users if u["username"] == "admin"]
        assert len(admins) == 1
    finally:
        await s1.close()
        await s2.close()


def test_row_to_profile_legacy() -> None:
    from qarunner.adapters.sqlite_store import _row_to_profile

    # A fake 12-column row resembling a legacy database row:
    # id, name, description, tests_path, selected_files, selected_markers,
    # extra_args, executor_mode, timeout, created_by, created_at, env_json
    fake_row = (
        "id-123",  # 0: id
        "Legacy Profile",  # 1: name
        "Desc",  # 2: description
        "suite_a",  # 3: tests_path
        '["f1.py"]',  # 4: selected_files
        '["m1"]',  # 5: selected_markers
        "--args",  # 6: extra_args
        "subprocess",  # 7: executor_mode
        100,  # 8: timeout
        "user1",  # 9: created_by
        "2026-06-24T12:00:00Z",  # 10: created_at
        '{"ENV_VAR": "val"}',  # 11: env_json
    )
    profile = _row_to_profile(fake_row)
    assert profile.id == "id-123"
    assert profile.runner == "pytest"  # Default value for legacy row
    assert profile.selected_files == ["f1.py"]
    assert profile.selected_markers == ["m1"]
    assert profile.env == {"ENV_VAR": "val"}


async def test_save_cases_round_trip(store: SqliteStore) -> None:
    run = _make_run(id="run-cases")
    await store.save(run)
    cases = [
        TestCaseResult(suite="s", name="t1", status="passed", duration_ms=5),
        TestCaseResult(suite="s", name="t2", status="failed", duration_ms=9, message="err"),
    ]
    await store.save_cases("run-cases", "tests/", run.created_at, cases)

    got = await store.get_cases_for_run("run-cases")
    assert [c.name for c in got] == ["t1", "t2"]
    assert got[1].status == "failed"
    assert got[1].message == "err"
    assert got[0].message is None  # passed case carries no message


async def test_save_cases_is_idempotent(store: SqliteStore) -> None:
    run = _make_run(id="run-idem")
    await store.save(run)
    cases = [TestCaseResult(suite="s", name="t1", status="passed", duration_ms=1)]
    await store.save_cases("run-idem", "tests/", run.created_at, cases)
    await store.save_cases("run-idem", "tests/", run.created_at, cases)  # re-persist

    got = await store.get_cases_for_run("run-idem")
    assert len(got) == 1  # cleared before reinsert — no duplicate rows


async def test_save_cases_truncates_long_message(store: SqliteStore) -> None:
    from qarunner.adapters.sqlite_store import _MAX_CASE_MESSAGE_CHARS

    run = _make_run(id="run-trunc")
    await store.save(run)
    huge = "x" * (_MAX_CASE_MESSAGE_CHARS + 5000)
    cases = [TestCaseResult(suite="s", name="t", status="failed", duration_ms=1, message=huge)]
    await store.save_cases("run-trunc", "tests/", run.created_at, cases)

    got = await store.get_cases_for_run("run-trunc")
    assert got[0].message is not None
    assert len(got[0].message) == _MAX_CASE_MESSAGE_CHARS


async def test_save_cases_empty_list(store: SqliteStore) -> None:
    run = _make_run(id="run-empty")
    await store.save(run)
    await store.save_cases("run-empty", "tests/", run.created_at, [])
    assert await store.get_cases_for_run("run-empty") == []


async def test_get_case_history_oldest_first(store: SqliteStore) -> None:
    for i, st in enumerate(["passed", "failed", "passed"]):
        run = _make_run(
            id=f"h{i}",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=datetime(2025, 1, i + 1, tzinfo=UTC),
        )
        await store.save(run)
        await store.save_cases(
            f"h{i}",
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status=st, duration_ms=0)],
        )
    hist = await store.get_case_history("suite_a", "s", "t")
    assert [p.status for p in hist] == ["passed", "failed", "passed"]  # oldest-first


async def test_get_case_history_owner_scoped(store: SqliteStore) -> None:
    for rid, owner in [("ha", "alice"), ("hb", "bob")]:
        run = _make_run(
            id=rid,
            tests_path="suite_a",
            created_by=owner,
            status=RunStatus.COMPLETED,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        await store.save(run)
        await store.save_cases(
            rid,
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status="passed", duration_ms=0)],
        )
    # admin (created_by=None) spans both owners; alice sees only her own run.
    assert len(await store.get_case_history("suite_a", "s", "t")) == 2
    assert len(await store.get_case_history("suite_a", "s", "t", created_by="alice")) == 1


async def test_get_case_history_profile_scoped(store: SqliteStore) -> None:
    for rid, profile_id, status, day in [
        ("pa-old", "profile-a", "passed", 1),
        ("pb", "profile-b", "failed", 2),
        ("pa-new", "profile-a", "passed", 3),
    ]:
        run = _make_run(
            id=rid,
            tests_path="suite_a",
            profile_id=profile_id,
            status=RunStatus.COMPLETED,
            created_at=datetime(2025, 1, day, tzinfo=UTC),
        )
        await store.save(run)
        await store.save_cases(
            rid,
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status=status, duration_ms=0)],
        )

    hist = await store.get_case_history("suite_a", "s", "t", profile_id="profile-a")
    assert [p.status for p in hist] == ["passed", "passed"]


async def test_get_case_histories_batches_multiple_cases_one_connection(
    store: SqliteStore,
) -> None:
    """PERF: create_ai_analysis looped get_case_history per failed case, each
    opening its own connection (N+1). get_case_histories answers every case
    in one connection instead."""
    for i, (suite, name, st) in enumerate(
        [("s1", "t1", "failed"), ("s1", "t1", "passed"), ("s2", "t2", "failed")]
    ):
        run = _make_run(
            id=f"h{i}",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=datetime(2025, 1, i + 1, tzinfo=UTC),
        )
        await store.save(run)
        await store.save_cases(
            f"h{i}",
            "suite_a",
            run.created_at,
            [TestCaseResult(suite=suite, name=name, status=st, duration_ms=0)],
        )

    result = await store.get_case_histories("suite_a", [("s1", "t1"), ("s2", "t2")])

    assert [p.status for p in result[("s1", "t1")]] == ["failed", "passed"]  # oldest-first
    assert [p.status for p in result[("s2", "t2")]] == ["failed"]


async def test_get_case_histories_owner_and_profile_scoped(store: SqliteStore) -> None:
    for rid, owner, profile_id in [("ha", "alice", "prof-a"), ("hb", "bob", "prof-b")]:
        run = _make_run(
            id=rid,
            tests_path="suite_a",
            created_by=owner,
            profile_id=profile_id,
            status=RunStatus.COMPLETED,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        await store.save(run)
        await store.save_cases(
            rid,
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status="passed", duration_ms=0)],
        )

    admin_view = await store.get_case_histories("suite_a", [("s", "t")])
    assert len(admin_view[("s", "t")]) == 2

    alice_view = await store.get_case_histories("suite_a", [("s", "t")], created_by="alice")
    assert len(alice_view[("s", "t")]) == 1

    profile_a_view = await store.get_case_histories("suite_a", [("s", "t")], profile_id="prof-a")
    assert len(profile_a_view[("s", "t")]) == 1


async def test_get_case_histories_empty_cases_returns_empty_dict(store: SqliteStore) -> None:
    assert await store.get_case_histories("suite_a", []) == {}


async def test_cases_cascade_deleted_with_run(store: SqliteStore) -> None:
    run = _make_run(id="run-cascade")
    await store.save(run)
    await store.save_cases(
        "run-cascade",
        "tests/",
        run.created_at,
        [TestCaseResult(suite="s", name="t", status="passed", duration_ms=1)],
    )
    assert len(await store.get_cases_for_run("run-cascade")) == 1

    # FK ON DELETE CASCADE: deleting the run drops its cases automatically.
    await store.delete_run("run-cascade")
    assert await store.get_cases_for_run("run-cascade") == []


async def test_count_flaky_tests_detects_flips(store: SqliteStore) -> None:
    now = datetime.now(UTC)
    # 4 runs with alternating pass/fail for the same test case → flaky (3 flips).
    for i, st in enumerate(["passed", "failed", "passed", "failed"]):
        run = _make_run(
            id=f"fl{i}",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=now - timedelta(hours=4 - i),
        )
        await store.save(run)
        await store.save_cases(
            f"fl{i}",
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status=st, duration_ms=0)],
        )
    assert await store.count_flaky_tests(days=30) == 1


async def test_count_flaky_tests_ignores_two_flip_regression_fix(
    store: SqliteStore,
) -> None:
    now = datetime.now(UTC)
    for i, st in enumerate(["passed", "failed", "passed"]):
        run = _make_run(
            id=f"tw{i}",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=now - timedelta(hours=3 - i),
        )
        await store.save(run)
        await store.save_cases(
            f"tw{i}",
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status=st, duration_ms=0)],
        )
    assert await store.count_flaky_tests(days=30) == 0


async def test_count_flaky_tests_ignores_stable(store: SqliteStore) -> None:
    now = datetime.now(UTC)
    for i, st in enumerate(["passed", "passed", "passed"]):
        run = _make_run(
            id=f"st{i}",
            tests_path="suite_a",
            status=RunStatus.COMPLETED,
            created_at=now - timedelta(hours=3 - i),
        )
        await store.save(run)
        await store.save_cases(
            f"st{i}",
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status=st, duration_ms=0)],
        )
    assert await store.count_flaky_tests(days=30) == 0


async def test_count_flaky_tests_owner_scoped(store: SqliteStore) -> None:
    now = datetime.now(UTC)
    # alice's test: flaky; bob's test: stable.
    for i, (owner, st) in enumerate(
        [
            ("alice", "passed"),
            ("alice", "failed"),
            ("alice", "passed"),
            ("alice", "failed"),
        ]
    ):
        run = _make_run(
            id=f"own{i}",
            tests_path="suite_a",
            created_by=owner,
            status=RunStatus.COMPLETED,
            created_at=now - timedelta(hours=3 - i),
        )
        await store.save(run)
        await store.save_cases(
            f"own{i}",
            "suite_a",
            run.created_at,
            [TestCaseResult(suite="s", name="t", status=st, duration_ms=0)],
        )
    run_bob = _make_run(
        id="bob0",
        tests_path="suite_a",
        created_by="bob",
        status=RunStatus.COMPLETED,
        created_at=now - timedelta(hours=1),
    )
    await store.save(run_bob)
    await store.save_cases(
        "bob0",
        "suite_a",
        run_bob.created_at,
        [TestCaseResult(suite="s", name="t", status="passed", duration_ms=0)],
    )
    # Admin sees all → 1 flaky (alice's).
    assert await store.count_flaky_tests(days=30) == 1
    # alice scope → 1 flaky.
    assert await store.count_flaky_tests(days=30, created_by="alice") == 1
    # bob scope → 0 flaky.
    assert await store.count_flaky_tests(days=30, created_by="bob") == 0


# ── BUG regression: list() and get_old_unlocked_runs() missing profile_id ────


async def test_list_preserves_profile_id(store: SqliteStore) -> None:
    """Regression guard: list() must include profile_id in its SELECT.

    An earlier version of this query missed the column; the fix (v8 migration)
    added ``profile_id`` to the SELECT.  This test ensures it doesn't regress.
    """
    run = _make_run(id="run-with-profile", profile_id="prof-abc")
    await store.save(run)

    # get() has profile_id — this already works.
    direct = await store.get("run-with-profile")
    assert direct.profile_id == "prof-abc", (
        f"get() should return profile_id='prof-abc', got {direct.profile_id!r}"
    )

    # list() must also preserve profile_id (was missing before v8 migration fix).
    listed = await store.list()
    listed_run = next(r for r in listed if r.id == "run-with-profile")
    assert listed_run.profile_id == "prof-abc", (
        f"list() returned profile_id={listed_run.profile_id!r}, expected 'prof-abc'"
    )


async def test_get_old_unlocked_runs_preserves_profile_id(store: SqliteStore) -> None:
    """BUG: get_old_unlocked_runs() SELECT also misses the profile_id column."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    run = _make_run(
        id="run-old-prof",
        profile_id="prof-xyz",
        status=RunStatus.COMPLETED,
        created_at=now - timedelta(days=30),
    )
    run = run.model_copy(update={"finished_at": now - timedelta(days=30)})
    await store.save(run)

    old_runs = await store.get_old_unlocked_runs(7)
    old_run = next(r for r in old_runs if r.id == "run-old-prof")
    assert old_run.profile_id == "prof-xyz", (
        f"BUG: get_old_unlocked_runs() returned profile_id={old_run.profile_id!r}, "
        f"expected 'prof-xyz'. SELECT is missing the profile_id column."
    )


# ── dequeue_next_queued ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dequeue_next_queued_returns_oldest_fifo(store: SqliteStore) -> None:
    """Dequeue picks the oldest QUEUED run by created_at (FIFO)."""
    now = datetime.now(UTC)
    run_a = _make_run(id="a", status=RunStatus.QUEUED, created_at=now)
    run_b = _make_run(id="b", status=RunStatus.QUEUED, created_at=now + timedelta(seconds=1))
    await store.save(run_a)
    await store.save(run_b)

    first = await store.dequeue_next_queued()
    assert first == "a", f"expected oldest run 'a', got {first!r}"
    second = await store.dequeue_next_queued()
    assert second == "b", f"expected second run 'b', got {second!r}"


@pytest.mark.asyncio
async def test_dequeue_next_queued_marks_running(store: SqliteStore) -> None:
    """Dequeued run has status RUNNING and a started_at timestamp."""
    run = _make_run(id="r1", status=RunStatus.QUEUED)
    await store.save(run)

    run_id = await store.dequeue_next_queued()
    assert run_id == "r1"

    fetched = await store.get(run_id)
    assert fetched.status == RunStatus.RUNNING
    assert fetched.started_at is not None


@pytest.mark.asyncio
async def test_dequeue_next_queued_returns_none_when_empty(store: SqliteStore) -> None:
    """Empty queue → None."""
    assert await store.dequeue_next_queued() is None


@pytest.mark.asyncio
async def test_dequeue_skips_cancelled(store: SqliteStore) -> None:
    """CANCELLED runs are not picked up by dequeue."""
    run_cancelled = _make_run(id="c1", status=RunStatus.CANCELLED)
    run_queued = _make_run(id="q1", status=RunStatus.QUEUED)
    await store.save(run_cancelled)
    await store.save(run_queued)

    run_id = await store.dequeue_next_queued()
    assert run_id == "q1", f"should skip cancelled, got {run_id!r}"


@pytest.mark.asyncio
async def test_dequeue_skips_completed(store: SqliteStore) -> None:
    """COMPLETED runs are not picked up by dequeue."""
    run_done = _make_run(id="d1", status=RunStatus.COMPLETED)
    run_queued = _make_run(id="q2", status=RunStatus.QUEUED)
    await store.save(run_done)
    await store.save(run_queued)

    run_id = await store.dequeue_next_queued()
    assert run_id == "q2"


# ── crash recovery: QUEUED survives, RUNNING marked FAILED ─────────────────


@pytest.mark.asyncio
async def test_mark_interrupted_runs_only_touches_running(store: SqliteStore) -> None:
    """Crash recovery marks RUNNING → FAILED, leaves QUEUED untouched."""
    run_queued = _make_run(id="q", status=RunStatus.QUEUED)
    run_running = _make_run(id="r", status=RunStatus.RUNNING)
    run_completed = _make_run(id="c", status=RunStatus.COMPLETED)
    await store.save(run_queued)
    await store.save(run_running)
    await store.save(run_completed)

    count = await store.mark_interrupted_runs()
    assert count >= 1  # at least the RUNNING one

    queued = await store.get("q")
    assert queued.status == RunStatus.QUEUED, "QUEUED must survive restart"

    running = await store.get("r")
    assert running.status == RunStatus.FAILED
    assert "interrupted" in (running.error or "")

    completed = await store.get("c")
    assert completed.status == RunStatus.COMPLETED, "COMPLETED must survive restart"
