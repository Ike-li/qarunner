"""Behavioral integration tests for the PostgreSQL store."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from qarunner.adapters.postgres_store import PostgresStore
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
async def store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-tracer-admin-password-with-at-least-32-characters",
    )
    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    value = PostgresStore(database_url, schema=schema)
    try:
        await value.initialize()
        yield value
    finally:
        await value.close()
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_empty_database_initialization_is_repeatable_and_seeds_admin(
    store: PostgresStore,
) -> None:
    await store.initialize()

    admin = await store.get_user("admin")

    assert admin is not None
    assert admin["username"] == "admin"
    assert admin["role"] == "admin"


@pytest.mark.asyncio
async def test_migrations_record_checksums_and_reject_ddl_drift(store: PostgresStore) -> None:
    async with store._require_pool().acquire() as connection:
        migrations = await connection.fetch(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )

        assert [row["version"] for row in migrations] == list(range(1, 11))
        assert all(len(row["checksum"]) == 64 for row in migrations)

        await connection.execute(
            "UPDATE schema_migrations SET checksum = $1 WHERE version = 1", "0" * 64
        )

    with pytest.raises(RuntimeError, match="migration 1 checksum mismatch"):
        await store.initialize()


@pytest.mark.asyncio
async def test_existing_migration_ledger_is_bootstrapped_once(store: PostgresStore) -> None:
    async with store._require_pool().acquire() as connection:
        await connection.execute(
            "ALTER TABLE schema_migrations ALTER COLUMN checksum DROP NOT NULL"
        )
        await connection.execute("UPDATE schema_migrations SET checksum = NULL WHERE version = 1")

    await store.initialize()

    async with store._require_pool().acquire() as connection:
        checksum = await connection.fetchval(
            "SELECT checksum FROM schema_migrations WHERE version = 1"
        )
        nullable = await connection.fetchval(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'schema_migrations'
              AND column_name = 'checksum'
            """
        )

    assert len(checksum) == 64
    assert nullable == "NO"


@pytest.mark.asyncio
async def test_initialization_rejects_schema_version_newer_than_code(store: PostgresStore) -> None:
    async with store._require_pool().acquire() as connection:
        await connection.execute(
            "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
            999,
            "0" * 64,
        )

    with pytest.raises(RuntimeError, match="unknown applied migration 999"):
        await store.initialize()


@pytest.mark.asyncio
async def test_initialization_rejects_non_contiguous_migration_ledger(
    store: PostgresStore,
) -> None:
    async with store._require_pool().acquire() as connection:
        await connection.execute("DELETE FROM schema_migrations WHERE version = 2")

    with pytest.raises(RuntimeError, match="non-contiguous migration ledger"):
        await store.initialize()


@pytest.mark.asyncio
async def test_run_round_trip_preserves_execution_inputs(
    store: PostgresStore,
) -> None:
    run = Run(
        id="run-postgres-001",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        args=["-v", "-m", "smoke"],
        allure_enabled=True,
        timeout=300,
        executor_mode="docker",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
        env={"TARGET": "staging"},
        summary=TestSummary(total=2, passed=1, failed=1, skipped=0, error=0, duration_ms=42),
        report=ReportRef(
            allure_results_dir="/results",
            allure_report_file="/report/index.html",
            html_generated=True,
        ),
    )

    await store.save(run)

    assert await store.get(run.id) == run


@pytest.mark.asyncio
async def test_run_list_is_newest_first_and_optionally_bounded(store: PostgresStore) -> None:
    for index in range(3):
        await store.save(
            Run(
                id=f"run-{index}",
                status=RunStatus.QUEUED,
                runner="pytest",
                created_by="alice",
                tests_path="suite/tests",
                created_at=datetime(2026, 7, 16 + index, tzinfo=UTC),
            )
        )

    assert [run.id for run in await store.list()] == ["run-2", "run-1", "run-0"]
    assert [run.id for run in await store.list(limit=2)] == ["run-2", "run-1"]


@pytest.mark.asyncio
async def test_run_list_limit_has_stable_id_tie_break_for_equal_timestamps(
    store: PostgresStore,
) -> None:
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    for run_id in ["run-tie-b", "run-tie-a", "run-tie-c"]:
        await store.save(
            Run(
                id=run_id,
                status=RunStatus.QUEUED,
                runner="pytest",
                created_by="alice",
                tests_path="suite/tests",
                created_at=created_at,
            )
        )

    runs = await store.list(limit=2)

    assert [run.id for run in runs] == ["run-tie-c", "run-tie-b"]


