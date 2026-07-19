"""Real PostgreSQL transaction coverage for greenfield Batch finalization."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from tests.integration.adapters.test_postgres_run_finalization import _seed_finalizable_run
from tests.integration.migration_operator import run_migration_operator
from tests.unit.application.test_finalize_run import command as run_command
from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _unknown_basis_inputs

from qarunner.adapters.postgres_run_finalization import PostgresRunFinalizationUnitOfWork
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
from qarunner.application.ports.batch_finalization import (
    BatchFinalizationGateway,
    BatchFinalizationMutationSnapshot,
    BatchFinalizationProjection,
    BatchFinalizationPublication,
    BatchFinalizationSideEffect,
    BatchFinalizationSourceSnapshot,
    FinalizeBatchAuthority,
)
from qarunner.application.run_finalization import FinalizeRun
from qarunner.domain import BatchFinalizationBasis, canonical_digest


@pytest.fixture
async def batch_finalization_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-batch-finalization-admin-password-at-least-32-chars",
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


@pytest.mark.asyncio
async def test_postgres_batch_finalization_uow_implements_gateway() -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )

    assert issubclass(PostgresBatchFinalizationUnitOfWork, BatchFinalizationGateway)


@pytest.mark.asyncio
async def test_batch_finalization_commits_all_facts_once_and_exactly_replays(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)

    async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
        first = await FinalizeBatch(gateway=gateway).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )

    assert not first.replayed
    async with pool.acquire() as connection:
        persisted = await _publication_snapshot(connection)
        basis_row = await connection.fetchrow(
            "SELECT basis_digest, payload, readiness_ref FROM qep_batch_finalization_bases"
        )
        readiness_ref = await connection.fetchval(
            "SELECT finalization_readiness_ref FROM qep_batches WHERE id = $1",
            candidate.batch_id,
        )
        resolution_rows = await connection.fetch(
            """
            SELECT item_index, resolution_digest, payload
            FROM qep_batch_item_resolutions
            ORDER BY item_index
            """
        )
        audit_row = await connection.fetchrow(
            """
            SELECT before_digest, after_digest, payload
            FROM qep_audit_events
            WHERE action = 'finalize_batch'
            """
        )
        outbox_row = await connection.fetchrow(
            """
            SELECT id, event_id, aggregate_type, aggregate_id, event_type,
                   payload_digest, payload
            FROM qep_outbox_events
            WHERE event_type = 'batch.finalized.v1'
            """
        )

    assert persisted == {
        "batch_state": candidate.batch_outcome.value,
        "batch_version": candidate.source_batch_version + 1,
        "basis_count": 1,
        "resolution_count": candidate.item_count,
        "audit_count": 1,
        "outbox_count": 1,
    }
    assert basis_row is not None
    assert basis_row["readiness_ref"] is not None
    assert basis_row["readiness_ref"] == readiness_ref
    assert basis_row["basis_digest"] == _digest_hex(candidate.basis_digest)
    assert json.loads(basis_row["payload"]) == candidate.canonical_payload()
    assert [json.loads(row["payload"]) for row in resolution_rows] == [
        entry.canonical_payload() for entry in candidate.resolution_set.entries
    ]
    assert [row["resolution_digest"] for row in resolution_rows] == [
        _digest_hex(entry.batch_item_resolution_digest)
        for entry in candidate.resolution_set.entries
    ]
    assert audit_row is not None
    assert audit_row["before_digest"] is None
    assert audit_row["after_digest"] == _digest_hex(candidate.basis_digest)
    assert json.loads(audit_row["payload"])["authority_digest"] == (
        authority.authority_digest.value
    )
    assert outbox_row is not None
    event_payload = _finalized_event_payload(candidate, authority)
    identity = canonical_digest(
        schema_version="qep.batch-finalized-identity.v1",
        payload={
            "batch_id": candidate.batch_id,
            "source_batch_version": candidate.source_batch_version,
            "basis_digest": candidate.basis_digest.value,
        },
    )
    event_id = f"batch-finalized-event-{_digest_hex(identity)}"
    assert (
        outbox_row["id"],
        outbox_row["event_id"],
        outbox_row["aggregate_type"],
        outbox_row["aggregate_id"],
        outbox_row["event_type"],
        outbox_row["payload_digest"],
        json.loads(outbox_row["payload"]),
    ) == (
        event_id,
        event_id,
        "batch",
        candidate.batch_id,
        "batch.finalized.v1",
        _digest_hex(
            canonical_digest(
                schema_version="qep.batch-finalized-event-payload.v1",
                payload=event_payload,
            )
        ),
        event_payload,
    )

    async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
        replay = await FinalizeBatch(gateway=gateway).execute(
            FinalizeBatchCommand(candidate, expected_batch_version=99)
        )

    assert replay.replayed
    assert replay.projection == first.projection
    async with pool.acquire() as connection:
        assert await _publication_snapshot(connection) == persisted


@pytest.mark.asyncio
async def test_live_authority_is_revalidated_before_terminal_replay(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
        await FinalizeBatch(gateway=gateway).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_batches SET write_epoch = write_epoch + 1 WHERE id = $1",
            candidate.batch_id,
        )

    with pytest.raises(AuthorityStateConflict) as superseded:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, expected_batch_version=99)
            )

    assert superseded.value.reason == "batch_finalization_authority_superseded"
    async with pool.acquire() as connection:
        assert (await _publication_snapshot(connection))["outbox_count"] == 1


@pytest.mark.asyncio
async def test_missing_audit_insert_sticky_aborts_and_rolls_back_every_participant(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
    from qarunner.application.ports.common import PortContractError

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_batch_finalization_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action = 'finalize_batch' THEN
                    RETURN NULL;
                END IF;
                RETURN NEW;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_batch_finalization_audit
            BEFORE INSERT ON qep_audit_events
            FOR EACH ROW EXECUTE FUNCTION suppress_batch_finalization_audit()
            """
        )

    async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
        with pytest.raises(AuthorityStateConflict) as missing:
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, candidate.source_batch_version)
            )
        assert missing.value.reason == "batch_finalization_audit_write_missing"
        with pytest.raises(PortContractError, match="aborted"):
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, candidate.source_batch_version)
            )

    async with pool.acquire() as connection:
        assert await _publication_snapshot(connection) == {
            "batch_state": "finalizing",
            "batch_version": candidate.source_batch_version,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 0,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ("qep_batch_item_resolutions", "batch_item_resolution_write_missing"),
        ("qep_batch_finalization_bases", "batch_finalization_basis_write_missing"),
        ("qep_outbox_events", "batch_finalization_outbox_write_missing"),
    ],
)
async def test_missing_mandatory_insert_aborts_the_whole_batch_publication(
    batch_finalization_store: PostgresStore,
    table: str,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_batch_finalization_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            f"""
            CREATE TRIGGER suppress_batch_finalization_insert
            BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION suppress_batch_finalization_insert()
            """
        )

    with pytest.raises(AuthorityStateConflict) as missing:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, candidate.source_batch_version)
            )

    assert missing.value.reason == reason
    async with pool.acquire() as connection:
        assert await _publication_snapshot(connection) == {
            "batch_state": "finalizing",
            "batch_version": candidate.source_batch_version,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 0,
        }


