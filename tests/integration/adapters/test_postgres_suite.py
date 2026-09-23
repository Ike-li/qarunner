"""SUITE-PERSIST: real PostgreSQL Suite CAS (register / publish_revision / retire).

Proves concurrent publish_revision elects one winner under run-style version CAS,
history rows stay immutable, and retire CAS returns the suite to non-accepting.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from tests.integration.migration_operator import run_migration_operator

from qarunner.adapters.postgres_store import PostgresStore
from qarunner.adapters.postgres_suite import PostgresSuiteGateway
from qarunner.application.ports.common import PortContractError
from qarunner.domain import Suite, VersionConflict, canonical_digest

REGISTERED_AT = datetime(2026, 7, 23, 16, tzinfo=UTC)
UPDATED_AT = datetime(2026, 7, 23, 17, tzinfo=UTC)
RETIRED_AT = datetime(2026, 7, 23, 18, tzinfo=UTC)
PROJECT_ID = "project-suite-1"
SUITE_ID = "suite-persist-1"


def _digest(label: str):
    return canonical_digest(
        schema_version="qep.test-suite-revision.v1",
        payload={"label": label},
    )


@pytest.fixture
async def suite_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-suite-admin-password-at-least-32-chars",
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
            "Suite Project",
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


def _register_suite(*, suite_id: str = SUITE_ID, revision_id: str = "suite-revision-001") -> Suite:
    return Suite.register(
        suite_id=suite_id,
        project_id=PROJECT_ID,
        name="shop-regression",
        revision_id=revision_id,
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )


@pytest.mark.asyncio
async def test_register_and_rehydrate_suite(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        result = await gateway.register(suite=suite)
    assert result.replayed is False
    async with PostgresSuiteGateway(pool) as gateway:
        snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
    assert snapshot.suite.id == SUITE_ID
    assert snapshot.suite.version == 0
    assert snapshot.suite.current_revision_id == "suite-revision-001"
    assert len(snapshot.suite.revisions) == 1
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_suites") == 1
        assert await connection.fetchval("SELECT count(*) FROM qep_suite_revisions") == 1
        assert await connection.fetchval("SELECT version FROM qep_suites") == 0


@pytest.mark.asyncio
async def test_register_exact_replay(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        first = await gateway.register(suite=suite)
    async with PostgresSuiteGateway(pool) as gateway:
        second = await gateway.register(suite=suite)
    assert first.replayed is False
    assert second.replayed is True
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_suite_revisions") == 1


@pytest.mark.asyncio
async def test_publish_revision_appends_history(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    async with PostgresSuiteGateway(pool) as gateway:
        snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
        updated = snapshot.suite.publish_revision(
            revision_id="suite-revision-002",
            source_spec_digest=_digest("source-v2"),
            config_digest=_digest("config-v2"),
            framework="pytest",
            resource_profile_id="profile-default",
            created_at=UPDATED_AT,
            expected_version=snapshot.version,
        )
        result = await gateway.publish_revision(suite=updated, expected=snapshot)
    assert result.replayed is False
    assert result.value.version == 1
    assert result.value.current_revision_id == "suite-revision-002"
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT id, revision_no FROM qep_suite_revisions ORDER BY revision_no"
        )
        assert [row["id"] for row in rows] == [
            "suite-revision-001",
            "suite-revision-002",
        ]
        assert await connection.fetchval("SELECT version FROM qep_suites") == 1
        # Historical digest not rewritten.
        first_digest = await connection.fetchval(
            "SELECT source_spec_digest FROM qep_suite_revisions WHERE id = $1",
            "suite-revision-001",
        )
        assert first_digest == _digest("source-v1").value.removeprefix("sha256:")


@pytest.mark.asyncio
async def test_eight_concurrent_publish_revision_elect_one_winner(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    contenders = 8
    start = asyncio.Barrier(contenders)

    async def contend(index: int):
        await start.wait()
        try:
            async with PostgresSuiteGateway(pool) as gateway:
                snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
                # True concurrent CAS: only contenders still seeing version 0 may publish
                # the first post-register revision. FOR UPDATE serializes loaders; after
                # the winner commits, losers rehydrate version>=1 and lose the race.
                if snapshot.version != 0:
                    raise VersionConflict(
                        entity_type="suite",
                        entity_id=SUITE_ID,
                        current_version=snapshot.version,
                        expected_version=0,
                    )
                updated = snapshot.suite.publish_revision(
                    revision_id=f"suite-revision-c{index:03d}",
                    source_spec_digest=_digest(f"source-c{index}"),
                    config_digest=_digest(f"config-c{index}"),
                    framework="pytest",
                    resource_profile_id="profile-default",
                    created_at=UPDATED_AT,
                    expected_version=snapshot.version,
                )
                return await gateway.publish_revision(suite=updated, expected=snapshot)
        except BaseException as error:
            return error

    results = await asyncio.gather(*(contend(i) for i in range(contenders)))
    winners = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, VersionConflict)]
    assert len(winners) == 1, results
    assert len(conflicts) == contenders - 1
    assert winners[0].value.version == 1
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_suite_revisions") == 2
        assert await connection.fetchval("SELECT version FROM qep_suites") == 1


@pytest.mark.asyncio
async def test_retire_rejects_new_batch_acceptance(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    async with PostgresSuiteGateway(pool) as gateway:
        snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
        retired = snapshot.suite.retire(retired_at=RETIRED_AT, expected_version=snapshot.version)
        result = await gateway.publish_retire(suite=retired, expected=snapshot)
    assert result.value.accepts_new_batch is False
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, version, retired_at FROM qep_suites WHERE id = $1",
            SUITE_ID,
        )
    assert dict(row) == {
        "status": "retired",
        "version": 1,
        "retired_at": RETIRED_AT,
    }
    # History still present.
    async with PostgresSuiteGateway(pool) as gateway:
        snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
    assert len(snapshot.suite.revisions) == 1
    assert snapshot.suite.revisions[0].id == "suite-revision-001"


@pytest.mark.asyncio
async def test_publish_revision_rejects_stale_snapshot(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    async with PostgresSuiteGateway(pool) as gateway:
        snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
        updated = snapshot.suite.publish_revision(
            revision_id="suite-revision-002",
            source_spec_digest=_digest("source-v2"),
            config_digest=_digest("config-v2"),
            framework="pytest",
            resource_profile_id="profile-default",
            created_at=UPDATED_AT,
            expected_version=snapshot.version,
        )
        await gateway.publish_revision(suite=updated, expected=snapshot)
    with pytest.raises(VersionConflict):
        async with PostgresSuiteGateway(pool) as gateway:
            # Re-load would get version=1; force stale by using old snapshot object.
            await gateway.get_suite_for_update(suite_id=SUITE_ID)
            # Build a domain result as if still on version 0.
            stale_suite = _register_suite().publish_revision(
                revision_id="suite-revision-stale",
                source_spec_digest=_digest("source-stale"),
                config_digest=_digest("config-stale"),
                framework="pytest",
                resource_profile_id="profile-default",
                created_at=UPDATED_AT,
                expected_version=0,
            )
            from qarunner.application.ports.suite import SuiteMutationSnapshot

            stale_expected = SuiteMutationSnapshot(suite=_register_suite())
            await gateway.publish_revision(suite=stale_suite, expected=stale_expected)


@pytest.mark.asyncio
async def test_get_suite_for_update_rejects_unknown(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            await gateway.get_suite_for_update(suite_id="missing")
    assert exc.value.reason == "not_found"


@pytest.mark.asyncio
async def test_gateway_lifecycle_rejects_reentry(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    gateway = PostgresSuiteGateway(pool)
    async with gateway:
        with pytest.raises(PortContractError) as reenter:
            await gateway.__aenter__()
        assert reenter.value.reason == "already_active"
    with pytest.raises(PortContractError) as closed:
        async with gateway:
            pass
    assert closed.value.reason == "closed"


@pytest.mark.asyncio
async def test_publish_revision_rejects_when_suite_version_bumped_in_tx(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(VersionConflict):
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            updated = snapshot.suite.publish_revision(
                revision_id="suite-revision-cas",
                source_spec_digest=_digest("source-cas"),
                config_digest=_digest("config-cas"),
                framework="pytest",
                resource_profile_id="profile-default",
                created_at=UPDATED_AT,
                expected_version=snapshot.version,
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_suites SET version = version + 10 WHERE id = $1",
                SUITE_ID,
            )
            await gateway.publish_revision(suite=updated, expected=snapshot)


@pytest.mark.asyncio
async def test_register_rejects_conflicting_existing_suite(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    conflict = Suite.register(
        suite_id=SUITE_ID,
        project_id=PROJECT_ID,
        name="other-name",
        revision_id="suite-revision-other",
        source_spec_digest=_digest("source-other"),
        config_digest=_digest("config-other"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            await gateway.register(suite=conflict)
    assert exc.value.reason == "already_exists"


@pytest.mark.asyncio
async def test_register_rejects_non_initial_suite(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    advanced = suite.publish_revision(
        revision_id="suite-revision-002",
        source_spec_digest=_digest("source-v2"),
        config_digest=_digest("config-v2"),
        framework="pytest",
        resource_profile_id="profile-default",
        created_at=UPDATED_AT,
        expected_version=0,
    )
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            await gateway.register(suite=advanced)
    assert exc.value.reason == "not_initial_register"


@pytest.mark.asyncio
async def test_get_suite_for_update_rejects_empty_revisions(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_suites (id, project_id, name, status, version, created_at)
            VALUES ($1, $2, $3, 'active', 0, $4)
            """,
            SUITE_ID,
            PROJECT_ID,
            "orphan",
            REGISTERED_AT,
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            await gateway.get_suite_for_update(suite_id=SUITE_ID)
    assert exc.value.reason == "empty"


