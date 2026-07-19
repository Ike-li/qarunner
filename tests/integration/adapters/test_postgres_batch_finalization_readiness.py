"""Real PostgreSQL transaction coverage for entering Batch finalization."""

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
from tests.unit.application.test_begin_batch_finalization import _values
from tests.unit.application.test_finalize_run import command as run_command

from qarunner.adapters.postgres_run_finalization import PostgresRunFinalizationUnitOfWork
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.application.begin_batch_finalization import (
    BeginBatchFinalization,
    BeginBatchFinalizationCommand,
)
from qarunner.application.ports.batch_finalization_readiness import (
    AttemptCreationOpportunityKind,
    BatchFinalizationReadinessMutationSnapshot,
    BatchFinalizationReadinessProjection,
    BatchFinalizationReadinessPublication,
    BatchFinalizationReadinessReason,
    BatchFinalizationReadinessSideEffect,
)
from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.common import PortContractError
from qarunner.application.run_closed_handoff import build_run_closed_handoff
from qarunner.application.run_finalization import FinalizeRun
from qarunner.domain import BatchState, canonical_digest


@pytest.fixture
async def batch_readiness_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-batch-readiness-admin-password-at-least-32-chars",
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
async def test_ready_snapshot_atomically_publishes_fact_ref_audit_outbox_and_replays(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        first = await BeginBatchFinalization(gateway=gateway).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
        )

    assert not first.replayed
    assert first.projection is not None
    assert first.projection.state is BatchState.FINALIZING
    assert first.projection.batch_version == snapshot.source_batch_version + 1
    assert first.projection.readiness_digest == snapshot.readiness_digest

    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                batch.state,
                batch.version,
                batch.finalization_readiness_ref,
                fact.ref,
                fact.readiness_digest,
                fact.authority_digest,
                fact.write_epoch,
                fact.payload,
                (SELECT count(*) FROM qep_audit_events
                 WHERE action = 'begin_batch_finalization') AS audit_count,
                (SELECT count(*) FROM qep_outbox_events
                 WHERE event_type = 'batch.finalization.started.v1') AS outbox_count
            FROM qep_batches AS batch
            JOIN qep_batch_finalization_readiness_facts AS fact
              ON fact.ref = batch.finalization_readiness_ref
            WHERE batch.id = $1
            """,
            snapshot.batch_id,
        )

    assert persisted is not None
    assert dict(persisted) == {
        "state": "finalizing",
        "version": snapshot.source_batch_version + 1,
        "finalization_readiness_ref": persisted["ref"],
        "ref": persisted["ref"],
        "readiness_digest": snapshot.readiness_digest.value.removeprefix("sha256:"),
        "authority_digest": authority.authority_digest.value.removeprefix("sha256:"),
        "write_epoch": authority.write_epoch,
        "payload": persisted["payload"],
        "audit_count": 1,
        "outbox_count": 1,
    }
    assert json.loads(persisted["payload"]) == _readiness_payload(snapshot)

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        replay = await BeginBatchFinalization(gateway=gateway).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, expected_batch_version=99)
        )

    assert replay.replayed
    assert replay.projection == first.projection


@pytest.mark.asyncio
async def test_pending_retry_intent_keeps_batch_running_without_any_publication(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values(blocked="retry")
    pending = replace(
        snapshot.pending_retry_intents[0],
        source_attempt_id="attempt-1",
    )
    snapshot = replace(snapshot, pending_retry_intents=(pending,))
    authority = replace(authority, snapshot=snapshot)
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        result = await BeginBatchFinalization(gateway=gateway).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
        )

    assert result.projection is None
    assert result.reason is BatchFinalizationReadinessReason.PENDING_RETRY
    async with pool.acquire() as connection:
        state = await connection.fetchrow(
            """
            SELECT state, version, finalization_readiness_ref,
                   (SELECT count(*) FROM qep_batch_finalization_readiness_facts) AS fact_count,
                   (SELECT count(*) FROM qep_audit_events
                    WHERE action = 'begin_batch_finalization') AS audit_count,
                   (SELECT count(*) FROM qep_outbox_events
                    WHERE aggregate_type = 'batch') AS outbox_count
            FROM qep_batches WHERE id = $1
            """,
            snapshot.batch_id,
        )
    assert dict(state) == {
        "state": "running",
        "version": snapshot.source_batch_version,
        "finalization_readiness_ref": None,
        "fact_count": 0,
        "audit_count": 0,
        "outbox_count": 0,
    }


@pytest.mark.asyncio
async def test_attempt_creation_opportunity_keeps_batch_running_without_publication(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values(blocked="attempt")
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        result = await BeginBatchFinalization(gateway=gateway).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
        )

    assert result.projection is None
    assert result.reason is BatchFinalizationReadinessReason.PENDING_ATTEMPT
    assert (
        snapshot.attempt_creation_opportunities[0].kind
        is AttemptCreationOpportunityKind.RETRY_COMMIT
    )
    async with pool.acquire() as connection:
        state = await connection.fetchrow(
            """
            SELECT state, version, finalization_readiness_ref,
                   (SELECT count(*) FROM qep_batch_finalization_readiness_facts) AS fact_count,
                   (SELECT count(*) FROM qep_audit_events
                    WHERE action = 'begin_batch_finalization') AS audit_count
            FROM qep_batches WHERE id = $1
            """,
            snapshot.batch_id,
        )
    assert dict(state) == {
        "state": "running",
        "version": snapshot.source_batch_version,
        "finalization_readiness_ref": None,
        "fact_count": 0,
        "audit_count": 0,
    }


@pytest.mark.asyncio
async def test_ready_snapshot_revalidates_optional_frozen_plan_cancellation_intent(
    batch_readiness_store: PostgresStore,
) -> None:
    from tests.unit.application.test_begin_batch_finalization import _digest

    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values()
    snapshot = replace(snapshot, batch_cancellation_intent_digest=_digest("cancel"))
    authority = replace(authority, snapshot=snapshot)
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        result = await BeginBatchFinalization(gateway=gateway).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
        )

    assert result.projection is not None
    assert result.reason is BatchFinalizationReadinessReason.READY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("missing_manifest", "batch_readiness_source_missing"),
        ("manifest_digest", "batch_readiness_source_snapshot_mismatch"),
        ("manifest_coverage", "batch_readiness_manifest_coverage_invalid"),
        ("manifest_item", "batch_readiness_source_snapshot_mismatch"),
        ("missing_plan", "batch_readiness_source_missing"),
        ("plan_version", "batch_readiness_source_snapshot_mismatch"),
        ("missing_policy", "batch_readiness_source_missing"),
        ("policy_payload", "batch_readiness_source_snapshot_mismatch"),
        ("cancellation_intent", "batch_readiness_cancellation_source_mismatch"),
        ("run_set", "batch_readiness_run_set_mismatch"),
        ("run_basis", "batch_readiness_run_closure_mismatch"),
        ("run_resolution_count", "batch_readiness_run_resolution_mismatch"),
        ("run_resolution", "batch_readiness_run_resolution_mismatch"),
        ("run_handoff", "batch_readiness_run_handoff_mismatch"),
        ("retry_count", "batch_readiness_retry_opportunity_mismatch"),
        ("retry_binding", "batch_readiness_retry_opportunity_mismatch"),
        ("opportunity_count", "batch_readiness_attempt_opportunity_mismatch"),
        ("opportunity_binding", "batch_readiness_attempt_opportunity_mismatch"),
        ("malformed_json", "batch_readiness_source_integrity_invalid"),
    ],
)
async def test_authority_rebuild_rejects_corrupt_source_facts(
    batch_readiness_store: PostgresStore,
    corruption: str,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    blocked = (
        "retry"
        if corruption in {"retry_binding"}
        else "attempt"
        if corruption in {"opportunity_binding"}
        else None
    )
    snapshot, authority, _ = _values(blocked=blocked)
    if blocked == "retry":
        pending = replace(
            snapshot.pending_retry_intents[0],
            source_attempt_id="attempt-1",
        )
        snapshot = replace(snapshot, pending_retry_intents=(pending,))
        authority = replace(authority, snapshot=snapshot)
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    async with pool.acquire() as connection:
        await _corrupt_readiness_source(connection, corruption)

    with pytest.raises(AuthorityStateConflict) as corrupt:
        async with PostgresBatchFinalizationReadinessUnitOfWork(
            pool, authority=authority
        ) as gateway:
            await gateway.require_readiness_authority(batch_id=snapshot.batch_id)

    assert corrupt.value.reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("payload", "batch_readiness_stored_integrity_invalid"),
        ("digest", "batch_readiness_stored_binding_invalid"),
        ("batch_ref", "batch_readiness_source_state_invalid"),
        ("authority_epoch", "batch_readiness_stored_binding_invalid"),
    ],
)
async def test_exact_replay_rejects_corrupt_stored_fact_or_binding(
    batch_readiness_store: PostgresStore,
    corruption: str,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        await BeginBatchFinalization(gateway=gateway).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
        )
    async with pool.acquire() as connection:
        if corruption == "payload":
            await connection.execute(
                "UPDATE qep_batch_finalization_readiness_facts SET payload = '{}'::jsonb"
            )
        elif corruption == "digest":
            await connection.execute(
                "UPDATE qep_batch_finalization_readiness_facts SET readiness_digest = $1",
                "f" * 64,
            )
        elif corruption == "batch_ref":
            await connection.execute("UPDATE qep_batches SET finalization_readiness_ref = NULL")
        else:
            await connection.execute(
                "UPDATE qep_batch_finalization_readiness_facts SET write_epoch = write_epoch + 1"
            )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        async with PostgresBatchFinalizationReadinessUnitOfWork(
            pool, authority=authority
        ) as gateway:
            await BeginBatchFinalization(gateway=gateway).execute(
                BeginBatchFinalizationCommand(snapshot.batch_id, expected_batch_version=99)
            )

    assert corrupt.value.reason == reason


@pytest.mark.asyncio
async def test_authority_epoch_drift_and_missing_batch_fail_before_replay(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_batches SET write_epoch = write_epoch + 1 WHERE id = $1",
            snapshot.batch_id,
        )
    with pytest.raises(AuthorityStateConflict) as superseded:
        async with PostgresBatchFinalizationReadinessUnitOfWork(
            pool, authority=authority
        ) as gateway:
            await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
    assert superseded.value.reason == "batch_readiness_authority_superseded"

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_projects (id, name, created_at)
            VALUES ($1, $2, transaction_timestamp())
            """,
            "project-other",
            "Other Project",
        )
        await connection.execute("UPDATE qep_suites SET project_id = 'project-other'")
    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        with pytest.raises(Exception) as missing:
            await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
    assert type(missing.value).__name__ == "AuthorityPermissionDenied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("qep_batch_finalization_readiness_facts", "batch_readiness_fact_write_missing"),
        ("qep_audit_events", "batch_readiness_audit_write_missing"),
        ("qep_outbox_events", "batch_readiness_outbox_write_missing"),
    ],
)
async def test_suppressed_mandatory_insert_sticky_aborts_and_rolls_back_everything(
    batch_readiness_store: PostgresStore,
    target: str,
    reason: str,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_readiness_insert() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            f"""
            CREATE TRIGGER suppress_readiness_insert
            BEFORE INSERT ON {target}
            FOR EACH ROW EXECUTE FUNCTION suppress_readiness_insert()
            """
        )

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        with pytest.raises(AuthorityStateConflict) as missing:
            await BeginBatchFinalization(gateway=gateway).execute(
                BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
            )
        assert missing.value.reason == reason
        with pytest.raises(PortContractError, match="aborted"):
            await gateway.require_readiness_authority(batch_id=snapshot.batch_id)

    async with pool.acquire() as connection:
        state = await connection.fetchrow(
            """
            SELECT state, version, finalization_readiness_ref,
                   (SELECT count(*) FROM qep_batch_finalization_readiness_facts) AS fact_count,
                   (SELECT count(*) FROM qep_audit_events
                    WHERE action = 'begin_batch_finalization') AS audit_count,
                   (SELECT count(*) FROM qep_outbox_events
                    WHERE aggregate_type = 'batch') AS outbox_count
            FROM qep_batches WHERE id = $1
            """,
            snapshot.batch_id,
        )
    assert dict(state) == {
        "state": "running",
        "version": snapshot.source_batch_version,
        "finalization_readiness_ref": None,
        "fact_count": 0,
        "audit_count": 0,
        "outbox_count": 0,
    }


