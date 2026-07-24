"""BATCH-IDEM: real PostgreSQL Batch create with scoped idempotency.

Proves concurrent identical create elects one row under UNIQUE
(idempotency_scope, idempotency_key), digest conflict is stable, and retired
Suite rejects new Batch binding.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from tests.integration.migration_operator import run_migration_operator

from qarunner.adapters.postgres_batch import PostgresBatchGateway
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.adapters.postgres_suite import PostgresSuiteGateway
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import (
    Batch,
    IdempotencyConflict,
    Suite,
    SuiteConflict,
    canonical_digest,
)

CREATED_AT = datetime(2026, 7, 23, 19, tzinfo=UTC)
REGISTERED_AT = datetime(2026, 7, 23, 16, tzinfo=UTC)
RETIRED_AT = datetime(2026, 7, 23, 18, tzinfo=UTC)
PROJECT_ID = "project-batch-1"
SUITE_ID = "suite-batch-1"
REVISION_ID = "suite-revision-batch-1"
SCOPE = "project:project-batch-1:batch-create"


def _digest(label: str):
    return canonical_digest(
        schema_version="qep.test-batch-request.v1",
        payload={"label": label},
    )


@pytest.fixture
async def batch_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-batch-admin-password-at-least-32-chars",
    )
    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    migration = await asyncio.to_thread(
        run_migration_operator,
        database_url,
        schema,
        "upgrade",
        "head",
    )
    assert migration.returncode == 0, migration.stderr
    store = PostgresStore(database_url, schema=schema)
    try:
        await store.initialize()
        yield store
    finally:
        await store.close()
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()


async def _seed_project(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO qep_projects (id, name, created_at) VALUES ($1, $2, $3)",
            PROJECT_ID,
            "Batch Project",
            REGISTERED_AT,
        )
        await connection.execute(
            """
            INSERT INTO qep_resource_profiles (
                id, name, profile_version, framework, requests, limits,
                internal_workers, security_profile_id, approved_at, created_at
            ) VALUES ($1, $2, 1, 'pytest', '{}'::jsonb, '{}'::jsonb, 1, $3, $4, $4)
            """,
            "profile-default",
            "Default",
            "security-profile-1",
            REGISTERED_AT,
        )


def _register_suite() -> Suite:
    return Suite.register(
        suite_id=SUITE_ID,
        project_id=PROJECT_ID,
        name="shop-regression",
        revision_id=REVISION_ID,
        source_spec_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "source-v1"},
        ),
        config_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "config-v1"},
        ),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )


def _open_batch(
    *,
    batch_id: str = "batch-001",
    key: str = "idem-001",
    label: str = "create-v1",
    suite: Suite | None = None,
) -> Batch:
    return Batch.open_for_suite(
        batch_id=batch_id,
        suite=suite or _register_suite(),
        request_digest=_digest(label),
        idempotency_scope=SCOPE,
        idempotency_key=key,
        created_at=CREATED_AT,
    )


async def _register_active_suite(pool: asyncpg.Pool) -> Suite:
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    return suite


@pytest.mark.asyncio
async def test_create_and_rehydrate_batch(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = _open_batch(suite=suite)
    async with PostgresBatchGateway(pool) as gateway:
        result = await gateway.create(batch=batch)
    assert result.replayed is False
    async with PostgresBatchGateway(pool) as gateway:
        loaded = await gateway.get_batch(batch_id=batch.id)
    assert loaded.id == batch.id
    assert loaded.project_id == PROJECT_ID
    assert loaded.suite_revision_id == REVISION_ID
    assert loaded.request_digest == batch.request_digest
    assert loaded.idempotency_scope == SCOPE
    assert loaded.idempotency_key == "idem-001"
    assert loaded.has_create_identity is True
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 1


@pytest.mark.asyncio
async def test_create_exact_replay(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = _open_batch(suite=suite)
    async with PostgresBatchGateway(pool) as gateway:
        first = await gateway.create(batch=batch)
    async with PostgresBatchGateway(pool) as gateway:
        second = await gateway.create(batch=batch)
    assert first.replayed is False
    assert second.replayed is True
    assert second.value.id == batch.id
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 1


@pytest.mark.asyncio
async def test_create_digest_conflict(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    first = _open_batch(suite=suite, label="create-v1")
    async with PostgresBatchGateway(pool) as gateway:
        await gateway.create(batch=first)
    conflicting = _open_batch(
        suite=suite, batch_id="batch-other", key="idem-001", label="create-v2"
    )
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(IdempotencyConflict) as caught:
            await gateway.create(batch=conflicting)
    assert caught.value.scope == SCOPE
    assert caught.value.key == "idem-001"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 1


@pytest.mark.asyncio
async def test_create_rejects_retired_suite(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    async with PostgresSuiteGateway(pool) as gateway:
        snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
        retired = snapshot.suite.retire(retired_at=RETIRED_AT, expected_version=snapshot.version)
        await gateway.publish_retire(suite=retired, expected=snapshot)
    # Domain open on a still-active object; adapter re-checks live suite status.
    batch = _open_batch(suite=suite)
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(SuiteConflict) as caught:
            await gateway.create(batch=batch)
    assert caught.value.reason == "suite_retired"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 0


@pytest.mark.asyncio
async def test_concurrent_identical_create_elects_one_row(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    contenders = 100
    barrier = asyncio.Barrier(contenders)
    batch = _open_batch(suite=suite)

    async def contender() -> ReplayResult[Batch]:
        # Barrier before pool acquire: default pool max is 10; holding 100
        # connections through the barrier deadlocks the test harness.
        await barrier.wait()
        async with PostgresBatchGateway(pool) as gateway:
            return await gateway.create(batch=batch)

    results = await asyncio.gather(*(contender() for _ in range(contenders)))
    winners = [item for item in results if item.replayed is False]
    replays = [item for item in results if item.replayed is True]
    assert len(winners) == 1
    assert len(replays) == contenders - 1
    assert all(item.value.id == batch.id for item in results)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 1


@pytest.mark.asyncio
async def test_concurrent_digest_conflict_is_stable(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    first = _open_batch(suite=suite, batch_id="batch-a", key="idem-race", label="create-a")
    second = _open_batch(suite=suite, batch_id="batch-b", key="idem-race", label="create-b")
    barrier = asyncio.Barrier(2)

    async def create_one(candidate: Batch) -> ReplayResult[Batch]:
        await barrier.wait()
        async with PostgresBatchGateway(pool) as gateway:
            return await gateway.create(batch=candidate)

    outcomes = await asyncio.gather(
        create_one(first),
        create_one(second),
        return_exceptions=True,
    )
    successes = [item for item in outcomes if not isinstance(item, BaseException)]
    conflicts = [item for item in outcomes if isinstance(item, IdempotencyConflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 1


@pytest.mark.asyncio
async def test_create_rejects_lifecycle_only_batch(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(PortContractError) as caught:
            await gateway.create(batch=Batch.create(batch_id="batch-naked"))
    assert caught.value.reason == "missing"


@pytest.mark.asyncio
async def test_sticky_abort_rolls_back_create_when_later_op_fails(
    batch_store: PostgresStore,
) -> None:
    """Same one-shot UoW: successful insert then conflict sticky-aborts the whole txn."""
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = _open_batch(suite=suite)
    async with PostgresBatchGateway(pool) as gateway:
        await gateway.create(batch=batch)
        with pytest.raises(IdempotencyConflict):
            await gateway.create(
                batch=_open_batch(suite=suite, batch_id="batch-x", key="idem-001", label="other")
            )
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 0


@pytest.mark.asyncio
async def test_gateway_lifecycle_rejects_reentry(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    gateway = PostgresBatchGateway(pool)
    async with gateway:
        with pytest.raises(PortContractError) as reenter:
            await gateway.__aenter__()
        assert reenter.value.reason == "already_active"
    with pytest.raises(PortContractError) as closed:
        async with gateway:
            pass
    assert closed.value.reason == "closed"


@pytest.mark.asyncio
async def test_methods_reject_use_outside_active_transaction(
    batch_store: PostgresStore,
) -> None:
    pool = batch_store._require_pool()
    gateway = PostgresBatchGateway(pool)
    with pytest.raises(PortContractError) as exc:
        await gateway.get_batch(batch_id="missing")
    assert exc.value.reason == "not_active"


@pytest.mark.asyncio
async def test_get_batch_not_found_and_by_idempotency_miss(
    batch_store: PostgresStore,
) -> None:
    pool = batch_store._require_pool()
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(PortContractError) as missing:
            await gateway.get_batch(batch_id="batch-missing")
        assert missing.value.reason == "not_found"
        assert (
            await gateway.get_by_idempotency(
                idempotency_scope=SCOPE, idempotency_key="no-such-key"
            )
            is None
        )


@pytest.mark.asyncio
async def test_get_by_idempotency_hit(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = _open_batch(suite=suite)
    async with PostgresBatchGateway(pool) as gateway:
        await gateway.create(batch=batch)
    async with PostgresBatchGateway(pool) as gateway:
        loaded = await gateway.get_by_idempotency(
            idempotency_scope=SCOPE, idempotency_key="idem-001"
        )
    assert loaded is not None
    assert loaded.id == batch.id


@pytest.mark.asyncio
async def test_create_rejects_non_initial_batch(batch_store: PostgresStore) -> None:
    from dataclasses import replace

    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = _open_batch(suite=suite)
    advanced = replace(batch, version=1)
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(PortContractError) as exc:
            await gateway.create(batch=advanced)
    assert exc.value.reason == "not_initial_create"


@pytest.mark.asyncio
async def test_create_rejects_unknown_suite_revision(batch_store: PostgresStore) -> None:
    from dataclasses import replace

    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = replace(_open_batch(suite=suite), suite_revision_id="suite-revision-ghost")
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(PortContractError) as exc:
            await gateway.create(batch=batch)
    assert exc.value.field == "suite_revision_id"
    assert exc.value.reason == "not_found"


@pytest.mark.asyncio
async def test_create_rejects_project_mismatch(batch_store: PostgresStore) -> None:
    from dataclasses import replace

    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = replace(_open_batch(suite=suite), project_id="project-other")
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(PortContractError) as exc:
            await gateway.create(batch=batch)
    assert exc.value.reason == "suite_project_mismatch"


@pytest.mark.asyncio
async def test_create_duplicate_batch_id_is_stable(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    first = _open_batch(suite=suite, batch_id="batch-dup", key="idem-a", label="a")
    second = _open_batch(suite=suite, batch_id="batch-dup", key="idem-b", label="b")
    async with PostgresBatchGateway(pool) as gateway:
        await gateway.create(batch=first)
    async with PostgresBatchGateway(pool) as gateway:
        with pytest.raises(PortContractError) as exc:
            await gateway.create(batch=second)
    assert exc.value.field == "batch_id"
    assert exc.value.reason == "already_exists"


@pytest.mark.asyncio
async def test_suppressed_batch_insert_sticky_aborts(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    async with pool.acquire() as setup:
        await setup.execute(
            """
            CREATE OR REPLACE FUNCTION qep_suppress_batch_insert()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END;
            $$
            """
        )
        await setup.execute(
            """
            CREATE TRIGGER qep_suppress_batch_insert
            BEFORE INSERT ON qep_batches
            FOR EACH ROW EXECUTE FUNCTION qep_suppress_batch_insert()
            """
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresBatchGateway(pool) as gateway:
            await gateway.create(batch=_open_batch(suite=suite))
    assert exc.value.reason == "insert_suppressed"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 0


@pytest.mark.asyncio
async def test_aexit_rejects_when_not_active(batch_store: PostgresStore) -> None:
    pool = batch_store._require_pool()
    gateway = PostgresBatchGateway(pool)
    with pytest.raises(PortContractError) as exc:
        await gateway.__aexit__(None, None, None)
    assert exc.value.reason == "not_active"


@pytest.mark.asyncio
async def test_aenter_start_failure_releases_connection(batch_store: PostgresStore) -> None:
    """transaction.start failure must release the acquired connection and close."""
    pool = batch_store._require_pool()
    gateway = PostgresBatchGateway(pool)

    class BrokenConnection:
        def transaction(self, isolation: str = "read_committed"):
            return BrokenTransaction()

    class BrokenTransaction:
        async def start(self) -> None:
            raise RuntimeError("start failed")

    real_acquire = pool.acquire
    released: list[object] = []

    class FakePool:
        async def acquire(self):
            return BrokenConnection()

        async def release(self, connection):
            released.append(connection)

    gateway._pool = FakePool()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="start failed"):
        await gateway.__aenter__()
    assert gateway._closed is True
    assert len(released) == 1
    # restore unused real pool reference so fixture teardown is unaffected
    gateway._pool = pool  # type: ignore[assignment]
    del real_acquire


# --- CRASH-RECOVERY (T-M2-CRASH-001): create path leaves no duplicate Batch ---


@pytest.mark.asyncio
async def test_create_crash_before_commit_leaves_zero_rows_and_retry_succeeds(
    batch_store: PostgresStore,
) -> None:
    """Crash after insert but before commit: sticky-abort rolls back; retry creates once."""
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = _open_batch(suite=suite)
    with pytest.raises(RuntimeError, match="simulated-create-crash"):
        async with PostgresBatchGateway(pool) as gateway:
            await gateway.create(batch=batch)
            raise RuntimeError("simulated-create-crash")
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 0
    async with PostgresBatchGateway(pool) as gateway:
        result = await gateway.create(batch=batch)
    assert result.replayed is False
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 1


@pytest.mark.asyncio
async def test_create_committed_then_client_retry_is_exact_replay(
    batch_store: PostgresStore,
) -> None:
    """Client crashes after successful create and retries: same key → one row, replayed."""
    pool = batch_store._require_pool()
    await _seed_project(pool)
    suite = await _register_active_suite(pool)
    batch = _open_batch(suite=suite)
    async with PostgresBatchGateway(pool) as gateway:
        first = await gateway.create(batch=batch)
    assert first.replayed is False
    # Simulate lost response / process restart: same identity retried.
    async with PostgresBatchGateway(pool) as gateway:
        second = await gateway.create(batch=batch)
    assert second.replayed is True
    assert second.value.id == first.value.id
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_batches") == 1


@pytest.mark.asyncio
async def test_collection_accept_is_deterministic_after_retry() -> None:
    """Collection crash recovery: re-accepting identical structured result is pure & stable."""
    from qarunner.domain import (
        EstimateConfidence,
        FrameworkLocator,
        ManifestConstraints,
        ManifestInputs,
        ManifestItem,
        WorkEstimate,
        accept_collection_adapter_result,
        canonical_digest,
    )

    def digest(label: str):
        return canonical_digest(
            schema_version="qep.test-collection-input.v1",
            payload={"label": label},
        )

    inputs = ManifestInputs(
        suite_revision_digest=digest("suite"),
        source_digest=digest("source"),
        dependency_digest=digest("deps"),
        config_digest=digest("config"),
        runner_digest=digest("runner"),
        collection_contract_version="qep.pytest-collection.v1",
    )
    item = ManifestItem(
        item_index=0,
        stable_case_id="tests/test_shop.py::test_checkout",
        framework_locator=FrameworkLocator(
            schema_version="qep.pytest-locator.v1",
            kind="pytest_nodeid",
            parts=(
                ("file", "tests/test_shop.py"),
                ("node", "tests/test_shop.py::test_checkout"),
            ),
        ),
        atomic_group_id="tests/test_shop.py::test_checkout",
        resource_profile_id="profile-default",
        constraints=ManifestConstraints(
            serial_group=None,
            environment_requirements=(),
            account_requirements=(),
            data_lease_requirements=(),
        ),
        estimate=WorkEstimate(duration_ms=100, confidence=EstimateConfidence.MEDIUM),
        tags=("regression",),
        selection_metadata_digest=digest("selection"),
    )
    first = accept_collection_adapter_result(
        framework="pytest",
        expected_inputs=inputs,
        claimed_inputs=inputs,
        items=(item,),
        manifest_id="manifest-001",
        batch_id="batch-001",
    )
    second = accept_collection_adapter_result(
        framework="pytest",
        expected_inputs=inputs,
        claimed_inputs=inputs,
        items=(item,),
        manifest_id="manifest-001",
        batch_id="batch-001",
    )
    assert first.digest == second.digest
    assert first.items == second.items