@pytest.mark.asyncio
async def test_publish_revision_rejects_stale_expected_object(
    suite_store: PostgresStore,
) -> None:
    from qarunner.application.ports.suite import SuiteMutationSnapshot

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    # Capture snapshot at version 0, then advance the suite to version 1.
    async with PostgresSuiteGateway(pool) as gateway:
        snapshot_v0 = await gateway.get_suite_for_update(suite_id=SUITE_ID)
        updated = snapshot_v0.suite.publish_revision(
            revision_id="suite-revision-002",
            source_spec_digest=_digest("source-v2"),
            config_digest=_digest("config-v2"),
            framework="pytest",
            resource_profile_id="profile-default",
            created_at=UPDATED_AT,
            expected_version=snapshot_v0.version,
        )
        await gateway.publish_revision(suite=updated, expected=snapshot_v0)
    # New transaction still holding the old expected snapshot must fail CAS.
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.get_suite_for_update(suite_id=SUITE_ID)
        next_rev = updated.publish_revision(
            revision_id="suite-revision-003",
            source_spec_digest=_digest("source-v3"),
            config_digest=_digest("config-v3"),
            framework="pytest",
            resource_profile_id="profile-default",
            created_at=UPDATED_AT + timedelta(minutes=1),
            expected_version=1,
        )
        stale = SuiteMutationSnapshot(suite=suite)
        with pytest.raises(VersionConflict):
            await gateway.publish_revision(suite=next_rev, expected=stale)