@pytest.mark.asyncio
async def test_cancel_only_transitions_inflight_runs(store: PostgresStore) -> None:
    queued = Run(
        id="queued-run",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    completed = queued.model_copy(update={"id": "completed-run", "status": RunStatus.COMPLETED})
    await store.save(queued)
    await store.save(completed)

    assert await store.cancel_if_inflight(queued.id, "2026-07-16T01:00:00+00:00") is True
    assert await store.cancel_if_inflight(completed.id, "2026-07-16T01:00:00+00:00") is False
    assert (await store.get(queued.id)).status is RunStatus.CANCELLED
    assert (await store.get(completed.id)).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_inflight_count_is_owner_scoped(store: PostgresStore) -> None:
    base = Run(
        id="alice-queued",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save(base)
    await store.save(base.model_copy(update={"id": "alice-running", "status": RunStatus.RUNNING}))
    await store.save(base.model_copy(update={"id": "alice-done", "status": RunStatus.COMPLETED}))
    await store.save(base.model_copy(update={"id": "bob-queued", "created_by": "bob"}))

    assert await store.count_inflight_runs("alice") == 2
    assert await store.count_inflight_runs("bob") == 1


@pytest.mark.asyncio
async def test_concurrent_inflight_limited_creates_respect_owner_cap(
    store: PostgresStore,
) -> None:
    base = Run(
        id="limited-0",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

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


@pytest.mark.asyncio
async def test_run_delete_reports_hit_and_miss(store: PostgresStore) -> None:
    run = Run(
        id="run-delete",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save(run)

    assert await store.delete_run(run.id) is True
    assert await store.delete_run(run.id) is False
    with pytest.raises(RunNotFound):
        await store.get(run.id)


def _diagnosis(summary: str = "an assertion failed") -> FailureDiagnosis:
    return FailureDiagnosis(
        category=RootCauseCategory.ASSERTION,
        confidence=DiagnosisConfidence.HIGH,
        summary=summary,
        evidence=["expected 200"],
        is_likely_regression=True,
    )


@pytest.mark.asyncio
async def test_ai_diagnosis_round_trip_upsert_and_run_delete_cascade(
    store: PostgresStore,
) -> None:
    run = Run(
        id="postgres-ai-diagnosis",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save(run)
    assert await store.get_ai_diagnosis(run.id) is None

    await store.save_ai_diagnosis(
        run.id,
        _diagnosis(),
        "anthropic",
        "claude-x",
        datetime(2026, 7, 16, tzinfo=UTC),
    )
    assert await store.get_ai_diagnosis(run.id) == _diagnosis()

    revised = _diagnosis("revised")
    await store.save_ai_diagnosis(
        run.id,
        revised,
        "openai",
        "gpt-x",
        datetime(2026, 7, 17, tzinfo=UTC),
    )
    assert await store.get_ai_diagnosis(run.id) == revised

    assert await store.delete_run(run.id) is True
    assert await store.get_ai_diagnosis(run.id) is None


@pytest.mark.asyncio
async def test_ai_diagnosis_schema_drift_degrades_to_missing(store: PostgresStore) -> None:
    run = Run(
        id="postgres-ai-schema-drift",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save(run)
    async with store._require_pool().acquire() as connection:
        await connection.execute(
            """
            INSERT INTO run_ai_diagnosis
                (run_id, diagnosis_json, provider, model, created_at)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            """,
            run.id,
            '{"category": "future-category"}',
            "future-provider",
            "future-model",
            datetime(2026, 7, 16, tzinfo=UTC),
        )

    assert await store.get_ai_diagnosis(run.id) is None


@pytest.mark.asyncio
async def test_concurrent_ai_diagnosis_upserts_keep_one_coherent_row(
    store: PostgresStore,
) -> None:
    run = Run(
        id="postgres-ai-concurrent-upsert",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save(run)
    first_at = datetime(2026, 7, 16, tzinfo=UTC)
    second_at = datetime(2026, 7, 17, tzinfo=UTC)

    await asyncio.gather(
        store.save_ai_diagnosis(run.id, _diagnosis("first"), "provider-a", "model-a", first_at),
        store.save_ai_diagnosis(run.id, _diagnosis("second"), "provider-b", "model-b", second_at),
    )

    async with store._require_pool().acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT diagnosis_json, provider, model, created_at
            FROM run_ai_diagnosis
            WHERE run_id = $1
            """,
            run.id,
        )
    assert row is not None
    persisted = FailureDiagnosis.model_validate_json(row["diagnosis_json"])
    assert (persisted.summary, row["provider"], row["model"], row["created_at"]) in {
        ("first", "provider-a", "model-a", first_at),
        ("second", "provider-b", "model-b", second_at),
    }


@pytest.mark.asyncio
async def test_ai_diagnosis_rejects_missing_parent_run(store: PostgresStore) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await store.save_ai_diagnosis(
            "missing-run",
            _diagnosis(),
            "anthropic",
            "claude-x",
            datetime(2026, 7, 16, tzinfo=UTC),
        )

    async with store._require_pool().acquire() as connection:
        count = await connection.fetchval("SELECT count(*) FROM run_ai_diagnosis")
    assert count == 0


@pytest.mark.asyncio
async def test_dequeue_is_fifo_and_each_queued_run_has_one_winner(store: PostgresStore) -> None:
    for index in range(3):
        await store.save(
            Run(
                id=f"queued-{index}",
                status=RunStatus.QUEUED,
                runner="pytest",
                created_by="alice",
                tests_path="suite/tests",
                created_at=datetime(2026, 7, 16, 0, 0, index, tzinfo=UTC),
            )
        )

    assert await store.dequeue_next_queued() == "queued-0"
    concurrent = await asyncio.gather(
        store.dequeue_next_queued(),
        store.dequeue_next_queued(),
        store.dequeue_next_queued(),
    )

    assert sorted(value for value in concurrent if value is not None) == ["queued-1", "queued-2"]
    assert concurrent.count(None) == 1
    claimed = [await store.get(f"queued-{index}") for index in range(3)]
    assert all(run.status is RunStatus.RUNNING for run in claimed)
    assert all(run.started_at is not None for run in claimed)


@pytest.mark.asyncio
async def test_missing_run_raises_domain_error(store: PostgresStore) -> None:
    with pytest.raises(RunNotFound):
        await store.get("missing-run")


@pytest.mark.asyncio
async def test_lifecycle_save_preserves_a_concurrent_run_lock(store: PostgresStore) -> None:
    run = Run(
        id="run-lock-race",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save(run)
    await store.lock_run(run.id, True)

    await store.save(run.model_copy(update={"status": RunStatus.COMPLETED}))

    persisted = await store.get(run.id)
    assert persisted.status is RunStatus.COMPLETED
    assert persisted.locked is True


def _old_cleanup_run(run_id: str, **updates: object) -> Run:
    old = datetime.now(UTC) - timedelta(days=10)
    return Run(
        id=run_id,
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite/tests",
        created_at=old,
        finished_at=old,
        report=ReportRef(
            allure_results_dir="results",
            allure_report_file="report/index.html",
            html_generated=True,
        ),
    ).model_copy(update=updates)


@pytest.mark.asyncio
async def test_cleanup_claim_and_user_lock_elect_one_winner(store: PostgresStore) -> None:
    run = _old_cleanup_run("postgres-cleanup-lock-race")
    await store.save(run)

    claim_won, lock_won = await asyncio.gather(
        store.claim_run_cleanup(run.id, retention_days=7),
        store.lock_run(run.id, True),
    )

    assert claim_won + lock_won == 1
    if claim_won:
        assert (await store.get(run.id)).locked is False
        await store.finish_run_cleanup(run.id, cleaned=False)
    else:
        assert (await store.get(run.id)).locked is True


@pytest.mark.asyncio
async def test_cleanup_claim_validates_eligibility_and_has_one_winner(
    store: PostgresStore,
) -> None:
    eligible = _old_cleanup_run("postgres-cleanup-eligible")
    await store.save(eligible)
    await store.save(_old_cleanup_run("postgres-cleanup-recent", finished_at=datetime.now(UTC)))
    await store.save(_old_cleanup_run("postgres-cleanup-running", status=RunStatus.RUNNING))
    locked = _old_cleanup_run("postgres-cleanup-locked")
    await store.save(locked)
    assert await store.lock_run(locked.id, True) is True

    winners = await asyncio.gather(
        store.claim_run_cleanup(eligible.id, retention_days=7),
        store.claim_run_cleanup(eligible.id, retention_days=7),
    )

    assert winners.count(True) == 1
    assert await store.claim_run_cleanup("missing", retention_days=7) is False
    assert await store.claim_run_cleanup("postgres-cleanup-recent", 7) is False
    assert await store.claim_run_cleanup("postgres-cleanup-running", 7) is False
    assert await store.claim_run_cleanup(locked.id, 7) is False
    assert await store.get_old_unlocked_runs(7) == []


@pytest.mark.asyncio
async def test_finish_cleanup_narrowly_updates_report_and_releases_claim(
    store: PostgresStore,
) -> None:
    run = _old_cleanup_run("postgres-cleanup-finish", error="preserve-me")
    await store.save(run)

    assert await store.claim_run_cleanup(run.id, 7) is True
    await store.finish_run_cleanup(run.id, cleaned=False)
    assert (await store.get(run.id)).report == run.report
    assert await store.claim_run_cleanup(run.id, 7) is True

    await store.finish_run_cleanup(run.id, cleaned=True)

    persisted = await store.get(run.id)
    assert persisted.report is None
    assert persisted.error == "preserve-me"
    assert persisted.finished_at == run.finished_at
    assert await store.lock_run(run.id, True) is True


@pytest.mark.asyncio
async def test_repeated_initialize_does_not_release_active_cleanup_claim(
    store: PostgresStore,
) -> None:
    run = _old_cleanup_run("postgres-cleanup-repeat-initialize")
    await store.save(run)
    assert await store.claim_run_cleanup(run.id, 7) is True

    await store.initialize()

    assert await store.lock_run(run.id, True) is False
    await store.finish_run_cleanup(run.id, cleaned=False)


@pytest.mark.asyncio
async def test_new_store_instance_recovers_abandoned_cleanup_claim(
    store: PostgresStore,
) -> None:
    run = _old_cleanup_run("postgres-cleanup-restart-recovery")
    await store.save(run)
    assert await store.claim_run_cleanup(run.id, 7) is True
    database_url = store._database_url
    schema = store._schema
    await store.close()

    restarted = PostgresStore(database_url, schema=schema)
    try:
        await restarted.initialize()
        assert await restarted.claim_run_cleanup(run.id, 7) is True
        await restarted.finish_run_cleanup(run.id, cleaned=False)
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_suite_round_trip_preserves_registration(store: PostgresStore) -> None:
    suite = TestSuite(
        name="postgres-suite",
        source="git",
        repo_url="https://example.com/qarunner-tests.git",
        ref="main",
        credential_ref="credential-1",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    await store.save_suite(suite)

    assert await store.get_suite(suite.name) == suite


@pytest.mark.asyncio
async def test_suite_save_replaces_existing_registration(store: PostgresStore) -> None:
    suite = TestSuite(
        name="postgres-suite",
        source="git",
        repo_url="https://example.com/qarunner-tests.git",
        ref="main",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    replacement = suite.model_copy(
        update={"ref": "release", "credential_ref": "credential-2", "created_by": "bob"}
    )

    await store.save_suite(suite)
    await store.save_suite(replacement)

    assert await store.get_suite(suite.name) == replacement


@pytest.mark.asyncio
async def test_suite_list_is_name_ordered(store: PostgresStore) -> None:
    base = TestSuite(
        name="b-suite",
        source="local",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_suite(base)
    await store.save_suite(base.model_copy(update={"name": "a-suite"}))

    assert [suite.name for suite in await store.list_suites()] == ["a-suite", "b-suite"]


@pytest.mark.asyncio
async def test_suite_delete_and_missing_lookup_report_absence(store: PostgresStore) -> None:
    suite = TestSuite(
        name="delete-suite",
        source="local",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_suite(suite)

    assert await store.delete_suite(suite.name) is True
    assert await store.get_suite(suite.name) is None
    assert await store.delete_suite(suite.name) is False
    assert await store.get_suite("missing-suite") is None


@pytest.mark.asyncio
async def test_credential_metadata_and_ciphertext_have_separate_read_paths(
    store: PostgresStore,
) -> None:
    credential = Credential(
        id="credential-1",
        name="git-token",
        type="https_token",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    await store.save_credential(credential, "ENC(secret-payload)")

    metadata = await store.get_credential(credential.id)
    assert metadata == credential
    assert metadata is not None
    assert not hasattr(metadata, "secret")
    assert "ENC(secret-payload)" not in metadata.model_dump_json()
    assert await store.get_credential_secret(credential.id) == "ENC(secret-payload)"


@pytest.mark.asyncio
async def test_credential_list_is_created_ordered_and_contains_only_metadata(
    store: PostgresStore,
) -> None:
    later = Credential(
        id="credential-later",
        name="later-token",
        type="https_token",
        created_by="alice",
        created_at=datetime(2026, 7, 16, 1, tzinfo=UTC),
    )
    earlier = later.model_copy(
        update={
            "id": "credential-earlier",
            "name": "earlier-token",
            "created_at": datetime(2026, 7, 16, tzinfo=UTC),
        }
    )
    await store.save_credential(later, "ENC(later-secret)")
    await store.save_credential(earlier, "ENC(earlier-secret)")

    credentials = await store.list_credentials()

    assert credentials == [earlier, later]
    serialized = "".join(credential.model_dump_json() for credential in credentials)
    assert "ENC(" not in serialized


@pytest.mark.asyncio
async def test_credential_delete_and_missing_reads_report_absence(store: PostgresStore) -> None:
    credential = Credential(
        id="credential-delete",
        name="delete-token",
        type="https_token",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_credential(credential, "ENC(delete-secret)")

    assert await store.delete_credential(credential.id) is True
    assert await store.get_credential(credential.id) is None
    assert await store.get_credential_secret(credential.id) is None
    assert await store.delete_credential(credential.id) is False
    assert await store.get_credential("missing-credential") is None
    assert await store.get_credential_secret("missing-credential") is None


@pytest.mark.asyncio
async def test_profile_round_trip_preserves_execution_template(store: PostgresStore) -> None:
    profile = TestProfile(
        id="profile-1",
        name="Regression",
        description="Daily regression suite",
        tests_path="suite/tests",
        runner="playwright",
        selected_files=["tests/login.spec.ts"],
        selected_markers=["smoke"],
        extra_args="--workers=2",
        executor_mode="docker",
        timeout=600,
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
        env={"TARGET": "staging"},
        webhook_url="https://example.com/hooks/profile-1",
    )

    await store.save_profile(profile)

    assert await store.get_profile(profile.id) == profile


@pytest.mark.asyncio
async def test_profile_save_replaces_existing_template(store: PostgresStore) -> None:
    profile = TestProfile(
        id="profile-update",
        name="Original",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    replacement = profile.model_copy(
        update={
            "name": "Replacement",
            "description": "updated",
            "tests_path": "suite-b",
            "runner": "playwright",
            "selected_files": ["tests/new.spec.ts"],
            "selected_markers": ["regression"],
            "extra_args": "--trace=on",
            "executor_mode": "subprocess",
            "timeout": 120,
            "created_by": "bob",
            "created_at": datetime(2026, 7, 17, tzinfo=UTC),
            "env": {"TARGET": "production"},
            "webhook_url": "https://example.com/hooks/replacement",
        }
    )

    await store.save_profile(profile)
    await store.save_profile(replacement)

    assert await store.get_profile(profile.id) == replacement


@pytest.mark.asyncio
async def test_profile_list_is_newest_first_and_optionally_path_filtered(
    store: PostgresStore,
) -> None:
    older = TestProfile(
        id="profile-older",
        name="Older",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    newer = older.model_copy(
        update={
            "id": "profile-newer",
            "name": "Newer",
            "tests_path": "suite-b",
            "created_at": datetime(2026, 7, 17, tzinfo=UTC),
        }
    )
    await store.save_profile(older)
    await store.save_profile(newer)

    assert [profile.id for profile in await store.list_profiles()] == [
        "profile-newer",
        "profile-older",
    ]
    assert [profile.id for profile in await store.list_profiles(tests_path="suite-a")] == [
        "profile-older"
    ]


@pytest.mark.asyncio
async def test_profile_list_has_stable_id_tie_break_for_equal_timestamps(
    store: PostgresStore,
) -> None:
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    for suffix in ["b", "a", "c"]:
        await store.save_profile(
            TestProfile(
                id=f"profile-tie-{suffix}",
                name=f"Profile {suffix}",
                tests_path="suite-tie",
                created_by="alice",
                created_at=created_at,
            )
        )

    expected = ["profile-tie-c", "profile-tie-b", "profile-tie-a"]

    assert [profile.id for profile in await store.list_profiles()] == expected
    assert [
        profile.id for profile in await store.list_profiles(tests_path="suite-tie")
    ] == expected


@pytest.mark.asyncio
async def test_profile_delete_and_missing_lookup_report_absence(store: PostgresStore) -> None:
    profile = TestProfile(
        id="profile-delete",
        name="Delete",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)

    assert await store.delete_profile(profile.id) is True
    assert await store.get_profile(profile.id) is None
    assert await store.delete_profile(profile.id) is False
    assert await store.get_profile("missing-profile") is None


@pytest.mark.asyncio
async def test_schedule_round_trip_preserves_cron_configuration(store: PostgresStore) -> None:
    profile = TestProfile(
        id="schedule-profile",
        name="Schedule Profile",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    schedule = TestSchedule(
        id="schedule-1",
        name="Nightly",
        profile_id=profile.id,
        cron_expression="0 2 * * *",
        enabled=False,
        timezone="America/Chicago",
        last_run_at=datetime(2026, 7, 16, 7, tzinfo=UTC),
        next_run_at=datetime(2026, 7, 17, 7, tzinfo=UTC),
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)

    await store.save_schedule(schedule)

    assert await store.get_schedule(schedule.id) == schedule


@pytest.mark.asyncio
async def test_schedule_claim_elects_one_leader_per_fire_time(store: PostgresStore) -> None:
    profile = TestProfile(
        id="claim-profile",
        name="Claim Profile",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    schedule = TestSchedule(
        id="claim-schedule",
        name="Every Five Minutes",
        profile_id=profile.id,
        cron_expression="*/5 * * * *",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)
    await store.save_schedule(schedule)
    fire_time = datetime(2026, 7, 17, 12, tzinfo=UTC)

    winners = await asyncio.gather(
        *(store.claim_schedule_run(schedule.id, fire_time) for _ in range(5))
    )

    assert winners.count(True) == 1
    assert await store.claim_schedule_run(schedule.id, fire_time) is False
    assert await store.claim_schedule_run(schedule.id, fire_time - timedelta(minutes=5)) is False
    assert await store.claim_schedule_run(schedule.id, fire_time + timedelta(minutes=5)) is True
    assert await store.claim_schedule_run("missing-schedule", fire_time) is False
    persisted = await store.get_schedule(schedule.id)
    assert persisted is not None
    assert persisted.last_run_at == fire_time + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_schedule_save_preserves_claim_owned_last_run_at(store: PostgresStore) -> None:
    profile = TestProfile(
        id="preserve-profile",
        name="Preserve Profile",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    schedule = TestSchedule(
        id="preserve-schedule",
        name="Original",
        profile_id=profile.id,
        cron_expression="*/5 * * * *",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)
    await store.save_schedule(schedule)
    fire_time = datetime(2026, 7, 17, 12, tzinfo=UTC)
    assert await store.claim_schedule_run(schedule.id, fire_time) is True

    stale_update = schedule.model_copy(
        update={
            "name": "Renamed",
            "enabled": False,
            "next_run_at": fire_time + timedelta(minutes=5),
        }
    )
    await store.save_schedule(stale_update)

    persisted = await store.get_schedule(schedule.id)
    assert persisted is not None
    assert persisted.name == "Renamed"
    assert persisted.enabled is False
    assert persisted.next_run_at == fire_time + timedelta(minutes=5)
    assert persisted.last_run_at == fire_time


@pytest.mark.asyncio
async def test_schedule_list_is_newest_first_and_optionally_profile_filtered(
    store: PostgresStore,
) -> None:
    first_profile = TestProfile(
        id="schedule-profile-a",
        name="Profile A",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    second_profile = first_profile.model_copy(
        update={"id": "schedule-profile-b", "name": "Profile B"}
    )
    older = TestSchedule(
        id="schedule-older",
        name="Older",
        profile_id=first_profile.id,
        cron_expression="0 1 * * *",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    newer = older.model_copy(
        update={
            "id": "schedule-newer",
            "name": "Newer",
            "profile_id": second_profile.id,
            "created_at": datetime(2026, 7, 17, tzinfo=UTC),
        }
    )
    await store.save_profile(first_profile)
    await store.save_profile(second_profile)
    await store.save_schedule(older)
    await store.save_schedule(newer)

    assert [schedule.id for schedule in await store.list_schedules()] == [
        "schedule-newer",
        "schedule-older",
    ]
    assert [
        schedule.id for schedule in await store.list_schedules(profile_id=first_profile.id)
    ] == ["schedule-older"]


@pytest.mark.asyncio
async def test_schedule_list_has_stable_id_tie_break_for_equal_timestamps(
    store: PostgresStore,
) -> None:
    created_at = datetime(2026, 7, 16, tzinfo=UTC)
    profile = TestProfile(
        id="schedule-tie-profile",
        name="Schedule Tie Profile",
        tests_path="suite-tie",
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


@pytest.mark.asyncio
async def test_schedule_next_run_update_is_narrow_and_missing_safe(store: PostgresStore) -> None:
    profile = TestProfile(
        id="next-run-profile",
        name="Next Run Profile",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    schedule = TestSchedule(
        id="next-run-schedule",
        name="Next Run",
        profile_id=profile.id,
        cron_expression="0 1 * * *",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)
    await store.save_schedule(schedule)
    next_run_at = datetime(2026, 7, 18, tzinfo=UTC)

    await store.update_schedule_next_run(schedule.id, next_run_at)

    persisted = await store.get_schedule(schedule.id)
    assert persisted == schedule.model_copy(update={"next_run_at": next_run_at})
    await store.update_schedule_next_run(schedule.id, None)
    assert await store.get_schedule(schedule.id) == schedule
    await store.update_schedule_next_run("missing-schedule", next_run_at)
    assert await store.get_schedule("missing-schedule") is None


@pytest.mark.asyncio
async def test_schedule_delete_and_missing_lookup_report_absence(store: PostgresStore) -> None:
    profile = TestProfile(
        id="delete-schedule-profile",
        name="Delete Schedule Profile",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    schedule = TestSchedule(
        id="delete-schedule",
        name="Delete Schedule",
        profile_id=profile.id,
        cron_expression="0 1 * * *",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)
    await store.save_schedule(schedule)

    assert await store.delete_schedule(schedule.id) is True
    assert await store.get_schedule(schedule.id) is None
    assert await store.delete_schedule(schedule.id) is False
    assert await store.get_schedule("missing-schedule") is None


@pytest.mark.asyncio
async def test_profile_delete_cascades_to_bound_schedules(store: PostgresStore) -> None:
    profile = TestProfile(
        id="cascade-profile",
        name="Cascade Profile",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    schedule = TestSchedule(
        id="cascade-schedule",
        name="Cascade Schedule",
        profile_id=profile.id,
        cron_expression="0 1 * * *",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)
    await store.save_schedule(schedule)

    assert await store.delete_profile(profile.id) is True

    assert await store.get_schedule(schedule.id) is None


@pytest.mark.asyncio
async def test_profile_upsert_keeps_bound_schedules(store: PostgresStore) -> None:
    profile = TestProfile(
        id="upsert-profile",
        name="Original Profile",
        tests_path="suite-a",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    schedule = TestSchedule(
        id="upsert-profile-schedule",
        name="Bound Schedule",
        profile_id=profile.id,
        cron_expression="0 1 * * *",
        created_by="alice",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )
    await store.save_profile(profile)
    await store.save_schedule(schedule)

    await store.save_profile(profile.model_copy(update={"name": "Renamed Profile"}))

    assert await store.get_schedule(schedule.id) == schedule


@pytest.mark.asyncio
async def test_run_cases_round_trip_preserves_collection_order(store: PostgresStore) -> None:
    run = Run(
        id="run-cases",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    cases = [
        TestCaseResult(suite="auth", name="test_login", status="passed", duration_ms=5),
        TestCaseResult(
            suite="auth",
            name="test_logout",
            status="failed",
            duration_ms=9,
            message="assertion failed",
        ),
    ]
    await store.save(run)

    await store.save_cases(run.id, run.tests_path, run.created_at, cases)

    assert await store.get_cases_for_run(run.id) == cases


@pytest.mark.asyncio
async def test_run_cases_replay_replaces_prior_collection(store: PostgresStore) -> None:
    run = Run(
        id="run-cases-replay",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    original = TestCaseResult(suite="auth", name="test_login", status="failed", duration_ms=9)
    replacement = TestCaseResult(suite="auth", name="test_login", status="passed", duration_ms=5)
    await store.save(run)
    await store.save_cases(run.id, run.tests_path, run.created_at, [original])

    await store.save_cases(run.id, run.tests_path, run.created_at, [replacement])

    assert await store.get_cases_for_run(run.id) == [replacement]


@pytest.mark.asyncio
async def test_run_case_replay_rolls_back_after_connection_termination_and_retries(
    store: PostgresStore,
) -> None:
    run = Run(
        id="run-cases-connection-loss",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    original = TestCaseResult(suite="auth", name="original", status="failed", duration_ms=9)
    replacement = TestCaseResult(suite="auth", name="replacement", status="passed", duration_ms=5)
    await store.save(run)
    await store.save_cases(run.id, run.tests_path, run.created_at, [original])

    async with store._require_pool().acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION terminate_case_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_terminate_backend(pg_backend_pid());
                RETURN NEW;
            END
            $$;
            CREATE TRIGGER terminate_case_insert
            BEFORE INSERT ON run_test_cases
            FOR EACH ROW EXECUTE FUNCTION terminate_case_insert()
            """
        )

    with pytest.raises((asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError)):
        await store.save_cases(run.id, run.tests_path, run.created_at, [replacement])

    assert await store.get_cases_for_run(run.id) == [original]

    async with store._require_pool().acquire() as connection:
        await connection.execute(
            """
            DROP TRIGGER terminate_case_insert ON run_test_cases;
            DROP FUNCTION terminate_case_insert()
            """
        )

    await store.save_cases(run.id, run.tests_path, run.created_at, [replacement])

    assert await store.get_cases_for_run(run.id) == [replacement]


@pytest.mark.asyncio
async def test_concurrent_run_case_replays_leave_one_complete_collection(
    store: PostgresStore,
) -> None:
    run = Run(
        id="run-cases-concurrent",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    collections = [
        [
            TestCaseResult(
                suite="auth",
                name=f"test_{index}_{case_index}",
                status="passed",
                duration_ms=5,
            )
            for case_index in range(2)
        ]
        for index in range(5)
    ]
    await store.save(run)

    await asyncio.gather(
        *(store.save_cases(run.id, run.tests_path, run.created_at, cases) for cases in collections)
    )

    persisted = await store.get_cases_for_run(run.id)
    assert persisted in collections


@pytest.mark.asyncio
async def test_run_cases_empty_replay_clears_prior_collection(store: PostgresStore) -> None:
    run = Run(
        id="run-cases-clear",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    case = TestCaseResult(suite="auth", name="test_login", status="passed", duration_ms=5)
    await store.save(run)
    await store.save_cases(run.id, run.tests_path, run.created_at, [case])

    await store.save_cases(run.id, run.tests_path, run.created_at, [])

    assert await store.get_cases_for_run(run.id) == []


@pytest.mark.asyncio
async def test_run_case_messages_are_bounded(store: PostgresStore) -> None:
    run = Run(
        id="run-cases-message",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    case = TestCaseResult(
        suite="auth",
        name="test_login",
        status="failed",
        duration_ms=5,
        message="x" * 9000,
    )
    await store.save(run)

    await store.save_cases(run.id, run.tests_path, run.created_at, [case])

    persisted = await store.get_cases_for_run(run.id)
    assert persisted[0].message == "x" * 8192


@pytest.mark.asyncio
async def test_run_delete_cascades_to_cases(store: PostgresStore) -> None:
    run = Run(
        id="run-cases-cascade",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    case = TestCaseResult(suite="auth", name="test_login", status="passed", duration_ms=5)
    await store.save(run)
    await store.save_cases(run.id, run.tests_path, run.created_at, [case])

    assert await store.delete_run(run.id) is True

    assert await store.get_cases_for_run(run.id) == []


@pytest.mark.asyncio
async def test_case_history_returns_recent_window_oldest_first(store: PostgresStore) -> None:
    for index, status in enumerate(["passed", "failed", "passed"]):
        run = Run(
            id=f"history-{index}",
            status=RunStatus.COMPLETED,
            runner="pytest",
            created_by="alice",
            tests_path="suite-a",
            created_at=datetime(2026, 7, 15 + index, tzinfo=UTC),
        )
        case = TestCaseResult(suite="auth", name="test_login", status=status, duration_ms=5)
        await store.save(run)
        await store.save_cases(run.id, run.tests_path, run.created_at, [case])

    history = await store.get_case_history("suite-a", "auth", "test_login", limit=2)

    assert [point.status for point in history] == ["failed", "passed"]
    assert [point.created_at for point in history] == [
        datetime(2026, 7, 16, tzinfo=UTC),
        datetime(2026, 7, 17, tzinfo=UTC),
    ]


@pytest.mark.asyncio
async def test_case_history_has_stable_tie_break_at_limit_boundary(store: PostgresStore) -> None:
    created_at = datetime(2026, 7, 17, tzinfo=UTC)
    for suffix, status in [("a", "passed"), ("b", "failed"), ("c", "passed")]:
        run = Run(
            id=f"history-tie-{suffix}",
            status=RunStatus.COMPLETED,
            runner="pytest",
            created_by="alice",
            tests_path="suite-a",
            created_at=created_at,
        )
        await store.save(run)
        await store.save_cases(
            run.id,
            run.tests_path,
            run.created_at,
            [TestCaseResult(suite="auth", name="test_tie", status=status, duration_ms=5)],
        )

    history = await store.get_case_history("suite-a", "auth", "test_tie", limit=2)

    assert [point.status for point in history] == ["failed", "passed"]


@pytest.mark.asyncio
async def test_case_histories_batch_and_scope_by_owner_or_profile(store: PostgresStore) -> None:
    inputs = [
        ("history-alice", "alice", "profile-a", "auth", "test_login", "failed"),
        ("history-bob", "bob", "profile-b", "auth", "test_login", "passed"),
        ("history-other", "alice", "profile-a", "billing", "test_pay", "passed"),
    ]
    for index, (run_id, owner, profile_id, suite, name, status) in enumerate(inputs):
        run = Run(
            id=run_id,
            status=RunStatus.COMPLETED,
            runner="pytest",
            created_by=owner,
            tests_path="suite-a",
            profile_id=profile_id,
            created_at=datetime(2026, 7, 15 + index, tzinfo=UTC),
        )
        await store.save(run)
        await store.save_cases(
            run.id,
            run.tests_path,
            run.created_at,
            [TestCaseResult(suite=suite, name=name, status=status, duration_ms=5)],
        )

    admin = await store.get_case_histories(
        "suite-a", [("auth", "test_login"), ("billing", "test_pay")]
    )
    alice = await store.get_case_histories("suite-a", [("auth", "test_login")], created_by="alice")
    profile_b = await store.get_case_histories(
        "suite-a", [("auth", "test_login")], profile_id="profile-b"
    )

    assert [point.status for point in admin[("auth", "test_login")]] == [
        "failed",
        "passed",
    ]
    assert [point.status for point in admin[("billing", "test_pay")]] == ["passed"]
    assert [point.status for point in alice[("auth", "test_login")]] == ["failed"]
    assert [point.status for point in profile_b[("auth", "test_login")]] == ["passed"]
    assert await store.get_case_histories("suite-a", []) == {}


@pytest.mark.asyncio
async def test_flaky_count_uses_completed_recent_owner_scoped_histories(
    store: PostgresStore,
) -> None:
    now = datetime.now(UTC)
    for index, status in enumerate(["passed", "failed", "passed", "failed"]):
        run = Run(
            id=f"flaky-alice-{index}",
            status=RunStatus.COMPLETED,
            runner="pytest",
            created_by="alice",
            tests_path="suite-a",
            created_at=now - timedelta(hours=4 - index),
        )
        await store.save(run)
        await store.save_cases(
            run.id,
            run.tests_path,
            run.created_at,
            [TestCaseResult(suite="auth", name="test_login", status=status, duration_ms=5)],
        )
    bob = Run(
        id="flaky-bob-stable",
        status=RunStatus.COMPLETED,
        runner="pytest",
        created_by="bob",
        tests_path="suite-a",
        created_at=now - timedelta(hours=1),
    )
    await store.save(bob)
    await store.save_cases(
        bob.id,
        bob.tests_path,
        bob.created_at,
        [TestCaseResult(suite="auth", name="test_other", status="passed", duration_ms=5)],
    )
    for category, run_status, created_at in [
        ("running", RunStatus.RUNNING, now - timedelta(minutes=30)),
        ("old", RunStatus.COMPLETED, now - timedelta(days=31)),
    ]:
        for index, status in enumerate(["passed", "failed", "passed", "failed"]):
            excluded = Run(
                id=f"flaky-{category}-{index}",
                status=run_status,
                runner="pytest",
                created_by="alice",
                tests_path="suite-a",
                created_at=created_at + timedelta(seconds=index),
            )
            await store.save(excluded)
            await store.save_cases(
                excluded.id,
                excluded.tests_path,
                excluded.created_at,
                [
                    TestCaseResult(
                        suite="auth",
                        name=f"test_{category}",
                        status=status,
                        duration_ms=5,
                    )
                ],
            )

    assert await store.count_flaky_tests(days=30) == 1
    assert await store.count_flaky_tests(days=30, created_by="alice") == 1
    assert await store.count_flaky_tests(days=30, created_by="bob") == 0
    assert await store.count_flaky_tests(days=30, min_observations=5, flip_threshold=4) == 0


@pytest.mark.asyncio
async def test_flaky_count_has_stable_tie_break_for_equal_timestamps(
    store: PostgresStore,
) -> None:
    created_at = datetime.now(UTC) - timedelta(hours=1)
    for suffix, status in [
        ("a", "passed"),
        ("b", "failed"),
        ("c", "passed"),
        ("d", "failed"),
    ]:
        run = Run(
            id=f"flaky-tie-{suffix}",
            status=RunStatus.COMPLETED,
            runner="pytest",
            created_by="alice",
            tests_path="suite-a",
            created_at=created_at,
        )
        await store.save(run)
        await store.save_cases(
            run.id,
            run.tests_path,
            run.created_at,
            [TestCaseResult(suite="auth", name="test_tie", status=status, duration_ms=5)],
        )

    assert await store.count_flaky_tests(days=30) == 1


@pytest.mark.asyncio
async def test_recovery_fails_only_running_runs_and_reports_count(store: PostgresStore) -> None:
    base = Run(
        id="recovery-running",
        status=RunStatus.RUNNING,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    await store.save(base)
    await store.save(base.model_copy(update={"id": "recovery-queued", "status": RunStatus.QUEUED}))
    await store.save(
        base.model_copy(update={"id": "recovery-completed", "status": RunStatus.COMPLETED})
    )
    await store.save(base.model_copy(update={"id": "recovery-failed", "status": RunStatus.FAILED}))

    assert await store.mark_interrupted_runs() == 1

    recovered = await store.get("recovery-running")
    assert recovered.status is RunStatus.FAILED
    assert recovered.error == "interrupted by server restart"
    assert recovered.finished_at is not None
    assert (await store.get("recovery-queued")).status is RunStatus.QUEUED
    assert (await store.get("recovery-completed")).status is RunStatus.COMPLETED
    assert (await store.get("recovery-failed")).status is RunStatus.FAILED
    assert await store.mark_interrupted_runs() == 0


@pytest.mark.asyncio
async def test_recovery_can_be_scoped_to_one_worker_node(store: PostgresStore) -> None:
    base = Run(
        id="recovery-node-a-running",
        status=RunStatus.RUNNING,
        runner="pytest",
        created_by="alice",
        tests_path="suite-a",
        worker_node_id="node-a",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    await store.save(base)
    await store.save(
        base.model_copy(update={"id": "recovery-node-b-running", "worker_node_id": "node-b"})
    )
    await store.save(
        base.model_copy(update={"id": "recovery-unassigned-running", "worker_node_id": None})
    )
    await store.save(
        base.model_copy(update={"id": "recovery-node-a-queued", "status": RunStatus.QUEUED})
    )

    assert await store.mark_interrupted_runs(worker_node_id="node-a") == 1

    assert (await store.get("recovery-node-a-running")).status is RunStatus.FAILED
    assert (await store.get("recovery-node-b-running")).status is RunStatus.RUNNING
    assert (await store.get("recovery-unassigned-running")).status is RunStatus.RUNNING
    assert (await store.get("recovery-node-a-queued")).status is RunStatus.QUEUED


@pytest.mark.asyncio
async def test_user_create_and_list_round_trip(store: PostgresStore) -> None:
    await store.create_user("alice", "alice-password-hash", "user")

    alice = await store.get_user("alice")
    assert alice is not None
    assert alice["password_hash"] == "alice-password-hash"
    assert alice["role"] == "user"
    assert isinstance(alice["created_at"], str)
    users = await store.list_users()
    assert [user["username"] for user in users] == ["admin", "alice"]
    assert all(isinstance(user["created_at"], str) for user in users)


@pytest.mark.asyncio
async def test_password_update_reports_hit_and_miss(store: PostgresStore) -> None:
    await store.create_user("alice", "old-hash", "user")

    assert await store.update_password("alice", "new-hash") is True
    assert await store.update_password("ghost", "new-hash") is False
    assert (await store.get_user("alice"))["password_hash"] == "new-hash"


@pytest.mark.asyncio
async def test_token_version_increment_reports_hit_and_miss(store: PostgresStore) -> None:
    await store.create_user("alice", "hash", "user")

    assert await store.increment_token_version("alice") is True
    assert await store.increment_token_version("ghost") is False
    assert (await store.get_user("alice"))["token_version"] == 1


@pytest.mark.asyncio
async def test_role_update_reports_hit_and_miss(store: PostgresStore) -> None:
    await store.create_user("alice", "hash", "user")

    assert await store.update_role("alice", "admin") is True
    assert await store.update_role("ghost", "admin") is False
    assert (await store.get_user("alice"))["role"] == "admin"


@pytest.mark.asyncio
async def test_user_delete_reports_hit_and_miss(store: PostgresStore) -> None:
    await store.create_user("alice", "hash", "user")

    assert await store.delete_user("alice") is True
    assert await store.delete_user("alice") is False
    assert await store.get_user("alice") is None


@pytest.mark.asyncio
async def test_sole_admin_cannot_be_removed_or_demoted(store: PostgresStore) -> None:
    assert await store.demote_if_not_last_admin("admin", "user") is False
    assert await store.update_role("admin", "user") is True
    assert await store.delete_user("admin") is False
    assert (await store.get_user("admin"))["role"] == "admin"


@pytest.mark.asyncio
async def test_concurrent_admin_demotions_leave_one_admin(store: PostgresStore) -> None:
    await store.create_user("second-admin", "hash", "admin")

    results = await asyncio.gather(
        store.demote_if_not_last_admin("admin", "user"),
        store.demote_if_not_last_admin("second-admin", "user"),
    )

    assert sorted(results) == [False, True]
    assert sum(user["role"] == "admin" for user in await store.list_users()) == 1


@pytest.mark.asyncio
async def test_concurrent_admin_deletes_leave_one_admin(store: PostgresStore) -> None:
    await store.create_user("second-admin", "hash", "admin")

    results = await asyncio.gather(
        store.delete_user("admin"),
        store.delete_user("second-admin"),
    )

    assert sorted(results) == [False, True]
    assert sum(user["role"] == "admin" for user in await store.list_users()) == 1


@pytest.mark.asyncio
async def test_admin_delete_and_demotion_leave_one_admin(store: PostgresStore) -> None:
    await store.create_user("second-admin", "hash", "admin")

    results = await asyncio.gather(
        store.delete_user("admin"),
        store.demote_if_not_last_admin("second-admin", "user"),
    )

    assert sorted(results) == [False, True]
    assert sum(user["role"] == "admin" for user in await store.list_users()) == 1


@pytest.mark.asyncio
async def test_admin_role_update_and_delete_leave_one_admin(store: PostgresStore) -> None:
    await store.create_user("second-admin", "hash", "admin")

    results = await asyncio.gather(
        store.update_role("admin", "user"),
        store.delete_user("second-admin"),
    )

    assert results[0] is True  # update_role reports existence, not whether policy applied it
    assert sum(user["role"] == "admin" for user in await store.list_users()) == 1


@pytest.mark.asyncio
async def test_store_rejects_reads_before_initialization() -> None:
    store = PostgresStore(os.environ["QARUNNER_TEST_DATABASE_URL"])

    with pytest.raises(RuntimeError, match="not initialized"):
        await store.get_user("admin")

    await store.close()


def test_store_rejects_unsafe_schema_identifiers() -> None:
    with pytest.raises(ValueError, match="safe lowercase identifier"):
        PostgresStore(os.environ["QARUNNER_TEST_DATABASE_URL"], schema="public; DROP SCHEMA")


@pytest.mark.parametrize(
    ("pool_min_size", "pool_max_size"),
    [(0, 1), (1, 0), (2, 1)],
)
def test_store_rejects_invalid_pool_bounds(pool_min_size: int, pool_max_size: int) -> None:
    with pytest.raises(ValueError, match="pool sizes must satisfy"):
        PostgresStore(
            os.environ["QARUNNER_TEST_DATABASE_URL"],
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
        )


@pytest.mark.asyncio
async def test_concurrent_initialization_shares_one_usable_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-tracer-admin-password-with-at-least-32-characters",
    )
    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(database_url)
    await connection.execute(f'CREATE SCHEMA "{schema}"')
    await connection.close()
    store = PostgresStore(database_url, schema=schema)
    try:
        await asyncio.gather(store.initialize(), store.initialize())
        assert await store.get_user("admin") is not None
    finally:
        await store.close()
        connection = await asyncpg.connect(database_url)
        await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await connection.close()


@pytest.mark.asyncio
async def test_failed_initialization_releases_resources_and_can_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(database_url)
    await connection.execute(f'CREATE SCHEMA "{schema}"')
    await connection.close()
    store = PostgresStore(database_url, schema=schema)
    try:
        monkeypatch.setenv("QARUNNER_ADMIN_PASSWORD", "admin123")
        with pytest.raises(ValueError, match="known weak/default"):
            await store.initialize()

        monkeypatch.setenv(
            "QARUNNER_ADMIN_PASSWORD",
            "postgres-tracer-admin-password-with-at-least-32-characters",
        )
        await store.initialize()
        assert await store.get_user("admin") is not None

        monkeypatch.setenv("QARUNNER_ADMIN_PASSWORD", "admin123")
        with pytest.raises(ValueError, match="known weak/default"):
            await store.initialize()
        assert await store.get_user("admin") is not None
    finally:
        await store.close()
        connection = await asyncpg.connect(database_url)
        await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await connection.close()
