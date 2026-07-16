"""Behavioral integration tests for the PostgreSQL store."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from qarunner.adapters.postgres_store import PostgresStore
from qarunner.errors import RunNotFound
from qarunner.models import ReportRef, Run, RunStatus, TestSummary


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


@pytest.mark.asyncio
async def test_user_create_and_list_round_trip(store: PostgresStore) -> None:
    await store.create_user("alice", "alice-password-hash", "user")

    alice = await store.get_user("alice")
    assert alice is not None
    assert alice["password_hash"] == "alice-password-hash"
    assert alice["role"] == "user"
    assert [user["username"] for user in await store.list_users()] == ["admin", "alice"]


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
