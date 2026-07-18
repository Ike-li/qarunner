"""Real PostgreSQL transaction coverage for Batch cancellation intent."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from tests.integration.migration_operator import run_migration_operator

from qarunner.adapters.postgres_batch_cancellation import (
    PostgresBatchCancellationUnitOfWork,
)
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.application.batch_preexecution import (
    ObjectForbidden,
    PreexecutionStateConflict,
    ReconcilePreexecutionCancellation,
    ReconcilePreexecutionCancellationCommand,
    RequestBatchCancellation,
    RequestBatchCancellationCommand,
    TemporarilyUnavailable,
)
from qarunner.application.handoff import BatchMaterializedScopeHandoff, build_cancel_handoff
from qarunner.application.ports.batch_preexecution import (
    AuthorityProjectionStamp,
    AuthorityStateConflict,
    BatchCancellationAuthority,
    BatchClosureAuthority,
    InternalAuthorityRetired,
)
from qarunner.application.ports.common import PortContractError
from qarunner.application.preexecution_proof import ProvePreexecutionClosure
from qarunner.domain import (
    Batch,
    BatchCancellationIntent,
    BatchCancellationScope,
    BatchCancellationScopeKind,
    BatchPreexecutionScopeKind,
    BatchPreexecutionSnapshot,
    BatchRejection,
    BatchRejectionReasonClass,
    BatchRejectionStage,
    BatchState,
    CancellationSource,
    IdempotencyConflict,
    VersionConflict,
    canonical_digest,
    canonical_materialized_run_set_digest,
)


@pytest.fixture
async def batch_cancellation_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-batch-cancellation-admin-password-at-least-32-chars",
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
async def test_authorized_cancellation_atomically_publishes_batch_intent_audit_and_outbox(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)

    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        accepted = await RequestBatchCancellation(gateway=gateway).execute(
            RequestBatchCancellationCommand(
                batch_id="batch-001",
                expected_batch_version=3,
                idempotency_key="cancel-001",
                reason="stop before execution starts",
            )
        )

    assert accepted.batch_id == "batch-001"
    assert accepted.project_id == "project-001"
    assert accepted.suite_revision_id == "suite-revision-001"
    assert accepted.source_batch_version == 3
    assert accepted.actor_id == "user-001"
    assert accepted.authorization_digest == authority.authorization_digest
    assert accepted.scope == authority.scope

    async with pool.acquire() as connection:
        batch = await connection.fetchrow(
            "SELECT state, version FROM qep_batches WHERE id = 'batch-001'"
        )
        intent = await connection.fetchrow(
            """
            SELECT batch_id, project_id, suite_revision_id, source_batch_version,
                   idempotency_key, source, actor_id, reason, request_digest,
                   authorization_digest, scope_kind, preplan_scope_digest,
                   intent_digest, payload
            FROM qep_batch_cancellation_intents
            """
        )
        audit = await connection.fetchrow(
            """
            SELECT actor_id, action, object_type, object_id, decision, reason_code,
                   before_digest, after_digest, payload
            FROM qep_audit_events
            """
        )
        outbox = await connection.fetchrow(
            """
            SELECT aggregate_type, aggregate_id, event_type, payload_digest, payload, status
            FROM qep_outbox_events
            """
        )

    assert dict(batch) == {"state": "collecting", "version": 4}
    assert intent is not None
    assert (
        intent["batch_id"],
        intent["project_id"],
        intent["suite_revision_id"],
        intent["source_batch_version"],
        intent["idempotency_key"],
        intent["source"],
        intent["actor_id"],
        intent["reason"],
        intent["request_digest"],
        intent["authorization_digest"],
        intent["scope_kind"],
        intent["preplan_scope_digest"],
        intent["intent_digest"],
    ) == (
        "batch-001",
        "project-001",
        "suite-revision-001",
        3,
        "cancel-001",
        "user_request",
        "user-001",
        "stop before execution starts",
        _digest_hex(accepted.request_digest),
        _digest_hex(accepted.authorization_digest),
        "pre_plan",
        _digest_hex(accepted.scope.preplan_scope_digest),
        _digest_hex(accepted.digest),
    )
    assert json.loads(intent["payload"])["intent_digest"] == accepted.digest.value
    assert audit is not None
    assert (
        audit["actor_id"],
        audit["action"],
        audit["object_type"],
        audit["object_id"],
        audit["decision"],
        audit["reason_code"],
        audit["before_digest"],
        audit["after_digest"],
    ) == (
        "user-001",
        "request_batch_cancellation",
        "batch",
        "batch-001",
        "allowed",
        "batch_cancellation_intent_committed",
        None,
        _digest_hex(accepted.digest),
    )
    assert json.loads(audit["payload"])["authorization_digest"] == (
        accepted.authorization_digest.value
    )
    assert outbox is not None
    assert (
        outbox["aggregate_type"],
        outbox["aggregate_id"],
        outbox["event_type"],
        outbox["status"],
    ) == ("batch", "batch-001", "batch.cancel-requested.v1", "pending")
    outbox_payload = json.loads(outbox["payload"])
    assert outbox_payload["intent_digest"] == accepted.digest.value
    assert outbox["payload_digest"] == _digest_hex(
        canonical_digest(
            schema_version="qep.batch-cancel-requested.v1",
            payload=outbox_payload,
        )
    )


@pytest.mark.asyncio
async def test_materialized_cancel_reconciliation_atomically_publishes_one_handoff(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    cancel_authority = _authority()
    await _seed_batch(pool)
    async with PostgresBatchCancellationUnitOfWork(
        pool,
        authority=cancel_authority,
    ) as gateway:
        intent = await RequestBatchCancellation(gateway=gateway).execute(
            RequestBatchCancellationCommand(
                batch_id="batch-001",
                expected_batch_version=3,
                idempotency_key="cancel-001",
                reason="stop before execution starts",
            )
        )
    await _seed_materialized_run(pool)
    closure_authority = _closure_authority(intent)
    command = ReconcilePreexecutionCancellationCommand(
        batch_id="batch-001",
        reconciler_id="reconciler-001",
        closure_epoch=1,
    )

    async def reconcile() -> BatchMaterializedScopeHandoff:
        async with PostgresBatchCancellationUnitOfWork(
            pool,
            closure_authority=closure_authority,
            closure_reconciler_id="reconciler-001",
            closure_checked_at=intent.recorded_at + timedelta(minutes=1),
        ) as gateway:
            result = await ReconcilePreexecutionCancellation(
                gateway=gateway,
                proof=ProvePreexecutionClosure(gateway=gateway),
            ).execute(command)
        assert isinstance(result, BatchMaterializedScopeHandoff)
        return result

    first = await reconcile()
    replay = await reconcile()

    assert replay == first
    assert first.authoritative_run_set_digest == canonical_materialized_run_set_digest(
        batch_id="batch-001",
        run_ids=("run-001",),
    )
    assert first.command_or_observation_digest == intent.digest
    async with pool.acquire() as connection:
        batch = await connection.fetchrow(
            "SELECT state, version FROM qep_batches WHERE id = 'batch-001'"
        )
        handoff = await connection.fetchrow(
            """
            SELECT
                id, schema_version, batch_id, trigger_kind, trigger_digest,
                source_batch_version, materialized_run_set_digest, handoff_digest,
                event_id, payload
            FROM qep_materialized_scope_handoffs
            WHERE batch_id = 'batch-001'
            """
        )
        side_effects = await connection.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM qep_batch_preexecution_closure_bases) AS basis_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count,
                (SELECT count(*) FROM qep_outbox_events
                    WHERE event_type = 'batch.materialized-scope-handoff.v1')
                    AS handoff_event_count
            """
        )
    assert dict(batch) == {"state": "collecting", "version": 4}
    assert handoff is not None
    assert (
        handoff["id"],
        handoff["schema_version"],
        handoff["batch_id"],
        handoff["trigger_kind"],
        handoff["trigger_digest"],
        handoff["source_batch_version"],
        handoff["materialized_run_set_digest"],
        handoff["handoff_digest"],
        handoff["event_id"],
    ) == (
        first.handoff_id,
        first.schema_version,
        first.batch_id,
        first.trigger_kind.value,
        _digest_hex(first.command_or_observation_digest),
        first.source_batch_version,
        _digest_hex(first.authoritative_run_set_digest),
        _digest_hex(first.handoff_digest),
        first.event_id,
    )
    assert json.loads(handoff["payload"])["handoff_digest"] == first.handoff_digest.value
    assert dict(side_effects) == {
        "basis_count": 0,
        "audit_count": 2,
        "outbox_count": 2,
        "handoff_event_count": 1,
    }