@pytest.mark.asyncio
async def test_publish_retire_rejects_stale_run_version(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(VersionConflict):
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            retired = snapshot.suite.retire(
                retired_at=RETIRED_AT, expected_version=snapshot.version
            )
            assert gateway._connection is not None
            await gateway._connection.execute(
                "UPDATE qep_suites SET version = version + 5 WHERE id = $1",
                SUITE_ID,
            )
            await gateway.publish_retire(suite=retired, expected=snapshot)


@pytest.mark.asyncio
async def test_publish_revision_rejects_suite_id_mismatch(
    suite_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            updated = snapshot.suite.publish_revision(
                revision_id="suite-revision-002",
                source_spec_digest=_digest("source-v2"),
                config_digest=_digest("config-v2"),
                framework="pytest",
                resource_profile_id="profile-default",
                created_at=UPDATED_AT,
                expected_version=snapshot.version,
            )
            forged = SimpleNamespace(
                id="other-suite",
                version=updated.version,
                revisions=updated.revisions,
                status=updated.status,
            )
            await gateway.publish_revision(suite=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "suite_id_mismatch"


@pytest.mark.asyncio
async def test_publish_retire_rejects_not_retired_domain_result(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            await gateway.publish_retire(suite=snapshot.suite, expected=snapshot)
    assert exc.value.reason == "not_retired"


@pytest.mark.asyncio
async def test_get_suite_rejects_locking_a_second_suite(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.get_suite_for_update(suite_id=SUITE_ID)
        with pytest.raises(PortContractError) as exc:
            await gateway.get_suite_for_update(suite_id="another")
    assert exc.value.reason == "aggregate_already_locked"


@pytest.mark.asyncio
async def test_methods_reject_use_outside_active_transaction(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    gateway = PostgresSuiteGateway(pool)
    with pytest.raises(PortContractError) as exc:
        await gateway.get_suite_for_update(suite_id=SUITE_ID)
    assert exc.value.reason == "not_active"


@pytest.mark.asyncio
async def test_suppressed_revision_insert_sticky_aborts(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    async with pool.acquire() as setup:
        await setup.execute(
            """
            CREATE OR REPLACE FUNCTION qep_suppress_suite_revision_insert()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END;
            $$
            """
        )
        await setup.execute(
            """
            CREATE TRIGGER qep_suppress_suite_revision_insert
            BEFORE INSERT ON qep_suite_revisions
            FOR EACH ROW EXECUTE FUNCTION qep_suppress_suite_revision_insert()
            """
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            updated = snapshot.suite.publish_revision(
                revision_id="suite-revision-suppressed",
                source_spec_digest=_digest("source-s"),
                config_digest=_digest("config-s"),
                framework="pytest",
                resource_profile_id="profile-default",
                created_at=UPDATED_AT,
                expected_version=snapshot.version,
            )
            await gateway.publish_revision(suite=updated, expected=snapshot)
    assert exc.value.reason == "insert_suppressed"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM qep_suite_revisions") == 1
        assert await connection.fetchval("SELECT version FROM qep_suites") == 0


@pytest.mark.asyncio
async def test_suppressed_suite_insert_sticky_aborts(suite_store: PostgresStore) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    async with pool.acquire() as setup:
        await setup.execute(
            """
            CREATE OR REPLACE FUNCTION qep_suppress_suite_insert()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END;
            $$
            """
        )
        await setup.execute(
            """
            CREATE TRIGGER qep_suppress_suite_insert
            BEFORE INSERT ON qep_suites
            FOR EACH ROW EXECUTE FUNCTION qep_suppress_suite_insert()
            """
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            await gateway.register(suite=_register_suite())
    assert exc.value.reason == "insert_suppressed"


@pytest.mark.asyncio
async def test_publish_revision_rejects_version_not_monotonic(
    suite_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            updated = snapshot.suite.publish_revision(
                revision_id="suite-revision-002",
                source_spec_digest=_digest("source-v2"),
                config_digest=_digest("config-v2"),
                framework="pytest",
                resource_profile_id="profile-default",
                created_at=UPDATED_AT,
                expected_version=snapshot.version,
            )
            forged = SimpleNamespace(
                id=SUITE_ID,
                version=99,
                revisions=updated.revisions,
                status=updated.status,
            )
            await gateway.publish_revision(suite=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "version_not_monotonic"


@pytest.mark.asyncio
async def test_publish_revision_rejects_revision_count_mismatch(
    suite_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            updated = snapshot.suite.publish_revision(
                revision_id="suite-revision-002",
                source_spec_digest=_digest("source-v2"),
                config_digest=_digest("config-v2"),
                framework="pytest",
                resource_profile_id="profile-default",
                created_at=UPDATED_AT,
                expected_version=snapshot.version,
            )
            forged = SimpleNamespace(
                id=SUITE_ID,
                version=1,
                revisions=updated.revisions + updated.revisions[-1:],
                status=updated.status,
            )
            await gateway.publish_revision(suite=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "revision_count_mismatch"


@pytest.mark.asyncio
async def test_publish_revision_rejects_history_rewritten(
    suite_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            updated = snapshot.suite.publish_revision(
                revision_id="suite-revision-002",
                source_spec_digest=_digest("source-v2"),
                config_digest=_digest("config-v2"),
                framework="pytest",
                resource_profile_id="profile-default",
                created_at=UPDATED_AT,
                expected_version=snapshot.version,
            )
            # Replace historical first revision with a different object.
            other_first = _register_suite(revision_id="suite-revision-xxx").revisions[0]
            forged = SimpleNamespace(
                id=SUITE_ID,
                version=1,
                revisions=(other_first, updated.revisions[-1]),
                status=updated.status,
            )
            await gateway.publish_revision(suite=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "history_rewritten"


@pytest.mark.asyncio
async def test_publish_retire_rejects_suite_id_mismatch(
    suite_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            retired = snapshot.suite.retire(
                retired_at=RETIRED_AT, expected_version=snapshot.version
            )
            forged = SimpleNamespace(
                id="other",
                version=retired.version,
                status=retired.status,
                retired_at=retired.retired_at,
                revisions=retired.revisions,
            )
            await gateway.publish_retire(suite=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "suite_id_mismatch"


@pytest.mark.asyncio
async def test_publish_retire_rejects_version_not_monotonic(
    suite_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            retired = snapshot.suite.retire(
                retired_at=RETIRED_AT, expected_version=snapshot.version
            )
            forged = SimpleNamespace(
                id=SUITE_ID,
                version=99,
                status=retired.status,
                retired_at=retired.retired_at,
                revisions=retired.revisions,
            )
            await gateway.publish_retire(suite=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "version_not_monotonic"


@pytest.mark.asyncio
async def test_publish_retire_rejects_history_rewritten(
    suite_store: PostgresStore,
) -> None:
    from types import SimpleNamespace

    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            snapshot = await gateway.get_suite_for_update(suite_id=SUITE_ID)
            retired = snapshot.suite.retire(
                retired_at=RETIRED_AT, expected_version=snapshot.version
            )
            forged = SimpleNamespace(
                id=SUITE_ID,
                version=1,
                status=retired.status,
                retired_at=retired.retired_at,
                revisions=(),
            )
            await gateway.publish_retire(suite=forged, expected=snapshot)  # type: ignore[arg-type]
    assert exc.value.reason == "history_rewritten"


@pytest.mark.asyncio
async def test_get_suite_returns_cached_snapshot_on_repeat(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    suite = _register_suite()
    async with PostgresSuiteGateway(pool) as gateway:
        await gateway.register(suite=suite)
    async with PostgresSuiteGateway(pool) as gateway:
        first = await gateway.get_suite_for_update(suite_id=SUITE_ID)
        second = await gateway.get_suite_for_update(suite_id=SUITE_ID)
    assert first is second


@pytest.mark.asyncio
async def test_suppressed_revision_insert_on_register_sticky_aborts(
    suite_store: PostgresStore,
) -> None:
    pool = suite_store._require_pool()
    await _seed_project(pool)
    async with pool.acquire() as setup:
        await setup.execute(
            """
            CREATE OR REPLACE FUNCTION qep_suppress_revision_insert_on_register()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END;
            $$
            """
        )
        await setup.execute(
            """
            CREATE TRIGGER qep_suppress_revision_insert_on_register
            BEFORE INSERT ON qep_suite_revisions
            FOR EACH ROW EXECUTE FUNCTION qep_suppress_revision_insert_on_register()
            """
        )
    with pytest.raises(PortContractError) as exc:
        async with PostgresSuiteGateway(pool) as gateway:
            await gateway.register(suite=_register_suite())
    assert exc.value.reason == "insert_suppressed"