@pytest.mark.asyncio
async def test_batch_cas_failure_rolls_back_all_staged_finalization_facts(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.domain import VersionConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_batch_finalization_update() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_batch_finalization_update
            BEFORE UPDATE ON qep_batches
            FOR EACH ROW EXECUTE FUNCTION suppress_batch_finalization_update()
            """
        )

    with pytest.raises(VersionConflict):
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, candidate.source_batch_version)
            )

    async with pool.acquire() as connection:
        assert await _publication_snapshot(connection) == {
            "batch_state": "finalizing",
            "batch_version": candidate.source_batch_version,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 0,
        }


@pytest.mark.asyncio
async def test_forged_source_snapshot_is_rejected_after_database_rebuild(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    forged = replace(
        authority,
        source_snapshot=replace(
            authority.source_snapshot,
            completeness_proof_digest=canonical_digest(
                schema_version="qep.test-forged-completeness.v1",
                payload={"batch_id": candidate.batch_id},
            ),
        ),
    )

    with pytest.raises(AuthorityStateConflict) as mismatch:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=forged) as gateway:
            await gateway.require_finalization_authority(batch_id=candidate.batch_id)

    assert mismatch.value.reason == "batch_finalization_source_snapshot_mismatch"


@pytest.mark.asyncio
async def test_publish_revalidates_sources_changed_after_the_locked_snapshot(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    expected_authority = _authority(candidate)

    async with PostgresBatchFinalizationUnitOfWork(pool, authority=expected_authority) as gateway:
        authority = await gateway.require_finalization_authority(batch_id=candidate.batch_id)
        assert await gateway.lookup_stored(identity_scope=authority.identity_scope) is None
        snapshot = await gateway.get_mutation_snapshot_for_update(batch_id=candidate.batch_id)
        changed_policy = replace(candidate.policy, max_test_failed_items=2)
        changed_payload = _policy_payload(candidate)
        changed_payload["max_test_failed_items"] = 2
        async with pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE qep_batch_success_policies
                SET max_test_failed_items = 2, policy_digest = $1, payload = $2
                WHERE id = $3 AND policy_version = $4
                """,
                _digest_hex(changed_policy.policy_digest),
                json.dumps(changed_payload, sort_keys=True),
                changed_policy.policy_id,
                changed_policy.policy_version,
            )

        with pytest.raises(AuthorityStateConflict) as mismatch:
            await gateway.publish_finalization(
                publication=_publication(candidate, authority, snapshot)
            )

    assert mismatch.value.reason == "batch_finalization_source_snapshot_mismatch"
    async with pool.acquire() as connection:
        assert await _publication_snapshot(connection) == {
            "batch_state": "finalizing",
            "batch_version": candidate.source_batch_version,
            "basis_count": 0,
            "resolution_count": 0,
            "audit_count": 0,
            "outbox_count": 0,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("missing_policy", "batch_finalization_source_missing"),
        ("manifest_coverage", "batch_finalization_manifest_coverage_invalid"),
        ("duplicate_intent", "stored_cancellation_cardinality_invalid"),
        ("intent_envelope", "batch_finalization_cancellation_source_invalid"),
        ("scope_non_object", "batch_finalization_source_integrity_invalid"),
        ("scope_payload", "stored_cancellation_scope_integrity_invalid"),
        ("run_basis_non_object", "batch_finalization_source_integrity_invalid"),
        ("run_basis_payload", "stored_run_finalization_integrity_invalid"),
        ("run_projection", "stored_run_finalization_integrity_invalid"),
        ("run_resolution_non_object", "batch_finalization_source_integrity_invalid"),
        ("run_resolution_payload", "batch_finalization_run_resolution_invalid"),
        ("policy_non_object", "batch_finalization_source_integrity_invalid"),
        ("policy_digest", "stored_batch_success_policy_integrity_invalid"),
        ("policy_digest_type", "batch_finalization_source_integrity_invalid"),
    ],
)
async def test_corrupt_frozen_source_facts_fail_closed(
    batch_finalization_store: PostgresStore,
    corruption: str,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with pool.acquire() as connection:
        await _corrupt_source(connection, corruption)

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await gateway.require_finalization_authority(batch_id=candidate.batch_id)

    assert invalid.value.reason == reason


@pytest.mark.asyncio
async def test_finalization_without_cancellation_persists_unknown_lineage_sources(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )

    candidate = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)

    async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
        result = await FinalizeBatch(gateway=gateway).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )

    assert not result.replayed
    assert result.projection.outcome.value == "partial"
    assert candidate.batch_cancellation_intent_digest is None
    assert candidate.non_run_refs == ()
    assert len(candidate.unknown_fact_refs) == 1
    async with pool.acquire() as connection:
        resolution = await connection.fetchrow(
            """
            SELECT classification, source_kind, payload
            FROM qep_batch_item_resolutions
            """
        )
    assert resolution is not None
    assert (resolution["classification"], resolution["source_kind"]) == (
        "unknown_lineage",
        "run_resolution",
    )
    assert json.loads(resolution["payload"])["source_item_resolution_digest"] == (
        candidate.unknown_fact_refs[0].source_item_resolution_digest.value
    )