@pytest.mark.asyncio
async def test_suppressed_batch_cas_rolls_back_the_staged_fact(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )
    from qarunner.domain import VersionConflict

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_readiness_batch_update() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_readiness_batch_update
            BEFORE UPDATE ON qep_batches
            FOR EACH ROW EXECUTE FUNCTION suppress_readiness_batch_update()
            """
        )

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        with pytest.raises(VersionConflict):
            await BeginBatchFinalization(gateway=gateway).execute(
                BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
            )

    async with pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM qep_batch_finalization_readiness_facts"
            )
            == 0
        )


@pytest.mark.asyncio
async def test_gateway_contract_rejects_scope_lock_and_publication_authority_misuse(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        with pytest.raises(Exception) as denied:
            await gateway.require_readiness_authority(batch_id="batch-other")
    assert type(denied.value).__name__ == "AuthorityPermissionDenied"

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        current = await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
        assert await gateway.require_readiness_authority(batch_id=snapshot.batch_id) == current
        with pytest.raises(PortContractError, match="aggregate_already_locked"):
            await gateway.require_readiness_authority(batch_id="batch-other")
        with pytest.raises(PortContractError, match="unsupported"):
            await gateway.lookup_stored(
                identity_scope=("unsupported", snapshot.batch_id, snapshot.source_batch_version)
            )
        with pytest.raises(PortContractError, match="authority_not_locked"):
            await gateway.lookup_stored(
                identity_scope=(
                    "qep.batch-finalization-readiness.v1",
                    "batch-other",
                    snapshot.source_batch_version,
                )
            )

    mutation = BatchFinalizationReadinessMutationSnapshot(
        snapshot.batch_id,
        snapshot.source_batch_version,
        BatchState.RUNNING,
        snapshot,
    )
    projection = BatchFinalizationReadinessProjection(
        snapshot.batch_id,
        snapshot.source_batch_version,
        snapshot.source_batch_version + 1,
        BatchState.FINALIZING,
        snapshot.readiness_digest,
    )
    publication = BatchFinalizationReadinessPublication(
        authority,
        mutation,
        projection,
        BatchFinalizationReadinessSideEffect(snapshot.batch_id, snapshot.readiness_digest),
    )
    forged = replace(
        authority,
        authority_digest=canonical_digest(
            schema_version="qep.test-forged-readiness-authority.v1",
            payload={"batch_id": snapshot.batch_id},
        ),
    )
    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
        with pytest.raises(AuthorityStateConflict) as superseded:
            await gateway.publish_readiness(publication=replace(publication, authority=forged))
    assert superseded.value.reason == "publication_authority_superseded"


@pytest.mark.asyncio
async def test_gateway_publish_rejects_stale_expected_snapshot(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )
    from qarunner.domain import VersionConflict

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    mutation = BatchFinalizationReadinessMutationSnapshot(
        snapshot.batch_id,
        snapshot.source_batch_version,
        BatchState.RUNNING,
        snapshot,
    )
    projection = BatchFinalizationReadinessProjection(
        snapshot.batch_id,
        snapshot.source_batch_version,
        snapshot.source_batch_version + 1,
        BatchState.FINALIZING,
        snapshot.readiness_digest,
    )
    publication = BatchFinalizationReadinessPublication(
        authority,
        mutation,
        projection,
        BatchFinalizationReadinessSideEffect(snapshot.batch_id, snapshot.readiness_digest),
    )

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
        gateway._locked_batch_version += 1
        with pytest.raises(VersionConflict):
            await gateway.publish_readiness(publication=publication)


@pytest.mark.asyncio
async def test_publish_path_exact_replay_and_digest_conflict_are_authority_first(
    batch_readiness_store: PostgresStore,
) -> None:
    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )
    from qarunner.domain import IdempotencyConflict

    snapshot, authority, _ = _values()
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    publication = _publication(snapshot, authority)

    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
        first = await gateway.publish_readiness(publication=publication)
    assert not first.replayed
    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
        replay = await gateway.publish_readiness(publication=publication)
    assert replay.replayed

    changed_payload = _readiness_payload(snapshot)
    changed_payload["frozen_plan_binding_digest"] = "sha256:" + "e" * 64
    changed_digest = canonical_digest(
        schema_version="qep.batch-finalization-readiness.v1",
        payload=changed_payload,
    )
    async with PostgresBatchFinalizationReadinessUnitOfWork(pool, authority=authority) as gateway:
        await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
        await gateway._require_connection().execute(
            """
            UPDATE qep_batch_finalization_readiness_facts
            SET readiness_digest = $1, payload = $2
            WHERE batch_id = $3
            """,
            changed_digest.value.removeprefix("sha256:"),
            json.dumps(changed_payload, sort_keys=True),
            snapshot.batch_id,
        )
        with pytest.raises(IdempotencyConflict):
            await gateway.publish_readiness(publication=publication)


@pytest.mark.asyncio
async def test_cancellation_source_binding_drift_is_not_treated_as_ready(
    batch_readiness_store: PostgresStore,
) -> None:
    from tests.unit.application.test_begin_batch_finalization import _digest

    from qarunner.adapters.postgres_batch_finalization_readiness import (
        PostgresBatchFinalizationReadinessUnitOfWork,
    )

    snapshot, authority, _ = _values()
    snapshot = replace(snapshot, batch_cancellation_intent_digest=_digest("cancel"))
    authority = replace(authority, snapshot=snapshot)
    pool = batch_readiness_store._require_pool()
    snapshot, authority = await _seed_ready_sources(pool, snapshot, authority)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_batch_cancellation_intents SET manifest_digest = $1",
            "f" * 64,
        )
    with pytest.raises(AuthorityStateConflict) as mismatch:
        async with PostgresBatchFinalizationReadinessUnitOfWork(
            pool, authority=authority
        ) as gateway:
            await gateway.require_readiness_authority(batch_id=snapshot.batch_id)
    assert mismatch.value.reason == "batch_readiness_cancellation_source_mismatch"


async def _seed_ready_sources(pool, snapshot, authority):
    closure = snapshot.run_closures[0]
    run_source = run_command(
        candidate_basis=closure.basis,
        resolution_set=closure.resolution_set,
        expected_run_version=closure.basis.source_run_version,
        expected_attempt_version=closure.basis.final_attempt_version,
    )
    await _seed_finalizable_run(pool, run_source)
    async with PostgresRunFinalizationUnitOfWork(pool) as gateway:
        run_authority = await gateway.require_finalization_authority(run_id=closure.run_id)
        run_result = await FinalizeRun(gateway=gateway).execute(run_source)

    corrected_handoff = build_run_closed_handoff(
        basis=closure.basis,
        resolution_set=closure.resolution_set,
        authority_digest=run_authority.authority_digest,
        write_epoch=run_authority.write_epoch,
    )
    corrected_closure = replace(
        closure,
        handoff=corrected_handoff,
        state=run_result.projection.state,
    )
    snapshot = replace(snapshot, run_closures=(corrected_closure,))
    authority = replace(authority, snapshot=snapshot)

    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    policy = snapshot.success_policy
    manifest = snapshot.frozen_plan.bound_plan.plan.manifest
    plan = snapshot.frozen_plan.bound_plan.plan
    item = manifest.items[0]
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            """
            INSERT INTO qep_resource_profiles (
                id, name, profile_version, framework, requests, limits,
                internal_workers, security_profile_id, approved_at, created_at
            ) VALUES ($1, $2, 1, 'pytest', $3, $3, 1, $4, $5, $5)
            """,
            item.resource_profile_id,
            "Readiness Profile",
            json.dumps({}),
            "security-profile-readiness",
            now,
        )
        await connection.execute(
            "INSERT INTO qep_suites (id, project_id, name, created_at) VALUES ($1, $2, $3, $4)",
            policy.suite_id,
            "project-1",
            "Readiness Suite",
            now,
        )
        await connection.execute(
            "UPDATE qep_suite_revisions SET suite_id = $1 WHERE id = 'suite-revision-1'",
            policy.suite_id,
        )
        await connection.execute("DELETE FROM qep_suites WHERE id = 'suite-1'")
        await connection.execute(
            """
            UPDATE qep_batches
            SET state = 'running', version = $1, updated_at = $2
            WHERE id = $3
            """,
            snapshot.source_batch_version,
            now,
            snapshot.batch_id,
        )
        await connection.execute(
            "UPDATE qep_case_manifests SET item_count = $1, payload = $2 WHERE id = $3",
            manifest.item_count,
            json.dumps(_manifest_payload(manifest), sort_keys=True),
            manifest.id,
        )
        await connection.execute(
            """
            UPDATE qep_manifest_items
            SET stable_case_id = $1,
                framework_locator = $2,
                atomic_group_id = $3,
                estimated_duration_ms = $4,
                resource_profile_id = $5,
                constraints = $6,
                tags = $7
            WHERE manifest_id = $8 AND item_index = $9
            """,
            item.stable_case_id,
            json.dumps(
                {
                    "schema_version": item.framework_locator.schema_version,
                    "kind": item.framework_locator.kind,
                    "parts": [list(part) for part in item.framework_locator.parts],
                },
                sort_keys=True,
            ),
            item.atomic_group_id,
            item.estimate.duration_ms,
            item.resource_profile_id,
            json.dumps(
                {
                    "serial_group": item.constraints.serial_group,
                    "environment_requirements": list(item.constraints.environment_requirements),
                    "account_requirements": list(item.constraints.account_requirements),
                    "data_lease_requirements": list(item.constraints.data_lease_requirements),
                },
                sort_keys=True,
            ),
            json.dumps(list(item.tags)),
            manifest.id,
            item.item_index,
        )
        await connection.execute(
            """
            UPDATE qep_shard_plans
            SET algorithm_version = $1,
                digest = $2,
                run_count = $3,
                total_estimated_duration_ms = $4,
                version = $5,
                payload = $6
            WHERE id = $7
            """,
            plan.algorithm_version,
            plan.digest.value.removeprefix("sha256:"),
            plan.run_count,
            plan.total_estimated_duration_ms,
            snapshot.shard_plan_version,
            json.dumps(_plan_payload(plan), sort_keys=True),
            plan.id,
        )
        await connection.execute(
            "UPDATE qep_runs SET resource_profile_id = $1 WHERE id = $2",
            plan.shards[0].resource_profile_id,
            corrected_closure.run_id,
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
            policy.policy_digest.value.removeprefix("sha256:"),
            json.dumps(_policy_payload(policy), sort_keys=True),
            now,
        )
        if snapshot.batch_cancellation_intent_digest is not None:
            await connection.execute(
                """
                INSERT INTO qep_batch_cancellation_intents (
                    id, batch_id, project_id, suite_revision_id, source_batch_version,
                    idempotency_key, source, actor_id, reason, request_digest,
                    authorization_digest, scope_kind, manifest_digest,
                    shard_plan_version, shard_plan_digest, canonical_run_set_digest,
                    intent_digest, payload, recorded_at
                ) VALUES (
                    'readiness-cancel-intent', $1, 'project-1', 'suite-revision-1', $2,
                    'readiness-cancel-key', 'user_request', 'user-1', 'stop', $3, $4,
                    'frozen_plan', $5, $6, $7, $8, $9, '{}'::jsonb, $10
                )
                """,
                snapshot.batch_id,
                snapshot.source_batch_version - 1,
                "a" * 64,
                "b" * 64,
                snapshot.manifest_digest.value.removeprefix("sha256:"),
                snapshot.shard_plan_version,
                snapshot.shard_plan_digest.value.removeprefix("sha256:"),
                snapshot.canonical_run_set_digest.value.removeprefix("sha256:"),
                snapshot.batch_cancellation_intent_digest.value.removeprefix("sha256:"),
                now,
            )
        for intent in snapshot.pending_retry_intents:
            authority_payload = intent.authority.canonical_payload()
            await connection.execute(
                """
                INSERT INTO qep_retry_intents (
                    id, run_id, source_attempt_id, source_attempt_no, source_fence,
                    source_run_version, decision_source_kind, source_item_set_digest,
                    target_item_set_digest, authority_digest, retry_intent_digest,
                    status, payload, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 'unknown_adjudication', $7, $7,
                    $8, $9, 'pending', $10, $11
                )
                """,
                intent.id,
                intent.run_id,
                intent.source_attempt_id,
                intent.source_attempt_no,
                intent.source_fence,
                snapshot.run_closures[0].basis.source_run_version,
                snapshot.run_closures[0].basis.run_item_set_digest.value.removeprefix("sha256:"),
                authority_payload["adjudication_digest"].removeprefix("sha256:"),
                intent.digest.value.removeprefix("sha256:"),
                json.dumps(_retry_intent_payload(intent), sort_keys=True),
                intent.created_at,
            )
        for opportunity in snapshot.attempt_creation_opportunities:
            await connection.execute(
                """
                UPDATE qep_assignments
                SET state = 'offered', attempt_id = NULL, fence = NULL,
                    payload = $1
                WHERE run_id = $2
                """,
                json.dumps(
                    {
                        "opportunity_kind": opportunity.kind.value,
                        "authority_digest": opportunity.authority_digest.value,
                    },
                    sort_keys=True,
                ),
                opportunity.run_id,
            )
    return snapshot, authority


def _manifest_payload(manifest):
    return {
        "suite_revision_digest": manifest.inputs.suite_revision_digest.value,
        "input_digests": {
            "source": manifest.inputs.source_digest.value,
            "dependency": manifest.inputs.dependency_digest.value,
            "config": manifest.inputs.config_digest.value,
            "runner": manifest.inputs.runner_digest.value,
        },
        "collection_contract_version": manifest.inputs.collection_contract_version,
        "items": [
            {
                "item_index": item.item_index,
                "stable_case_id": item.stable_case_id,
                "framework_locator": {
                    "schema_version": item.framework_locator.schema_version,
                    "kind": item.framework_locator.kind,
                    "parts": [list(part) for part in item.framework_locator.parts],
                },
                "atomic_group_id": item.atomic_group_id,
                "resource_profile_id": item.resource_profile_id,
                "constraints": {
                    "serial_group": item.constraints.serial_group,
                    "environment_requirements": list(item.constraints.environment_requirements),
                    "account_requirements": list(item.constraints.account_requirements),
                    "data_lease_requirements": list(item.constraints.data_lease_requirements),
                },
                "estimate": {
                    "duration_ms": item.estimate.duration_ms,
                    "confidence": item.estimate.confidence.value,
                },
                "tags": list(item.tags),
                "selection_metadata_digest": item.selection_metadata_digest.value,
            }
            for item in manifest.items
        ],
    }


def _plan_payload(plan):
    return {
        "manifest_digest": plan.manifest.digest.value,
        "algorithm_version": plan.algorithm_version,
        "run_count": plan.run_count,
        "total_estimated_duration_ms": plan.total_estimated_duration_ms,
        "shards": [
            {
                "shard_index": shard.shard_index,
                "manifest_item_indices": list(shard.manifest_item_indices),
                "resource_profile_id": shard.resource_profile_id,
                "estimated_duration_ms": shard.estimated_duration_ms,
                "requirements": {
                    "serial_groups": list(shard.requirements.serial_groups),
                    "environment_requirements": list(shard.requirements.environment_requirements),
                    "account_requirements": list(shard.requirements.account_requirements),
                    "data_lease_requirements": list(shard.requirements.data_lease_requirements),
                },
                "flags": list(shard.flags),
            }
            for shard in plan.shards
        ],
    }


def _policy_payload(policy):
    optional = policy.allowed_test_failure_selector_digest
    return {
        "schema_version": policy.schema_version,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "suite_id": policy.suite_id,
        "max_test_failed_items": policy.max_test_failed_items,
        "allow_authorized_retry_pass": policy.allow_authorized_retry_pass,
        "allowed_test_failure_selector_digest": None if optional is None else optional.value,
        "result_mapping_schema": policy.result_mapping_schema,
        "result_mapping_version": policy.result_mapping_version,
        "result_mapping_digest": policy.result_mapping_digest.value,
        "approval_record_digest": policy.approval_record_digest.value,
    }


def _readiness_payload(snapshot):
    return {
        "batch_id": snapshot.batch_id,
        "source_batch_version": snapshot.source_batch_version,
        "manifest_id": snapshot.manifest_id,
        "manifest_digest": snapshot.manifest_digest.value,
        "item_count": snapshot.item_count,
        "shard_plan_id": snapshot.shard_plan_id,
        "shard_plan_version": snapshot.shard_plan_version,
        "shard_plan_digest": snapshot.shard_plan_digest.value,
        "frozen_plan_binding_digest": snapshot.frozen_plan.binding_digest.value,
        "canonical_run_ids": list(snapshot.canonical_run_ids),
        "canonical_run_set_digest": snapshot.canonical_run_set_digest.value,
        "success_policy_digest": snapshot.success_policy.policy_digest.value,
        "batch_cancellation_intent_digest": (
            None
            if snapshot.batch_cancellation_intent_digest is None
            else snapshot.batch_cancellation_intent_digest.value
        ),
        "run_basis_digests": [value.basis.basis_digest.value for value in snapshot.run_closures],
        "pending_retry_intents": [],
        "attempt_creation_opportunities": [],
    }


def _retry_intent_payload(intent):
    return {
        "id": intent.id,
        "run_id": intent.run_id,
        "source_attempt_id": intent.source_attempt_id,
        "source_attempt_no": intent.source_attempt_no,
        "source_fence": intent.source_fence,
        "authority": intent.authority.canonical_payload(),
        "execution_spec_digest": intent.execution_spec_digest.value,
        "created_at": intent.created_at.isoformat().replace("+00:00", "Z"),
    }


def _publication(snapshot, authority):
    mutation = BatchFinalizationReadinessMutationSnapshot(
        snapshot.batch_id,
        snapshot.source_batch_version,
        BatchState.RUNNING,
        snapshot,
    )
    projection = BatchFinalizationReadinessProjection(
        snapshot.batch_id,
        snapshot.source_batch_version,
        snapshot.source_batch_version + 1,
        BatchState.FINALIZING,
        snapshot.readiness_digest,
    )
    return BatchFinalizationReadinessPublication(
        authority,
        mutation,
        projection,
        BatchFinalizationReadinessSideEffect(snapshot.batch_id, snapshot.readiness_digest),
    )


async def _corrupt_readiness_source(connection: asyncpg.Connection, corruption: str) -> None:
    if corruption == "missing_manifest":
        await _insert_other_batch(connection)
        await connection.execute("UPDATE qep_case_manifests SET batch_id = 'batch-other'")
    elif corruption == "manifest_digest":
        await connection.execute(
            "UPDATE qep_case_manifests SET digest = $1",
            "f" * 64,
        )
    elif corruption == "manifest_coverage":
        await connection.execute(
            """
            INSERT INTO qep_manifest_items (
                manifest_id, item_index, stable_case_id, framework_locator,
                atomic_group_id, estimated_duration_ms, resource_profile_id,
                constraints, tags
            )
            SELECT manifest_id, 1, 'case-extra', framework_locator,
                   'case-extra', 1, resource_profile_id, constraints, tags
            FROM qep_manifest_items WHERE item_index = 0
            """
        )
    elif corruption == "manifest_item":
        await connection.execute("UPDATE qep_manifest_items SET stable_case_id = 'case-drift'")
    elif corruption == "missing_plan":
        await _insert_other_batch(connection)
        await connection.execute("UPDATE qep_shard_plans SET batch_id = 'batch-other'")
    elif corruption == "plan_version":
        await connection.execute("UPDATE qep_shard_plans SET version = version + 1")
    elif corruption == "missing_policy":
        await connection.execute("DELETE FROM qep_batch_success_policies")
    elif corruption == "policy_payload":
        await connection.execute("UPDATE qep_batch_success_policies SET payload = '{}'::jsonb")
    elif corruption == "cancellation_intent":
        await connection.execute(
            """
            INSERT INTO qep_batch_cancellation_intents (
                id, batch_id, project_id, suite_revision_id, source_batch_version,
                idempotency_key, source, actor_id, reason, request_digest,
                authorization_digest, scope_kind, manifest_digest,
                shard_plan_version, shard_plan_digest, canonical_run_set_digest,
                intent_digest, payload, recorded_at
            )
            SELECT 'unexpected-intent', batch.id, batch.project_id,
                   batch.suite_revision_id, batch.version,
                   'unexpected-key', 'user_request', 'user-1', 'stop', $1, $2,
                   'frozen_plan', manifest.digest, plan.version, plan.digest,
                   $3, $4, '{}'::jsonb, transaction_timestamp()
            FROM qep_batches AS batch
            JOIN qep_case_manifests AS manifest ON manifest.batch_id = batch.id
            JOIN qep_shard_plans AS plan ON plan.batch_id = batch.id
            """,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "d" * 64,
        )
    elif corruption == "run_set":
        await connection.execute(
            """
            INSERT INTO qep_runs (
                id, batch_id, plan_id, shard_index, resource_profile_id,
                orchestration_phase, current_fence, attempt_count, version,
                run_item_set_digest, created_at, updated_at, payload
            )
            SELECT 'run-extra', batch_id, plan_id, 1, resource_profile_id,
                   'planned', 0, 0, 0, $1, transaction_timestamp(),
                   transaction_timestamp(), '{}'::jsonb
            FROM qep_runs WHERE id = 'run-1'
            """,
            "e" * 64,
        )
    elif corruption == "run_basis":
        await connection.execute(
            "UPDATE qep_run_finalization_bases SET basis_digest = $1",
            "f" * 64,
        )
    elif corruption == "run_resolution_count":
        await connection.execute("DELETE FROM qep_run_item_resolutions")
    elif corruption == "run_resolution":
        await connection.execute(
            "UPDATE qep_run_item_resolutions SET item_resolution_digest = $1",
            "f" * 64,
        )
    elif corruption == "run_handoff":
        await connection.execute(
            """
            UPDATE qep_outbox_events
            SET payload_digest = $1
            WHERE event_type = 'run.closed.v1'
            """,
            "f" * 64,
        )
    elif corruption == "retry_count":
        await connection.execute(
            """
            INSERT INTO qep_retry_intents (
                id, run_id, source_attempt_id, source_attempt_no, source_fence,
                source_run_version, decision_source_kind, source_item_set_digest,
                target_item_set_digest, authority_digest, retry_intent_digest,
                status, payload, created_at
            ) VALUES (
                'unexpected-retry', 'run-1', 'attempt-1', 1, 1, 0,
                'unknown_adjudication', $1, $1, $2, $3, 'pending', '{}'::jsonb,
                transaction_timestamp()
            )
            """,
            "a" * 64,
            "b" * 64,
            "c" * 64,
        )
    elif corruption == "retry_binding":
        await connection.execute(
            "UPDATE qep_retry_intents SET source_attempt_no = source_attempt_no + 1"
        )
    elif corruption == "opportunity_count":
        await connection.execute(
            "UPDATE qep_assignments SET state = 'offered', attempt_id = NULL, fence = NULL"
        )
    elif corruption == "opportunity_binding":
        await connection.execute("UPDATE qep_assignments SET payload = '{}'::jsonb")
    else:
        await connection.execute("UPDATE qep_case_manifests SET payload = '[]'::jsonb")


async def _insert_other_batch(connection: asyncpg.Connection) -> None:
    await connection.execute(
        """
        INSERT INTO qep_batches (
            id, project_id, suite_revision_id, request_digest,
            idempotency_scope, idempotency_key, state, version, write_epoch,
            created_at, updated_at, payload
        )
        SELECT 'batch-other', project_id, suite_revision_id, request_digest,
               idempotency_scope, 'batch-other', state, version, write_epoch,
               created_at, updated_at, payload
        FROM qep_batches WHERE id = 'batch-1'
        ON CONFLICT (id) DO NOTHING
        """
    )