@pytest.mark.asyncio
async def test_materialized_handoff_replay_fails_closed_when_stored_payload_is_corrupt(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    cancel_authority = _authority()
    await _seed_batch(pool)
    async with PostgresBatchCancellationUnitOfWork(
        pool,
        authority=cancel_authority,
    ) as gateway:
        intent = await RequestBatchCancellation(gateway=gateway).execute(
            RequestBatchCancellationCommand(
                batch_id="batch-001",
                expected_batch_version=3,
                idempotency_key="cancel-001",
                reason="stop before execution starts",
            )
        )
    await _seed_materialized_run(pool)
    closure_authority = _closure_authority(intent)
    command = ReconcilePreexecutionCancellationCommand(
        batch_id="batch-001",
        reconciler_id="reconciler-001",
        closure_epoch=1,
    )

    async def reconcile() -> BatchMaterializedScopeHandoff:
        async with PostgresBatchCancellationUnitOfWork(
            pool,
            closure_authority=closure_authority,
            closure_reconciler_id="reconciler-001",
            closure_checked_at=intent.recorded_at + timedelta(minutes=1),
        ) as gateway:
            result = await ReconcilePreexecutionCancellation(
                gateway=gateway,
                proof=ProvePreexecutionClosure(gateway=gateway),
            ).execute(command)
        assert isinstance(result, BatchMaterializedScopeHandoff)
        return result

    first = await reconcile()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_materialized_scope_handoffs
            SET payload = jsonb_set(payload, '{destination}', '"corrupt"'::jsonb)
            WHERE id = $1
            """,
            first.handoff_id,
        )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        await reconcile()

    assert corrupt.value.reason == "stored_materialized_handoff_integrity_invalid"
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM qep_materialized_scope_handoffs) AS handoff_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(counts) == {"handoff_count": 1, "audit_count": 2, "outbox_count": 2}


@pytest.mark.asyncio
async def test_cached_closure_authority_rejects_a_changed_epoch(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    cancel_authority = _authority()
    await _seed_batch(pool)
    async with PostgresBatchCancellationUnitOfWork(
        pool,
        authority=cancel_authority,
    ) as gateway:
        intent = await RequestBatchCancellation(gateway=gateway).execute(
            RequestBatchCancellationCommand(
                batch_id="batch-001",
                expected_batch_version=3,
                idempotency_key="cancel-001",
                reason="stop before execution starts",
            )
        )
    closure_authority = _closure_authority(intent)

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=closure_authority,
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        accepted = await gateway.require_closure_authority(
            batch_id="batch-001",
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
        replayed = await gateway.require_closure_authority(
            batch_id="batch-001",
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
        with pytest.raises(InternalAuthorityRetired):
            await gateway.require_closure_authority(
                batch_id="batch-001",
                reconciler_id="retired-reconciler",
                closure_epoch=1,
            )
        with pytest.raises(PortContractError) as wrong_batch:
            await gateway.require_closure_authority(
                batch_id="batch-002",
                reconciler_id="reconciler-001",
                closure_epoch=1,
            )
        with pytest.raises(AuthorityStateConflict) as superseded:
            await gateway.require_closure_authority(
                batch_id="batch-001",
                reconciler_id="reconciler-001",
                closure_epoch=2,
            )

    assert accepted == closure_authority
    assert replayed == closure_authority
    assert wrong_batch.value.reason == "aggregate_already_locked"
    assert superseded.value.reason == "closure_epoch_superseded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_type", "expected_reason"),
    [
        ("expired", TemporarilyUnavailable, None),
        ("retired_reconciler", PreexecutionStateConflict, "reconciler_authority_retired"),
        ("command_epoch", PreexecutionStateConflict, "closure_epoch_superseded"),
        ("project", PreexecutionStateConflict, "reconciler_authority_denied"),
        ("write_epoch", PreexecutionStateConflict, "closure_epoch_superseded"),
        ("source_version", PreexecutionStateConflict, "source_binding_superseded"),
        ("scope", PreexecutionStateConflict, "source_binding_superseded"),
    ],
)
async def test_closure_authority_drift_fails_before_proof_or_handoff(
    batch_cancellation_store: PostgresStore,
    case: str,
    expected_type: type[Exception],
    expected_reason: str | None,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent = await _seed_cancellation_intent(pool)
    closure_authority = _closure_authority(intent)
    reconciler_id = "reconciler-001"
    closure_epoch = 1
    checked_at = intent.recorded_at + timedelta(minutes=1)
    if case == "expired":
        checked_at = closure_authority.projection.expires_at
    elif case == "retired_reconciler":
        reconciler_id = "retired-reconciler"
    elif case == "command_epoch":
        closure_epoch = 2
    elif case == "project":
        closure_authority = replace(closure_authority, project_id="project-attacker")
    elif case == "write_epoch":
        closure_authority = replace(closure_authority, write_epoch=2)
        closure_epoch = 2
    elif case == "source_version":
        closure_authority = replace(closure_authority, source_batch_version=5)
    else:
        closure_authority = replace(
            closure_authority,
            scope=replace(
                closure_authority.scope,
                preplan_scope_digest=_named_digest("changed-closure-scope"),
            ),
        )

    with pytest.raises(expected_type) as rejected:
        async with PostgresBatchCancellationUnitOfWork(
            pool,
            closure_authority=closure_authority,
            closure_reconciler_id="reconciler-001",
            closure_checked_at=checked_at,
        ) as gateway:
            await ReconcilePreexecutionCancellation(
                gateway=gateway,
                proof=ProvePreexecutionClosure(gateway=gateway),
            ).execute(
                ReconcilePreexecutionCancellationCommand(
                    batch_id="batch-001",
                    reconciler_id=reconciler_id,
                    closure_epoch=closure_epoch,
                )
            )

    if expected_reason is not None:
        assert rejected.value.reason == expected_reason
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM qep_materialized_scope_handoffs) AS handoff_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(counts) == {"handoff_count": 0, "audit_count": 1, "outbox_count": 1}


@pytest.mark.asyncio
async def test_closure_without_a_cancellation_intent_fails_before_child_scan(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    await _seed_batch(pool)
    candidate_intent = _intent(_authority())
    closure_authority = replace(
        _closure_authority(candidate_intent),
        source_batch_version=3,
    )

    with pytest.raises(PreexecutionStateConflict) as missing:
        async with PostgresBatchCancellationUnitOfWork(
            pool,
            closure_authority=closure_authority,
            closure_reconciler_id="reconciler-001",
            closure_checked_at=candidate_intent.recorded_at + timedelta(minutes=1),
        ) as gateway:
            await ReconcilePreexecutionCancellation(
                gateway=gateway,
                proof=ProvePreexecutionClosure(gateway=gateway),
            ).execute(
                ReconcilePreexecutionCancellationCommand(
                    batch_id="batch-001",
                    reconciler_id="reconciler-001",
                    closure_epoch=1,
                )
            )

    assert missing.value.reason == "closure_command_missing"


@pytest.mark.asyncio
async def test_closure_fails_closed_when_one_batch_has_multiple_cancellation_intents(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent = await _seed_cancellation_intent(pool)
    async with pool.acquire() as connection:
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
                'corrupt-closure-second-intent', batch_id, project_id, suite_revision_id,
                source_batch_version, 'cancel-002', source, actor_id, reason, $1,
                authorization_digest, scope_kind, preplan_scope_digest, manifest_digest,
                shard_plan_version, shard_plan_digest, canonical_run_set_digest,
                $2, payload, recorded_at
            FROM qep_batch_cancellation_intents
            WHERE idempotency_key = 'cancel-001'
            """,
            "e" * 64,
            "d" * 64,
        )

    with pytest.raises(PreexecutionStateConflict) as corrupt:
        async with PostgresBatchCancellationUnitOfWork(
            pool,
            closure_authority=_closure_authority(intent),
            closure_reconciler_id="reconciler-001",
            closure_checked_at=intent.recorded_at + timedelta(minutes=1),
        ) as gateway:
            await ReconcilePreexecutionCancellation(
                gateway=gateway,
                proof=ProvePreexecutionClosure(gateway=gateway),
            ).execute(
                ReconcilePreexecutionCancellationCommand(
                    batch_id="batch-001",
                    reconciler_id="reconciler-001",
                    closure_epoch=1,
                )
            )

    assert corrupt.value.reason == "stored_cancellation_cardinality_invalid"