@pytest.mark.asyncio
async def test_batch_finalization_uow_is_one_shot_and_one_aggregate_scoped(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityPermissionDenied
    from qarunner.application.ports.common import PortContractError

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    inactive = PostgresBatchFinalizationUnitOfWork(pool, authority=authority)
    with pytest.raises(PortContractError, match="not_active"):
        await inactive.require_finalization_authority(batch_id=candidate.batch_id)
    with pytest.raises(PortContractError, match="not_active"):
        await inactive.__aexit__(None, None, None)

    unit_of_work = PostgresBatchFinalizationUnitOfWork(pool, authority=authority)
    async with unit_of_work as gateway:
        with pytest.raises(PortContractError, match="already_active"):
            await gateway.__aenter__()
        with pytest.raises(PortContractError, match="authority_not_locked"):
            await gateway.lookup_stored(
                identity_scope=(
                    "qep.batch-finalization-basis.v1",
                    candidate.batch_id,
                    candidate.source_batch_version,
                )
            )
        with pytest.raises(AuthorityPermissionDenied):
            await gateway.require_finalization_authority(batch_id="batch-other")
        locked = await gateway.require_finalization_authority(batch_id=candidate.batch_id)
        assert await gateway.require_finalization_authority(batch_id=candidate.batch_id) == locked
        with pytest.raises(PortContractError, match="aggregate_already_locked"):
            await gateway.require_finalization_authority(batch_id="batch-other")
        with pytest.raises(PortContractError, match="unsupported"):
            await gateway.lookup_stored(
                identity_scope=("unsupported", candidate.batch_id, candidate.source_batch_version)
            )
        with pytest.raises(PortContractError, match="authority_not_locked"):
            await gateway.get_mutation_snapshot_for_update(batch_id="batch-other")

    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.require_finalization_authority(batch_id=candidate.batch_id)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()

    missing_authority = replace(
        authority,
        source_snapshot=replace(authority.source_snapshot, batch_id="batch-missing"),
    )
    with pytest.raises(AuthorityPermissionDenied):
        async with PostgresBatchFinalizationUnitOfWork(
            pool, authority=missing_authority
        ) as gateway:
            await gateway.require_finalization_authority(batch_id="batch-missing")


@pytest.mark.asyncio
async def test_direct_publication_requires_locked_current_authority_and_snapshot(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
    from qarunner.domain import VersionConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    expected_authority = _authority(candidate)

    async with PostgresBatchFinalizationUnitOfWork(pool, authority=expected_authority) as gateway:
        authority = await gateway.require_finalization_authority(batch_id=candidate.batch_id)
        snapshot = await gateway.get_mutation_snapshot_for_update(batch_id=candidate.batch_id)
        superseded = replace(
            authority,
            authority_digest=canonical_digest(
                schema_version="qep.test-superseded-authority.v1",
                payload={"batch_id": candidate.batch_id},
            ),
        )
        with pytest.raises(AuthorityStateConflict) as mismatch:
            await gateway.publish_finalization(
                publication=_publication(candidate, superseded, snapshot)
            )
        assert mismatch.value.reason == "publication_authority_superseded"

    expected_snapshot = BatchFinalizationMutationSnapshot(
        batch_id=candidate.batch_id,
        batch_version=candidate.source_batch_version,
        state=__import__("qarunner.domain", fromlist=["BatchState"]).BatchState.FINALIZING,
        source_snapshot=expected_authority.source_snapshot,
    )
    async with PostgresBatchFinalizationUnitOfWork(pool, authority=expected_authority) as gateway:
        authority = await gateway.require_finalization_authority(batch_id=candidate.batch_id)
        with pytest.raises(VersionConflict):
            await gateway.publish_finalization(
                publication=_publication(candidate, authority, expected_snapshot)
            )

    async with pool.acquire() as connection:
        assert (await _publication_snapshot(connection))["basis_count"] == 0


@pytest.mark.asyncio
async def test_direct_publication_exactly_replays_and_rejects_changed_stored_digest(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.domain import IdempotencyConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    expected_authority = _authority(candidate)
    async with PostgresBatchFinalizationUnitOfWork(pool, authority=expected_authority) as gateway:
        first = await FinalizeBatch(gateway=gateway).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )

    original_snapshot = BatchFinalizationMutationSnapshot(
        batch_id=candidate.batch_id,
        batch_version=candidate.source_batch_version,
        state=__import__("qarunner.domain", fromlist=["BatchState"]).BatchState.FINALIZING,
        source_snapshot=expected_authority.source_snapshot,
    )
    async with PostgresBatchFinalizationUnitOfWork(pool, authority=expected_authority) as gateway:
        authority = await gateway.require_finalization_authority(batch_id=candidate.batch_id)
        replay = await gateway.publish_finalization(
            publication=_publication(candidate, authority, original_snapshot)
        )
    assert replay.replayed
    assert replay.value == first.projection

    changed_payload = candidate.canonical_payload()
    changed_payload["stored_extension"] = "different-semantic-basis"
    changed_digest = canonical_digest(
        schema_version="qep.batch-finalization-basis.v1",
        payload=changed_payload,
    )
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_batch_finalization_bases
            SET basis_digest = $1, payload = $2
            WHERE batch_id = $3
            """,
            _digest_hex(changed_digest),
            json.dumps(changed_payload, sort_keys=True),
            candidate.batch_id,
        )

    with pytest.raises(IdempotencyConflict):
        async with PostgresBatchFinalizationUnitOfWork(
            pool, authority=expected_authority
        ) as gateway:
            authority = await gateway.require_finalization_authority(batch_id=candidate.batch_id)
            await gateway.publish_finalization(
                publication=_publication(candidate, authority, original_snapshot)
            )
    async with pool.acquire() as connection:
        assert (await _publication_snapshot(connection))["outbox_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_payload", ["[]", "{}"])
async def test_corrupt_stored_basis_payload_fails_with_integrity_conflict(
    batch_finalization_store: PostgresStore,
    stored_payload: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
        await FinalizeBatch(gateway=gateway).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_batch_finalization_bases SET payload = $1::jsonb",
            stored_payload,
        )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, expected_batch_version=99)
            )

    assert corrupt.value.reason == "stored_batch_finalization_integrity_invalid"


@pytest.mark.asyncio
async def test_terminal_replay_rejects_basis_bound_to_a_different_readiness_fact(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
        await FinalizeBatch(gateway=gateway).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_batch_finalization_readiness_facts (
                ref, batch_id, source_batch_version, batch_version,
                manifest_digest, shard_plan_digest, canonical_run_set_digest,
                success_policy_digest, batch_cancellation_intent_digest,
                readiness_digest, authority_digest, write_epoch,
                compatibility_epoch, state_model_version, payload, recorded_at
            )
            SELECT
                'readiness-other', batch_id, 0, 1,
                manifest_digest, shard_plan_digest, canonical_run_set_digest,
                success_policy_digest, batch_cancellation_intent_digest,
                $1, authority_digest, write_epoch,
                compatibility_epoch, state_model_version, payload, recorded_at
            FROM qep_batch_finalization_readiness_facts
            """,
            "f" * 64,
        )
        await connection.execute(
            "UPDATE qep_batch_finalization_bases SET readiness_ref = 'readiness-other'"
        )

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, expected_batch_version=99)
            )

    assert invalid.value.reason == "stored_batch_finalization_binding_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("missing_ref", "batch_finalization_readiness_binding_missing"),
        ("payload_fields", "batch_finalization_readiness_integrity_invalid"),
        ("payload_binding", "batch_finalization_readiness_binding_invalid"),
    ],
)
async def test_finalization_authority_rejects_readiness_fact_corruption(
    batch_finalization_store: PostgresStore,
    corruption: str,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    async with pool.acquire() as connection:
        if corruption == "missing_ref":
            await connection.execute("UPDATE qep_batches SET finalization_readiness_ref = NULL")
        elif corruption == "payload_fields":
            await connection.execute(
                "UPDATE qep_batch_finalization_readiness_facts SET payload = '{}'::jsonb"
            )
        else:
            payload = json.loads(
                await connection.fetchval(
                    "SELECT payload FROM qep_batch_finalization_readiness_facts"
                )
            )
            payload["manifest_id"] = "manifest-other"
            readiness_digest = canonical_digest(
                schema_version="qep.batch-finalization-readiness.v1",
                payload=payload,
            )
            await connection.execute(
                """
                UPDATE qep_batch_finalization_readiness_facts
                SET payload = $1, readiness_digest = $2
                """,
                json.dumps(payload, sort_keys=True),
                _digest_hex(readiness_digest),
            )

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await gateway.require_finalization_authority(batch_id=candidate.batch_id)

    assert invalid.value.reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("after_finalization", "reason"),
    [
        (False, "batch_finalization_source_state_invalid"),
        (True, "stored_batch_finalization_binding_invalid"),
    ],
)
async def test_batch_state_and_stored_terminal_binding_must_match_the_authority(
    batch_finalization_store: PostgresStore,
    after_finalization: bool,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    authority = _authority(candidate)
    if after_finalization:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await FinalizeBatch(gateway=gateway).execute(
                FinalizeBatchCommand(candidate, candidate.source_batch_version)
            )
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_batches SET state = 'running' WHERE id = $1",
            candidate.batch_id,
        )

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresBatchFinalizationUnitOfWork(pool, authority=authority) as gateway:
            await gateway.require_finalization_authority(batch_id=candidate.batch_id)

    assert invalid.value.reason == reason


