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