@pytest.mark.asyncio
async def test_integrity_quarantine_aborts_the_current_unit_of_work(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent = await _seed_cancellation_intent(pool)

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=_closure_authority(intent),
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        await gateway.require_closure_authority(
            batch_id="batch-001",
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
        await gateway.quarantine_integrity_failure(batch_id="batch-001")
        with pytest.raises(PortContractError) as aborted:
            await gateway.get_batch_for_update(batch_id="batch-001")

    assert aborted.value.reason == "aborted"


@pytest.mark.asyncio
async def test_materialized_handoff_ports_fail_closed_when_called_out_of_order_or_forged(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent = await _seed_cancellation_intent(pool)
    await _seed_materialized_run(pool)
    closure_authority = _closure_authority(intent)
    run_set_digest = canonical_materialized_run_set_digest(
        batch_id="batch-001",
        run_ids=("run-001",),
    )
    handoff = build_cancel_handoff(
        intent=intent,
        source_batch_version=closure_authority.source_batch_version,
        authoritative_run_set_digest=run_set_digest,
        authority_digest=closure_authority.authority_digest,
        write_epoch=closure_authority.write_epoch,
    )

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        authority=_authority(),
    ) as gateway:
        await gateway.require_cancel_authority(batch_id="batch-001")
        await gateway.get_batch_for_update(batch_id="batch-001")
        await gateway.scan_execution_children(batch_id="batch-001")
        with pytest.raises(PortContractError) as missing_closure_authority:
            await gateway.publish_materialized_handoff(handoff=handoff)

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=closure_authority,
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        await gateway.require_closure_authority(
            batch_id="batch-001",
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
        await gateway.scan_execution_children(batch_id="batch-001")
        with pytest.raises(PortContractError) as missing_snapshot:
            await gateway.publish_materialized_handoff(handoff=handoff)

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=closure_authority,
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        await gateway.require_closure_authority(
            batch_id="batch-001",
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
        snapshot = await gateway.get_batch_for_update(batch_id="batch-001")
        with pytest.raises(AuthorityStateConflict) as missing_scan:
            await gateway.publish_materialized_handoff(handoff=handoff)

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=closure_authority,
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        await gateway.require_closure_authority(
            batch_id="batch-001",
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
        await gateway.get_batch_for_update(batch_id="batch-001")
        await gateway.scan_execution_children(batch_id="batch-001")
        with pytest.raises(AuthorityStateConflict) as forged:
            await gateway.publish_materialized_handoff(
                handoff=replace(handoff, destination="forged")
            )

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=closure_authority,
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        await gateway.require_closure_authority(
            batch_id="batch-001",
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
        await gateway.get_batch_for_update(batch_id="batch-001")
        with pytest.raises(PortContractError) as missing_cancel_authority:
            await gateway.publish_cancellation(batch=snapshot, intent=intent)

    assert missing_closure_authority.value.reason == "closure_authority_not_locked"
    assert missing_snapshot.value.reason == "snapshot_not_loaded"
    assert missing_scan.value.reason == "materialized_handoff_source_missing"
    assert forged.value.reason == "materialized_handoff_authority_superseded"
    assert missing_cancel_authority.value.reason == "cancel_authority_not_locked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["scalar_payload", "invalid_source_version", "missing_authority_digest"],
)
async def test_materialized_handoff_replay_rejects_malformed_stored_envelopes(
    batch_cancellation_store: PostgresStore,
    corruption: str,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent, first = await _seed_and_publish_materialized_handoff(pool)
    async with pool.acquire() as connection:
        if corruption == "scalar_payload":
            await connection.execute(
                "UPDATE qep_materialized_scope_handoffs SET payload = '7'::jsonb WHERE id = $1",
                first.handoff_id,
            )
        elif corruption == "invalid_source_version":
            await connection.execute(
                """
                UPDATE qep_materialized_scope_handoffs
                SET payload = jsonb_set(payload, '{source_batch_version}', 'true'::jsonb)
                WHERE id = $1
                """,
                first.handoff_id,
            )
        else:
            await connection.execute(
                """
                UPDATE qep_materialized_scope_handoffs
                SET payload = payload - 'authority_digest'
                WHERE id = $1
                """,
                first.handoff_id,
            )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        await _reconcile_materialized_handoff(pool, intent=intent)

    assert corrupt.value.reason == "stored_materialized_handoff_integrity_invalid"


@pytest.mark.asyncio
async def test_materialized_handoff_replay_rejects_a_corrupt_stored_digest(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent, first = await _seed_and_publish_materialized_handoff(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_materialized_scope_handoffs
            SET handoff_digest = $1
            WHERE id = $2
            """,
            "e" * 64,
            first.handoff_id,
        )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        await _reconcile_materialized_handoff(pool, intent=intent)

    assert corrupt.value.reason == "stored_materialized_handoff_integrity_invalid"


@pytest.mark.asyncio
async def test_materialized_handoff_replay_rejects_coordinated_payload_corruption(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent, first = await _seed_and_publish_materialized_handoff(pool)
    corrupt_run_set = _named_digest("corrupt-materialized-run-set")
    async with pool.acquire() as connection:
        payload = json.loads(
            await connection.fetchval(
                "SELECT payload FROM qep_materialized_scope_handoffs WHERE id = $1",
                first.handoff_id,
            )
        )
        payload["authoritative_run_set_digest"] = corrupt_run_set.value
        payload["handoff_digest"] = f"sha256:{'e' * 64}"
        await connection.execute(
            """
            UPDATE qep_materialized_scope_handoffs
            SET materialized_run_set_digest = $1,
                handoff_digest = $2,
                payload = $3
            WHERE id = $4
            """,
            _digest_hex(corrupt_run_set),
            "e" * 64,
            json.dumps(payload),
            first.handoff_id,
        )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        await _reconcile_materialized_handoff(pool, intent=intent)

    assert corrupt.value.reason == "stored_materialized_handoff_integrity_invalid"


@pytest.mark.asyncio
async def test_materialized_handoff_run_set_drift_is_an_idempotency_conflict(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    intent, first = await _seed_and_publish_materialized_handoff(pool)
    await _seed_additional_materialized_run(pool)

    with pytest.raises(IdempotencyConflict) as drift:
        await _reconcile_materialized_handoff(pool, intent=intent)

    assert drift.value.stored_digest == first.handoff_digest
    assert drift.value.received_digest != first.handoff_digest
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM qep_materialized_scope_handoffs) AS handoff_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(counts) == {"handoff_count": 1, "audit_count": 2, "outbox_count": 2}


@pytest.mark.asyncio
async def test_caught_missing_handoff_audit_aborts_and_rolls_back_the_handoff(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    cancel_authority = _authority()
    await _seed_batch(pool)
    async with PostgresBatchCancellationUnitOfWork(
        pool,
        authority=cancel_authority,
    ) as gateway:
        intent = await RequestBatchCancellation(gateway=gateway).execute(
            RequestBatchCancellationCommand(
                batch_id="batch-001",
                expected_batch_version=3,
                idempotency_key="cancel-001",
                reason="stop before execution starts",
            )
        )
    await _seed_materialized_run(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_handoff_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action = 'publish_batch_materialized_handoff' THEN
                    RETURN NULL;
                END IF;
                RETURN NEW;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_handoff_audit
            BEFORE INSERT ON qep_audit_events
            FOR EACH ROW EXECUTE FUNCTION suppress_handoff_audit()
            """
        )

    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=_closure_authority(intent),
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        with pytest.raises(AuthorityStateConflict) as missing:
            await ReconcilePreexecutionCancellation(
                gateway=gateway,
                proof=ProvePreexecutionClosure(gateway=gateway),
            ).execute(
                ReconcilePreexecutionCancellationCommand(
                    batch_id="batch-001",
                    reconciler_id="reconciler-001",
                    closure_epoch=1,
                )
            )
        with pytest.raises(PortContractError) as aborted:
            await gateway.scan_execution_children(batch_id="batch-001")

    assert missing.value.reason == "batch_materialized_handoff_audit_write_missing"
    assert aborted.value.reason == "aborted"
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM qep_materialized_scope_handoffs) AS handoff_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(counts) == {"handoff_count": 0, "audit_count": 1, "outbox_count": 1}


@pytest.mark.asyncio
async def test_exact_replay_returns_the_first_intent_without_duplicate_side_effects(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    command = RequestBatchCancellationCommand(
        batch_id="batch-001",
        expected_batch_version=3,
        idempotency_key="cancel-001",
        reason="stop before execution starts",
    )
    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        first = await RequestBatchCancellation(gateway=gateway).execute(command)

    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        replay = await RequestBatchCancellation(gateway=gateway).execute(command)

    assert replay == first
    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_version": 4,
        "intent_count": 1,
        "audit_count": 1,
        "outbox_count": 1,
    }


@pytest.mark.asyncio
async def test_expired_authority_fails_before_reading_or_replaying_the_stored_intent(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    command = RequestBatchCancellationCommand(
        batch_id="batch-001",
        expected_batch_version=3,
        idempotency_key="cancel-001",
        reason="stop before execution starts",
    )
    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        first = await RequestBatchCancellation(gateway=gateway).execute(command)

    expired = replace(
        authority,
        projection=replace(authority.projection, expires_at=authority.recorded_at),
    )
    with pytest.raises(TemporarilyUnavailable):
        async with PostgresBatchCancellationUnitOfWork(pool, authority=expired) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(command)

    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT intent_digest FROM qep_batch_cancellation_intents) AS intent_digest,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_version": 4,
        "intent_digest": _digest_hex(first.digest),
        "audit_count": 1,
        "outbox_count": 1,
    }


@pytest.mark.asyncio
async def test_replay_fails_closed_when_the_stored_request_digest_is_corrupt(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    command = RequestBatchCancellationCommand(
        batch_id="batch-001",
        expected_batch_version=3,
        idempotency_key="cancel-001",
        reason="stop before execution starts",
    )
    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        first = await RequestBatchCancellation(gateway=gateway).execute(command)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_batch_cancellation_intents SET request_digest = $1",
            "f" * 64,
        )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(command)

    assert corrupt.value.reason == "stored_cancellation_integrity_invalid"
    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT intent_digest FROM qep_batch_cancellation_intents) AS intent_digest,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_version": 4,
        "intent_digest": _digest_hex(first.digest),
        "audit_count": 1,
        "outbox_count": 1,
    }


@pytest.mark.asyncio
async def test_replay_fails_closed_when_one_batch_has_multiple_cancellation_intents(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    command = RequestBatchCancellationCommand(
        batch_id="batch-001",
        expected_batch_version=3,
        idempotency_key="cancel-001",
        reason="stop before execution starts",
    )
    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        await RequestBatchCancellation(gateway=gateway).execute(command)
    async with pool.acquire() as connection:
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
                'corrupt-second-intent', batch_id, project_id, suite_revision_id,
                source_batch_version, 'cancel-002', source, actor_id, reason, $1,
                authorization_digest, scope_kind, preplan_scope_digest, manifest_digest,
                shard_plan_version, shard_plan_digest, canonical_run_set_digest,
                $2, payload, recorded_at
            FROM qep_batch_cancellation_intents
            WHERE idempotency_key = 'cancel-001'
            """,
            "e" * 64,
            "d" * 64,
        )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(command)

    assert corrupt.value.reason == "stored_cancellation_cardinality_invalid"
    async with pool.acquire() as connection:
        counts = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(counts) == {
        "batch_version": 4,
        "intent_count": 2,
        "audit_count": 1,
        "outbox_count": 1,
    }


@pytest.mark.asyncio
async def test_caught_outbox_conflict_aborts_and_rolls_back_every_cancellation_write(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    candidate = _intent(authority)
    event_id = f"batch-cancel-requested-{_digest_hex(candidate.digest)}"
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, available_at, created_at
            ) VALUES ($1, $2, 'batch', 'batch-001', 'poison.v1', $3, $4, $5, $5)
            """,
            "preexisting-poison-event",
            event_id,
            "e" * 64,
            json.dumps({"poison": True}),
            authority.recorded_at,
        )

    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        with pytest.raises(asyncpg.UniqueViolationError):
            await RequestBatchCancellation(gateway=gateway).execute(
                RequestBatchCancellationCommand(
                    batch_id="batch-001",
                    expected_batch_version=3,
                    idempotency_key="cancel-001",
                    reason="stop before execution starts",
                )
            )
        with pytest.raises(PortContractError) as aborted:
            await gateway.require_cancel_authority(batch_id="batch-001")
        assert aborted.value.reason == "aborted"

    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_version": 3,
        "intent_count": 0,
        "audit_count": 0,
        "outbox_count": 1,
    }


@pytest.mark.asyncio
async def test_suppressed_audit_insert_fails_closed_and_rolls_back_the_cancellation(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_cancellation_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_cancellation_audit
            BEFORE INSERT ON qep_audit_events
            FOR EACH ROW EXECUTE FUNCTION suppress_cancellation_audit()
            """
        )

    with pytest.raises(AuthorityStateConflict) as missing:
        async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(
                RequestBatchCancellationCommand(
                    batch_id="batch-001",
                    expected_batch_version=3,
                    idempotency_key="cancel-001",
                    reason="stop before execution starts",
                )
            )

    assert missing.value.reason == "batch_cancellation_audit_write_missing"
    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_version": 3,
        "intent_count": 0,
        "audit_count": 0,
        "outbox_count": 0,
    }


@pytest.mark.asyncio
async def test_rejected_terminal_blocks_late_cancellation_before_version_cas(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_rejected_batch(pool)

    with pytest.raises(PreexecutionStateConflict) as rejected:
        async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(
                RequestBatchCancellationCommand(
                    batch_id="batch-001",
                    expected_batch_version=3,
                    idempotency_key="late-cancel-001",
                    reason="too late",
                )
            )

    assert rejected.value.reason == "rejection_terminal_winner"
    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT state FROM qep_batches WHERE id = 'batch-001') AS batch_state,
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_state": "rejected",
        "batch_version": 4,
        "intent_count": 0,
        "audit_count": 0,
        "outbox_count": 0,
    }


@pytest.mark.asyncio
async def test_exact_request_replays_the_first_intent_after_batch_cancel_terminal(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    command = RequestBatchCancellationCommand(
        batch_id="batch-001",
        expected_batch_version=3,
        idempotency_key="cancel-001",
        reason="stop before execution starts",
    )
    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        first = await RequestBatchCancellation(gateway=gateway).execute(command)
    await _close_cancelled_batch(pool, intent=first)

    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        replay = await RequestBatchCancellation(gateway=gateway).execute(command)

    assert replay == first
    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT state FROM qep_batches WHERE id = 'batch-001') AS batch_state,
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_batch_preexecution_closure_bases) AS basis_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_state": "cancelled",
        "batch_version": 5,
        "intent_count": 1,
        "basis_count": 1,
        "audit_count": 1,
        "outbox_count": 1,
    }


@pytest.mark.asyncio
async def test_changed_project_authority_is_denied_before_stored_intent_replay(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    command = RequestBatchCancellationCommand(
        batch_id="batch-001",
        expected_batch_version=3,
        idempotency_key="cancel-001",
        reason="stop before execution starts",
    )
    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        first = await RequestBatchCancellation(gateway=gateway).execute(command)

    changed = replace(authority, project_id="project-other")
    with pytest.raises(ObjectForbidden):
        async with PostgresBatchCancellationUnitOfWork(pool, authority=changed) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(command)

    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT intent_digest FROM qep_batch_cancellation_intents) AS intent_digest,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "intent_digest": _digest_hex(first.digest),
        "audit_count": 1,
        "outbox_count": 1,
    }


@pytest.mark.asyncio
async def test_batch_cas_failure_rolls_back_without_publishing_an_intent(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE FUNCTION suppress_batch_cancel_update() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RETURN NULL;
            END
            $$
            """
        )
        await connection.execute(
            """
            CREATE TRIGGER suppress_batch_cancel_update
            BEFORE UPDATE ON qep_batches
            FOR EACH ROW EXECUTE FUNCTION suppress_batch_cancel_update()
            """
        )

    with pytest.raises(VersionConflict):
        async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(
                RequestBatchCancellationCommand(
                    batch_id="batch-001",
                    expected_batch_version=3,
                    idempotency_key="cancel-001",
                    reason="stop before execution starts",
                )
            )

    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_version": 3,
        "intent_count": 0,
        "audit_count": 0,
        "outbox_count": 0,
    }


@pytest.mark.asyncio
async def test_batch_cancellation_unit_of_work_is_one_shot_and_one_aggregate_scoped(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    inactive = PostgresBatchCancellationUnitOfWork(pool, authority=authority)
    with pytest.raises(PortContractError, match="not_active"):
        await inactive.require_cancel_authority(batch_id="batch-001")
    with pytest.raises(PortContractError, match="not_active"):
        await inactive.__aexit__(None, None, None)

    unit_of_work = PostgresBatchCancellationUnitOfWork(pool, authority=authority)
    async with unit_of_work as gateway:
        with pytest.raises(PortContractError, match="already_active"):
            await gateway.__aenter__()
        with pytest.raises(PortContractError, match="authority_not_locked"):
            await gateway.get_batch_for_update(batch_id="batch-001")
        locked = await gateway.require_cancel_authority(batch_id="batch-001")
        assert await gateway.require_cancel_authority(batch_id="batch-001") == locked
        with pytest.raises(PortContractError, match="aggregate_already_locked"):
            await gateway.require_cancel_authority(batch_id="other-batch")

    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.require_cancel_authority(batch_id="batch-001")
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()


@pytest.mark.asyncio
async def test_direct_publication_requires_the_locked_snapshot_and_current_authority(
    batch_cancellation_store: PostgresStore,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_batch(pool)
    intent = _intent(authority)
    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    candidate = source.request_cancel(intent=intent, expected_version=source.version)

    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        await gateway.require_cancel_authority(batch_id="batch-001")
        with pytest.raises(PortContractError, match="snapshot_not_loaded"):
            await gateway.publish_cancellation(batch=candidate, intent=intent)

    forged_authority = replace(
        authority,
        authorization_digest=_named_digest("forged-authority"),
    )
    forged_intent = _intent(forged_authority)
    forged_candidate = source.request_cancel(
        intent=forged_intent,
        expected_version=source.version,
    )
    async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
        await gateway.require_cancel_authority(batch_id="batch-001")
        await gateway.get_batch_for_update(batch_id="batch-001")
        with pytest.raises(AuthorityStateConflict) as superseded:
            await gateway.publish_cancellation(
                batch=forged_candidate,
                intent=forged_intent,
            )
        assert superseded.value.reason == "cancel_publication_authority_superseded"

    async with pool.acquire() as connection:
        persisted = await connection.fetchrow(
            """
            SELECT
                (SELECT version FROM qep_batches WHERE id = 'batch-001') AS batch_version,
                (SELECT count(*) FROM qep_batch_cancellation_intents) AS intent_count,
                (SELECT count(*) FROM qep_audit_events) AS audit_count,
                (SELECT count(*) FROM qep_outbox_events) AS outbox_count
            """
        )
    assert dict(persisted) == {
        "batch_version": 3,
        "intent_count": 0,
        "audit_count": 0,
        "outbox_count": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("second_rejection", "stored_rejection_cardinality_invalid"),
        ("rejection_digest", "stored_rejection_integrity_invalid"),
        ("basis_digest", "stored_preexecution_basis_integrity_invalid"),
        ("basis_payload_digest_type", "stored_digest_value_invalid"),
    ],
)
async def test_corrupt_rejection_terminal_fails_closed_before_late_cancel(
    batch_cancellation_store: PostgresStore,
    corruption: str,
    reason: str,
) -> None:
    pool = batch_cancellation_store._require_pool()
    authority = _authority()
    await _seed_rejected_batch(pool)
    async with pool.acquire() as connection:
        if corruption == "second_rejection":
            await connection.execute(
                """
                INSERT INTO qep_batch_rejections (
                    id, batch_id, source_batch_version, stage, reason_class, reason_code,
                    input_digest, authority_digest, rejection_digest, payload, recorded_at
                )
                SELECT
                    'rejection-002', batch_id, 4, stage, reason_class, reason_code,
                    $1, authority_digest, $2, payload, recorded_at
                FROM qep_batch_rejections
                """,
                "e" * 64,
                "d" * 64,
            )
        elif corruption == "rejection_digest":
            await connection.execute(
                "UPDATE qep_batch_rejections SET rejection_digest = $1",
                "f" * 64,
            )
        elif corruption == "basis_digest":
            await connection.execute(
                "UPDATE qep_batch_preexecution_closure_bases SET basis_digest = $1",
                "f" * 64,
            )
        else:
            payload = await connection.fetchval(
                "SELECT payload FROM qep_batch_preexecution_closure_bases"
            )
            decoded = json.loads(payload)
            decoded["submission_digest"] = 7
            await connection.execute(
                "UPDATE qep_batch_preexecution_closure_bases SET payload = $1",
                json.dumps(decoded),
            )

    with pytest.raises(AuthorityStateConflict) as corrupt:
        async with PostgresBatchCancellationUnitOfWork(pool, authority=authority) as gateway:
            await RequestBatchCancellation(gateway=gateway).execute(
                RequestBatchCancellationCommand(
                    batch_id="batch-001",
                    expected_batch_version=3,
                    idempotency_key="late-cancel-001",
                    reason="too late",
                )
            )

    assert corrupt.value.reason == reason
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval("SELECT count(*) FROM qep_batch_cancellation_intents") == 0
        )


async def _seed_batch(pool: asyncpg.Pool) -> None:
    recorded_at = datetime(2026, 7, 18, 10, tzinfo=UTC)
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO qep_projects (id, name, created_at) VALUES ($1, $2, $3)",
            "project-001",
            "Project 001",
            recorded_at,
        )
        await connection.execute(
            """
            INSERT INTO qep_principals (id, issuer, subject, display_name, created_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            "user-001",
            "test",
            "user-001",
            "User 001",
            recorded_at,
        )
        await connection.execute(
            """
            INSERT INTO qep_role_bindings (principal_id, role, project_id, granted_at)
            VALUES ($1, $2, $3, $4)
            """,
            "user-001",
            "batch_canceller",
            "project-001",
            recorded_at,
        )
        await connection.execute(
            "INSERT INTO qep_suites (id, project_id, name, created_at) VALUES ($1, $2, $3, $4)",
            "suite-001",
            "project-001",
            "Suite 001",
            recorded_at,
        )
        await connection.execute(
            """
            INSERT INTO qep_suite_revisions (
                id, suite_id, revision_no, source_spec_digest, config_digest,
                framework, status, payload, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9
            )
            """,
            "suite-revision-001",
            "suite-001",
            1,
            "1" * 64,
            "2" * 64,
            "pytest",
            "approved",
            json.dumps({}),
            recorded_at,
        )
        await connection.execute(
            """
            INSERT INTO qep_batches (
                id, project_id, suite_revision_id, request_digest,
                idempotency_scope, idempotency_key, state, version, write_epoch,
                created_at, updated_at, payload
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10, $11
            )
            """,
            "batch-001",
            "project-001",
            "suite-revision-001",
            "3" * 64,
            "project-001",
            "batch-create-001",
            "collecting",
            3,
            1,
            recorded_at,
            json.dumps({}),
        )


async def _seed_and_publish_materialized_handoff(
    pool: asyncpg.Pool,
) -> tuple[BatchCancellationIntent, BatchMaterializedScopeHandoff]:
    intent = await _seed_cancellation_intent(pool)
    await _seed_materialized_run(pool)
    return intent, await _reconcile_materialized_handoff(pool, intent=intent)


async def _seed_cancellation_intent(pool: asyncpg.Pool) -> BatchCancellationIntent:
    cancel_authority = _authority()
    await _seed_batch(pool)
    async with PostgresBatchCancellationUnitOfWork(
        pool,
        authority=cancel_authority,
    ) as gateway:
        intent = await RequestBatchCancellation(gateway=gateway).execute(
            RequestBatchCancellationCommand(
                batch_id="batch-001",
                expected_batch_version=3,
                idempotency_key="cancel-001",
                reason="stop before execution starts",
            )
        )
    return intent


async def _reconcile_materialized_handoff(
    pool: asyncpg.Pool,
    *,
    intent: BatchCancellationIntent,
) -> BatchMaterializedScopeHandoff:
    async with PostgresBatchCancellationUnitOfWork(
        pool,
        closure_authority=_closure_authority(intent),
        closure_reconciler_id="reconciler-001",
        closure_checked_at=intent.recorded_at + timedelta(minutes=1),
    ) as gateway:
        result = await ReconcilePreexecutionCancellation(
            gateway=gateway,
            proof=ProvePreexecutionClosure(gateway=gateway),
        ).execute(
            ReconcilePreexecutionCancellationCommand(
                batch_id="batch-001",
                reconciler_id="reconciler-001",
                closure_epoch=1,
            )
        )
    assert isinstance(result, BatchMaterializedScopeHandoff)
    return result


async def _seed_materialized_run(pool: asyncpg.Pool) -> None:
    recorded_at = datetime(2026, 7, 18, 10, 3, tzinfo=UTC)
    empty = json.dumps({})
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            """
            INSERT INTO qep_resource_profiles (
                id, name, profile_version, framework, requests, limits,
                internal_workers, security_profile_id, approved_at, created_at
            ) VALUES ($1, $2, 1, 'pytest', $3, $3, 1, $4, $5, $5)
            """,
            "profile-001",
            "Default",
            empty,
            "security-profile-001",
            recorded_at,
        )
        await connection.execute(
            """
            INSERT INTO qep_shard_plans (
                id, batch_id, algorithm_version, digest, run_count,
                total_estimated_duration_ms, status, payload, created_at
            ) VALUES ($1, $2, 'single-shard.v1', $3, 2, 1, 'approved', $4, $5)
            """,
            "plan-001",
            "batch-001",
            "4" * 64,
            empty,
            recorded_at,
        )
        await connection.execute(
            """
            INSERT INTO qep_runs (
                id, batch_id, plan_id, shard_index, resource_profile_id,
                orchestration_phase, current_fence, attempt_count, version,
                run_item_set_digest, created_at, updated_at, payload
            ) VALUES ($1, $2, $3, 0, $4, 'planned', 0, 0, 0, $5, $6, $6, $7)
            """,
            "run-001",
            "batch-001",
            "plan-001",
            "profile-001",
            "5" * 64,
            recorded_at,
            empty,
        )


async def _seed_additional_materialized_run(pool: asyncpg.Pool) -> None:
    recorded_at = datetime(2026, 7, 18, 10, 4, tzinfo=UTC)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_runs (
                id, batch_id, plan_id, shard_index, resource_profile_id,
                orchestration_phase, current_fence, attempt_count, version,
                run_item_set_digest, created_at, updated_at, payload
            ) VALUES ($1, $2, $3, 1, $4, 'planned', 0, 0, 0, $5, $6, $6, $7)
            """,
            "run-002",
            "batch-001",
            "plan-001",
            "profile-001",
            "6" * 64,
            recorded_at,
            json.dumps({}),
        )