@pytest.mark.asyncio
async def test_locked_mutation_snapshot_rejects_source_drift_after_authority_check(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    expected_authority = _authority(candidate)

    async with PostgresBatchFinalizationUnitOfWork(pool, authority=expected_authority) as gateway:
        await gateway.require_finalization_authority(batch_id=candidate.batch_id)
        changed_policy = replace(candidate.policy, max_test_failed_items=2)
        changed_payload = _policy_payload(candidate)
        changed_payload["max_test_failed_items"] = 2
        async with pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE qep_batch_success_policies
                SET max_test_failed_items = 2, policy_digest = $1, payload = $2
                WHERE id = $3 AND policy_version = $4
                """,
                _digest_hex(changed_policy.policy_digest),
                json.dumps(changed_payload, sort_keys=True),
                changed_policy.policy_id,
                changed_policy.policy_version,
            )

        with pytest.raises(AuthorityStateConflict) as mismatch:
            await gateway.get_mutation_snapshot_for_update(batch_id=candidate.batch_id)

    assert mismatch.value.reason == "batch_finalization_source_snapshot_mismatch"


@pytest.mark.asyncio
async def test_scope_fact_without_cancellation_intent_fails_closed(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
    from qarunner.domain import (
        BatchCancellationResolutionKind,
        BatchCancellationScopeItem,
        RunItemKey,
    )

    candidate = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    intent_digest = canonical_digest(
        schema_version="qep.test-orphan-cancel-intent.v1",
        payload={"batch_id": candidate.batch_id},
    )
    orphan = BatchCancellationScopeItem(
        batch_id=candidate.batch_id,
        batch_cancellation_intent_digest=intent_digest,
        manifest_id=candidate.manifest_id,
        manifest_digest=candidate.manifest_digest,
        manifest_item_key=RunItemKey(candidate.manifest_id, 0),
        shard_plan_version=candidate.shard_plan_version,
        shard_plan_digest=candidate.shard_plan_digest,
        resolution_kind=BatchCancellationResolutionKind.NOT_EXECUTED,
        run_id=None,
        source_run_version=None,
        recorded_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )
    async with pool.acquire() as connection:
        await _insert_scope_item(connection, orphan)

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresBatchFinalizationUnitOfWork(
            pool, authority=_authority(candidate)
        ) as gateway:
            await gateway.require_finalization_authority(batch_id=candidate.batch_id)

    assert invalid.value.reason == "batch_finalization_cancellation_source_invalid"


@pytest.mark.asyncio
async def test_unknown_resolution_requires_structured_adjudication_source(
    batch_finalization_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict

    candidate = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    async with pool.acquire() as connection:
        payload = json.loads(
            await connection.fetchval("SELECT payload FROM qep_run_item_resolutions")
        )
        payload["effective"] = "missing-adjudication-object"
        digest = canonical_digest(
            schema_version="qep.run-item-resolution-set.v1",
            payload={"projection_kind": "item", **payload},
        )
        await connection.execute(
            """
            UPDATE qep_run_item_resolutions
            SET item_resolution_digest = $1, payload = $2
            """,
            _digest_hex(digest),
            json.dumps(payload, sort_keys=True),
        )

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresBatchFinalizationUnitOfWork(
            pool, authority=_authority(candidate)
        ) as gateway:
            await gateway.require_finalization_authority(batch_id=candidate.batch_id)

    assert invalid.value.reason == "batch_finalization_unknown_source_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("fanout_version", "batch_finalization_run_fanout_invalid"),
        ("not_executed_overlap", "batch_finalization_item_overlap"),
    ],
)
async def test_cancellation_scope_must_bind_one_nonoverlapping_resolution_per_item(
    batch_finalization_store: PostgresStore,
    corruption: str,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization import (
        PostgresBatchFinalizationUnitOfWork,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
    from qarunner.domain import BatchCancellationResolutionKind

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    pool = batch_finalization_store._require_pool()
    await _seed_batch_finalization_sources(pool, candidate)
    run_fanout = next(
        item
        for item in candidate.cancellation_scope_items
        if item.resolution_kind is BatchCancellationResolutionKind.RUN_FANOUT
    )
    changed = (
        replace(run_fanout, source_run_version=run_fanout.source_run_version + 1)
        if corruption == "fanout_version"
        else replace(
            run_fanout,
            resolution_kind=BatchCancellationResolutionKind.NOT_EXECUTED,
            run_id=None,
            source_run_version=None,
        )
    )
    async with pool.acquire() as connection:
        await connection.execute(
            """
            DELETE FROM qep_batch_cancellation_scope_items
            WHERE batch_id = $1 AND manifest_id = $2 AND item_index = $3
            """,
            run_fanout.batch_id,
            run_fanout.manifest_id,
            run_fanout.manifest_item_key.item_index,
        )
        await _insert_scope_item(connection, changed)

    with pytest.raises(AuthorityStateConflict) as invalid:
        async with PostgresBatchFinalizationUnitOfWork(
            pool, authority=_authority(candidate)
        ) as gateway:
            await gateway.require_finalization_authority(batch_id=candidate.batch_id)

    assert invalid.value.reason == reason


def _authority(candidate: BatchFinalizationBasis) -> FinalizeBatchAuthority:
    return FinalizeBatchAuthority(
        source_snapshot=BatchFinalizationSourceSnapshot.from_basis(candidate),
        authority_digest=canonical_digest(
            schema_version="qep.test-batch-finalization-authority.v1",
            payload={"batch_id": candidate.batch_id},
        ),
        write_epoch=1,
    )


async def _seed_batch_finalization_sources(
    pool: asyncpg.Pool,
    candidate: BatchFinalizationBasis,
) -> None:
    run_source = run_command(
        candidate_basis=candidate.run_bases[0],
        resolution_set=candidate.run_resolution_sets[0],
        expected_run_version=candidate.run_bases[0].source_run_version,
        expected_attempt_version=candidate.run_bases[0].final_attempt_version,
    )
    await _seed_finalizable_run(pool, run_source)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_runs SET version = $1 WHERE id = $2",
            candidate.run_bases[0].source_run_version,
            candidate.run_bases[0].run_id,
        )
    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        await FinalizeRun(gateway=gateway).execute(run_source)

    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    empty = json.dumps({})
    policy = candidate.policy
    scope_items = candidate.cancellation_scope_items
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "INSERT INTO qep_suites (id, project_id, name, created_at) VALUES ($1, $2, $3, $4)",
            policy.suite_id,
            "project-1",
            "Finalization Suite",
            now,
        )
        await connection.execute(
            "UPDATE qep_suite_revisions SET suite_id = $1 WHERE id = 'suite-revision-1'",
            policy.suite_id,
        )
        await connection.execute(
            """
            UPDATE qep_batches
            SET state = 'finalizing', version = $1, updated_at = $2
            WHERE id = $3
            """,
            candidate.source_batch_version,
            now,
            candidate.batch_id,
        )
        await connection.execute(
            "UPDATE qep_case_manifests SET item_count = $1 WHERE id = $2",
            candidate.item_count,
            candidate.manifest_id,
        )
        for key in candidate.resolution_set.expected_item_keys:
            if key.item_index == 0:
                continue
            await connection.execute(
                """
                INSERT INTO qep_manifest_items (
                    manifest_id, item_index, stable_case_id, framework_locator,
                    atomic_group_id, estimated_duration_ms, resource_profile_id,
                    constraints, tags
                ) VALUES ($1, $2, $3, $4, $3, 1, 'profile-1', $4, $4)
                """,
                key.manifest_id,
                key.item_index,
                f"case-{key.item_index + 1}",
                empty,
            )
        await connection.execute(
            "UPDATE qep_shard_plans SET version = $1 WHERE id = $2",
            candidate.shard_plan_version,
            candidate.shard_plan_id,
        )
        await connection.execute(
            """
            INSERT INTO qep_batch_success_policies (
                id, policy_version, suite_id, max_test_failed_items,
                allow_authorized_retry_pass, policy_digest, payload, approved_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            policy.policy_id,
            policy.policy_version,
            policy.suite_id,
            policy.max_test_failed_items,
            policy.allow_authorized_retry_pass,
            _digest_hex(policy.policy_digest),
            json.dumps(_policy_payload(candidate), sort_keys=True),
            now,
        )
        if candidate.batch_cancellation_intent_digest is not None:
            await connection.execute(
                """
                INSERT INTO qep_batch_cancellation_intents (
                    id, batch_id, project_id, suite_revision_id, source_batch_version,
                    idempotency_key, source, actor_id, reason, request_digest,
                    authorization_digest, scope_kind, manifest_digest, shard_plan_version,
                    shard_plan_digest, canonical_run_set_digest, intent_digest,
                    payload, recorded_at
                ) VALUES (
                    'cancel-intent-1', $1, 'project-1', 'suite-revision-1', $2,
                    'cancel-key-1', 'user_request', 'user-1', 'stop remaining work', $3,
                    $4, 'frozen_plan', $5, $6, $7, $8, $9, $10, $11
                )
                """,
                candidate.batch_id,
                candidate.source_batch_version - 1,
                "a" * 64,
                "b" * 64,
                _digest_hex(candidate.manifest_digest),
                candidate.shard_plan_version,
                _digest_hex(candidate.shard_plan_digest),
                _digest_hex(candidate.canonical_run_set_digest),
                _digest_hex(candidate.batch_cancellation_intent_digest),
                json.dumps(
                    {"intent_digest": candidate.batch_cancellation_intent_digest.value},
                    sort_keys=True,
                ),
                now,
            )
        for item in scope_items:
            await connection.execute(
                """
                INSERT INTO qep_batch_cancellation_scope_items (
                    batch_id, cancellation_intent_digest, manifest_id, item_index,
                    resolution_kind, run_id, source_run_version, scope_item_digest,
                    payload, recorded_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                item.batch_id,
                _digest_hex(item.batch_cancellation_intent_digest),
                item.manifest_id,
                item.manifest_item_key.item_index,
                item.resolution_kind.value,
                item.run_id,
                item.source_run_version,
                _digest_hex(item.scope_item_digest),
                json.dumps(item.canonical_payload(), sort_keys=True),
                item.recorded_at,
            )
        await _seed_readiness_binding(connection, candidate, now)


async def _seed_readiness_binding(
    connection: asyncpg.Connection,
    candidate: BatchFinalizationBasis,
    now: datetime,
) -> None:
    source_batch_version = candidate.source_batch_version - 1
    payload = {
        "batch_id": candidate.batch_id,
        "source_batch_version": source_batch_version,
        "manifest_id": candidate.manifest_id,
        "manifest_digest": candidate.manifest_digest.value,
        "item_count": candidate.item_count,
        "shard_plan_id": candidate.shard_plan_id,
        "shard_plan_version": candidate.shard_plan_version,
        "shard_plan_digest": candidate.shard_plan_digest.value,
        "canonical_run_ids": sorted(value.run_id for value in candidate.run_bases),
        "canonical_run_set_digest": candidate.canonical_run_set_digest.value,
        "success_policy_digest": candidate.policy.policy_digest.value,
        "batch_cancellation_intent_digest": _optional_digest_value(
            candidate.batch_cancellation_intent_digest
        ),
        "run_basis_digests": [value.basis_digest.value for value in candidate.run_bases],
        "pending_retry_intents": [],
        "attempt_creation_opportunities": [],
    }
    readiness_digest = canonical_digest(
        schema_version="qep.batch-finalization-readiness.v1",
        payload=payload,
    )
    ref = f"batch-finalization-readiness-{_digest_hex(readiness_digest)}"
    authority_digest = canonical_digest(
        schema_version="qep.batch-finalization-readiness-authority.v1",
        payload={
            "batch_id": candidate.batch_id,
            "source_batch_version": source_batch_version,
            "write_epoch": 1,
        },
    )
    await connection.execute(
        """
        INSERT INTO qep_batch_finalization_readiness_facts (
            ref, batch_id, source_batch_version, batch_version,
            manifest_digest, shard_plan_digest, canonical_run_set_digest,
            success_policy_digest, batch_cancellation_intent_digest,
            readiness_digest, authority_digest, write_epoch,
            compatibility_epoch, state_model_version, payload, recorded_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 1,
            'M0-STATE-V1', 1, $12, $13
        )
        """,
        ref,
        candidate.batch_id,
        source_batch_version,
        candidate.source_batch_version,
        _digest_hex(candidate.manifest_digest),
        _digest_hex(candidate.shard_plan_digest),
        _digest_hex(candidate.canonical_run_set_digest),
        _digest_hex(candidate.policy.policy_digest),
        _optional_digest_hex(candidate.batch_cancellation_intent_digest),
        _digest_hex(readiness_digest),
        _digest_hex(authority_digest),
        json.dumps(payload, sort_keys=True),
        now,
    )
    await connection.execute(
        "UPDATE qep_batches SET finalization_readiness_ref = $1 WHERE id = $2",
        ref,
        candidate.batch_id,
    )


def _policy_payload(candidate: BatchFinalizationBasis) -> dict[str, object]:
    policy = candidate.policy
    return {
        "schema_version": policy.schema_version,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "suite_id": policy.suite_id,
        "max_test_failed_items": policy.max_test_failed_items,
        "allow_authorized_retry_pass": policy.allow_authorized_retry_pass,
        "allowed_test_failure_selector_digest": None,
        "result_mapping_schema": policy.result_mapping_schema,
        "result_mapping_version": policy.result_mapping_version,
        "result_mapping_digest": policy.result_mapping_digest.value,
        "approval_record_digest": policy.approval_record_digest.value,
    }


def _finalized_event_payload(
    candidate: BatchFinalizationBasis,
    authority: FinalizeBatchAuthority,
) -> dict[str, object]:
    return {
        "schema_version": "qep.batch-finalized.v1",
        "batch_id": candidate.batch_id,
        "source_batch_version": candidate.source_batch_version,
        "batch_version": candidate.source_batch_version + 1,
        "basis_digest": candidate.basis_digest.value,
        "outcome": candidate.batch_outcome.value,
        "authority_digest": authority.authority_digest.value,
        "write_epoch": authority.write_epoch,
    }


def _publication(
    candidate: BatchFinalizationBasis,
    authority: FinalizeBatchAuthority,
    snapshot: BatchFinalizationMutationSnapshot,
) -> BatchFinalizationPublication:
    projection = BatchFinalizationProjection(
        batch_id=candidate.batch_id,
        source_batch_version=candidate.source_batch_version,
        batch_version=snapshot.batch_version + 1,
        outcome=candidate.batch_outcome,
        basis_digest=candidate.basis_digest,
    )
    return BatchFinalizationPublication(
        authority=authority,
        expected_snapshot=snapshot,
        basis=candidate,
        projection=projection,
        side_effect=BatchFinalizationSideEffect(
            batch_id=candidate.batch_id,
            basis_digest=candidate.basis_digest,
            outcome=candidate.batch_outcome,
        ),
    )


async def _insert_scope_item(
    connection: asyncpg.Connection,
    item,
) -> None:
    await connection.execute(
        """
        INSERT INTO qep_batch_cancellation_scope_items (
            batch_id, cancellation_intent_digest, manifest_id, item_index,
            resolution_kind, run_id, source_run_version, scope_item_digest,
            payload, recorded_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        item.batch_id,
        _digest_hex(item.batch_cancellation_intent_digest),
        item.manifest_id,
        item.manifest_item_key.item_index,
        item.resolution_kind.value,
        item.run_id,
        item.source_run_version,
        _digest_hex(item.scope_item_digest),
        json.dumps(item.canonical_payload(), sort_keys=True),
        item.recorded_at,
    )


async def _corrupt_source(connection: asyncpg.Connection, corruption: str) -> None:
    if corruption == "missing_policy":
        await connection.execute("DELETE FROM qep_batch_success_policies")
    elif corruption == "manifest_coverage":
        await connection.execute("UPDATE qep_case_manifests SET item_count = 3")
    elif corruption == "duplicate_intent":
        await connection.execute(
            """
            INSERT INTO qep_batch_cancellation_intents (
                id, batch_id, project_id, suite_revision_id, source_batch_version,
                idempotency_key, source, actor_id, reason, request_digest,
                authorization_digest, scope_kind, preplan_scope_digest, manifest_digest,
                shard_plan_version, shard_plan_digest, canonical_run_set_digest,
                intent_digest, payload, recorded_at
            )
            SELECT
                'cancel-intent-2', batch_id, project_id, suite_revision_id,
                source_batch_version, 'cancel-key-2', source, actor_id, reason,
                request_digest, authorization_digest, scope_kind, preplan_scope_digest,
                manifest_digest, shard_plan_version, shard_plan_digest,
                canonical_run_set_digest, $1, payload, recorded_at
            FROM qep_batch_cancellation_intents
            """,
            "f" * 64,
        )
    elif corruption == "intent_envelope":
        await connection.execute(
            "UPDATE qep_batch_cancellation_intents SET canonical_run_set_digest = $1",
            "f" * 64,
        )
    elif corruption == "scope_non_object":
        await connection.execute(
            "UPDATE qep_batch_cancellation_scope_items SET payload = '[]'::jsonb"
        )
    elif corruption == "scope_payload":
        await connection.execute(
            "UPDATE qep_batch_cancellation_scope_items SET payload = '{}'::jsonb"
        )
    elif corruption == "run_basis_non_object":
        await connection.execute("UPDATE qep_run_finalization_bases SET payload = '[]'::jsonb")
    elif corruption == "run_basis_payload":
        await connection.execute("UPDATE qep_run_finalization_bases SET payload = '{}'::jsonb")
    elif corruption == "run_projection":
        await connection.execute(
            "UPDATE qep_runs SET finalization_basis_digest = $1",
            "f" * 64,
        )
    elif corruption == "run_resolution_non_object":
        await connection.execute("UPDATE qep_run_item_resolutions SET payload = '[]'::jsonb")
    elif corruption == "run_resolution_payload":
        await connection.execute("UPDATE qep_run_item_resolutions SET payload = '{}'::jsonb")
    elif corruption == "policy_non_object":
        await connection.execute("UPDATE qep_batch_success_policies SET payload = '[]'::jsonb")
    elif corruption == "policy_digest":
        await connection.execute(
            "UPDATE qep_batch_success_policies SET policy_digest = $1",
            "f" * 64,
        )
    elif corruption == "policy_digest_type":
        payload = json.loads(
            await connection.fetchval("SELECT payload FROM qep_batch_success_policies")
        )
        payload["result_mapping_digest"] = 7
        await connection.execute(
            "UPDATE qep_batch_success_policies SET payload = $1",
            json.dumps(payload, sort_keys=True),
        )
    else:
        raise AssertionError(f"unknown corruption: {corruption}")


async def _publication_snapshot(connection: asyncpg.Connection) -> dict[str, object]:
    row = await connection.fetchrow(
        """
        SELECT
            batch.state AS batch_state,
            batch.version AS batch_version,
            (SELECT count(*) FROM qep_batch_finalization_bases) AS basis_count,
            (SELECT count(*) FROM qep_batch_item_resolutions) AS resolution_count,
            (SELECT count(*) FROM qep_audit_events WHERE action = 'finalize_batch') AS audit_count,
            (SELECT count(*) FROM qep_outbox_events
             WHERE event_type = 'batch.finalized.v1') AS outbox_count
        FROM qep_batches AS batch
        WHERE batch.id = 'batch-1'
        """
    )
    assert row is not None
    return dict(row)


def _digest_hex(value) -> str:
    assert value is not None
    return value.value.removeprefix("sha256:")


def _optional_digest_hex(value) -> str | None:
    return None if value is None else _digest_hex(value)


def _optional_digest_value(value) -> str | None:
    return None if value is None else value.value