async def _seed_rejected_batch(pool: asyncpg.Pool) -> None:
    await _seed_batch(pool)
    recorded_at = datetime(2026, 7, 18, 10, 2, tzinfo=UTC)
    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id=source.id,
        source_batch_version=source.version,
        stage=BatchRejectionStage.COLLECTION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="source_collection_failed",
        input_digest=_named_digest("rejection-input"),
        authority_digest=None,
        recorded_at=recorded_at,
    )
    snapshot = BatchPreexecutionSnapshot(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
        submission_digest=_named_digest("submission"),
        preplan_scope_digest=_authority().scope.preplan_scope_digest,
        manifest_digest=None,
        shard_plan_version=None,
        shard_plan_digest=None,
        canonical_run_set_digest=None,
        materialized_run_absence_digest=_named_digest("no-materialized-runs"),
        execution_absence_snapshot_digest=_named_digest("no-execution"),
        task_stop_fact_digests=(),
        scope_items=(),
        item_coverage_proof_digest=None,
    )
    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    basis = rejected.preexecution_closure_basis
    assert basis is not None
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_batches
            SET state = $1, version = $2, updated_at = $3
            WHERE id = $4
            """,
            rejected.state.value,
            rejected.version,
            recorded_at,
            rejected.id,
        )
        await connection.execute(
            """
            INSERT INTO qep_batch_rejections (
                id, batch_id, source_batch_version, stage, reason_class, reason_code,
                input_digest, authority_digest, rejection_digest, payload, recorded_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            rejection.rejection_id,
            rejection.batch_id,
            rejection.source_batch_version,
            rejection.stage.value,
            rejection.reason_class.value,
            rejection.reason_code,
            _digest_hex(rejection.input_digest),
            None,
            _digest_hex(rejection.digest),
            json.dumps(_rejection_payload(rejection)),
            rejection.recorded_at,
        )
        await connection.execute(
            """
            INSERT INTO qep_batch_preexecution_closure_bases (
                id, batch_id, source_batch_version, source_phase, terminal_kind,
                command_digest, scope_kind, materialized_run_absence_digest,
                execution_absence_snapshot_digest, basis_digest, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            "basis-rejection-001",
            basis.batch_id,
            basis.source_batch_version,
            basis.source_phase.value,
            basis.terminal_kind.value,
            _digest_hex(rejection.digest),
            basis.scope_kind.value,
            _digest_hex(basis.materialized_run_absence_digest),
            _digest_hex(basis.execution_absence_snapshot_digest),
            _digest_hex(basis.digest),
            json.dumps(_basis_payload(basis)),
            recorded_at,
        )


async def _close_cancelled_batch(
    pool: asyncpg.Pool,
    *,
    intent: BatchCancellationIntent,
) -> None:
    source = Batch(
        id=intent.batch_id,
        state=BatchState.COLLECTING,
        version=intent.source_batch_version + 1,
        cancellation_intent=intent,
    )
    snapshot = BatchPreexecutionSnapshot(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
        submission_digest=_named_digest("submission"),
        preplan_scope_digest=intent.scope.preplan_scope_digest,
        manifest_digest=None,
        shard_plan_version=None,
        shard_plan_digest=None,
        canonical_run_set_digest=None,
        materialized_run_absence_digest=_named_digest("no-materialized-runs"),
        execution_absence_snapshot_digest=_named_digest("no-execution"),
        task_stop_fact_digests=(),
        scope_items=(),
        item_coverage_proof_digest=None,
    )
    cancelled = source.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=source.version,
    )
    basis = cancelled.preexecution_closure_basis
    assert basis is not None
    recorded_at = intent.recorded_at + timedelta(minutes=1)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE qep_batches
            SET state = $1, version = $2, updated_at = $3
            WHERE id = $4
            """,
            cancelled.state.value,
            cancelled.version,
            recorded_at,
            cancelled.id,
        )
        await connection.execute(
            """
            INSERT INTO qep_batch_preexecution_closure_bases (
                id, batch_id, source_batch_version, source_phase, terminal_kind,
                command_digest, scope_kind, materialized_run_absence_digest,
                execution_absence_snapshot_digest, basis_digest, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            "basis-cancel-001",
            basis.batch_id,
            basis.source_batch_version,
            basis.source_phase.value,
            basis.terminal_kind.value,
            _digest_hex(intent.digest),
            basis.scope_kind.value,
            _digest_hex(basis.materialized_run_absence_digest),
            _digest_hex(basis.execution_absence_snapshot_digest),
            _digest_hex(basis.digest),
            json.dumps(_basis_payload(basis)),
            recorded_at,
        )


def _authority() -> BatchCancellationAuthority:
    recorded_at = datetime(2026, 7, 18, 10, 1, tzinfo=UTC)
    return BatchCancellationAuthority(
        batch_id="batch-001",
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        actor_id="user-001",
        source=CancellationSource.USER_REQUEST,
        authorization_digest=canonical_digest(
            schema_version="qep.test-batch-cancel-authority.v1",
            payload={"actor_id": "user-001", "batch_id": "batch-001"},
        ),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=canonical_digest(
                schema_version="qep.test-batch-cancel-scope.v1",
                payload={"batch_id": "batch-001", "phase": "collecting"},
            ),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        projection=AuthorityProjectionStamp(
            source="local-authority-projection",
            projection_version=7,
            revocation_watermark=11,
            expires_at=recorded_at + timedelta(minutes=5),
        ),
        recorded_at=recorded_at,
    )


def _closure_authority(intent: BatchCancellationIntent) -> BatchClosureAuthority:
    return BatchClosureAuthority(
        batch_id=intent.batch_id,
        project_id=intent.project_id,
        suite_revision_id=intent.suite_revision_id,
        source_batch_version=intent.source_batch_version + 1,
        authority_digest=canonical_digest(
            schema_version="qep.test-batch-closure-authority.v1",
            payload={
                "batch_id": intent.batch_id,
                "reconciler_id": "reconciler-001",
                "write_epoch": 1,
            },
        ),
        scope=intent.scope,
        projection=AuthorityProjectionStamp(
            source="local-authority-projection",
            projection_version=7,
            revocation_watermark=11,
            expires_at=intent.recorded_at + timedelta(minutes=5),
        ),
        write_epoch=1,
    )


def _intent(authority: BatchCancellationAuthority) -> BatchCancellationIntent:
    return BatchCancellationIntent(
        batch_id=authority.batch_id,
        project_id=authority.project_id,
        suite_revision_id=authority.suite_revision_id,
        source_batch_version=3,
        idempotency_key="cancel-001",
        source=authority.source,
        actor_id=authority.actor_id,
        reason="stop before execution starts",
        authorization_digest=authority.authorization_digest,
        scope=authority.scope,
        recorded_at=authority.recorded_at,
    )


def _rejection_payload(rejection: BatchRejection) -> dict[str, object]:
    return {
        "schema_version": "qep.batch-rejection.v1",
        "rejection_id": rejection.rejection_id,
        "batch_id": rejection.batch_id,
        "source_batch_version": rejection.source_batch_version,
        "stage": rejection.stage.value,
        "reason_class": rejection.reason_class.value,
        "reason_code": rejection.reason_code,
        "input_digest": rejection.input_digest.value,
        "authority_digest": None,
        "recorded_at": rejection.recorded_at.isoformat().replace("+00:00", "Z"),
        "rejection_digest": rejection.digest.value,
    }


def _basis_payload(basis) -> dict[str, object]:
    return {
        "schema_version": "qep.batch-preexecution-closure-basis.v1",
        "batch_id": basis.batch_id,
        "source_batch_version": basis.source_batch_version,
        "source_phase": basis.source_phase.value,
        "terminal_kind": basis.terminal_kind.value,
        "rejection_fact_digest": (
            None if basis.rejection_fact_digest is None else basis.rejection_fact_digest.value
        ),
        "batch_cancellation_intent_digest": (
            None
            if basis.batch_cancellation_intent_digest is None
            else basis.batch_cancellation_intent_digest.value
        ),
        "scope_kind": basis.scope_kind.value,
        "submission_digest": basis.submission_digest.value,
        "preplan_scope_digest": (
            None if basis.preplan_scope_digest is None else basis.preplan_scope_digest.value
        ),
        "manifest_digest": None if basis.manifest_digest is None else basis.manifest_digest.value,
        "shard_plan_version": basis.shard_plan_version,
        "shard_plan_digest": (
            None if basis.shard_plan_digest is None else basis.shard_plan_digest.value
        ),
        "canonical_run_set_digest": (
            None
            if basis.canonical_run_set_digest is None
            else basis.canonical_run_set_digest.value
        ),
        "materialized_run_absence_digest": basis.materialized_run_absence_digest.value,
        "execution_absence_snapshot_digest": basis.execution_absence_snapshot_digest.value,
        "task_stop_fact_digest": [value.value for value in basis.task_stop_fact_digests],
        "preexecution_scope_item_fact_digest": [
            value.value for value in basis.preexecution_scope_item_fact_digests
        ],
        "item_coverage_proof_digest": (
            None
            if basis.item_coverage_proof_digest is None
            else basis.item_coverage_proof_digest.value
        ),
        "batch_outcome": basis.batch_outcome.value,
        "basis_digest": basis.digest.value,
    }


def _named_digest(label: str):
    return canonical_digest(
        schema_version="qep.test-postgres-batch-cancellation.v1",
        payload={"label": label},
    )


def _digest_hex(value) -> str:
    assert value is not None
    return value.value.removeprefix("sha256:")
