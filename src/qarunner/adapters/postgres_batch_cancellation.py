"""PostgreSQL Batch-local UoW for cancellation and rejection materialized handoffs."""

from __future__ import annotations

import json
from datetime import datetime
from types import TracebackType
from typing import Self, cast

import asyncpg

from qarunner.application.handoff import (
    BatchMaterializedScopeHandoff,
    build_cancel_handoff,
    build_rejection_conflict_handoff_from_digest,
)
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
    BatchCancellationAuthority,
    BatchClosureAuthority,
    BatchRejectionAuthority,
    InternalAuthorityRetired,
)
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.preexecution_proof import (
    ExecutionChildInventory,
    PreexecutionTaskGeneration,
    PreexecutionTaskKey,
    SealedPlannedScopeInventory,
    SealedTaskInventory,
    TaskLedgerPosition,
    TrustedTaskStop,
    ZeroChildSnapshotInputs,
)
from qarunner.domain.batch import (
    Batch,
    BatchPreexecutionClosureBasis,
    BatchPreexecutionScopeItem,
    BatchPreexecutionScopeKind,
    BatchPreexecutionSnapshot,
    BatchPreexecutionTerminalKind,
    BatchRejection,
    BatchRejectionReasonClass,
    BatchRejectionStage,
    BatchState,
)
from qarunner.domain.cancellation import (
    BatchCancellationIntent,
    BatchCancellationScope,
    BatchCancellationScopeKind,
    CancellationSource,
)
from qarunner.domain.digest import Digest, canonical_digest, canonical_materialized_run_set_digest
from qarunner.domain.errors import IdempotencyConflict, VersionConflict


class PostgresBatchCancellationUnitOfWork:
    """One-shot transaction for Batch cancellation and materialized-scope handoffs."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        authority: BatchCancellationAuthority | None = None,
        closure_authority: BatchClosureAuthority | None = None,
        closure_reconciler_id: str | None = None,
        closure_checked_at: datetime | None = None,
        rejection_authority: BatchRejectionAuthority | None = None,
        rejection_phase_owner_id: str | None = None,
        rejection_checked_at: datetime | None = None,
        trusted_inventory_issuers: frozenset[str] = frozenset(),
        trusted_stop_issuers: frozenset[str] = frozenset(),
        trusted_planned_scope_issuers: frozenset[str] = frozenset(),
    ) -> None:
        cancel_mode = authority is not None
        closure_mode = closure_authority is not None
        rejection_mode = rejection_authority is not None
        if sum((cancel_mode, closure_mode, rejection_mode)) != 1:
            self._state_error("authority_mode_invalid")
        if closure_mode != (closure_reconciler_id is not None and closure_checked_at is not None):
            self._state_error("closure_authority_binding_invalid")
        if rejection_mode != (
            rejection_phase_owner_id is not None and rejection_checked_at is not None
        ):
            self._state_error("rejection_authority_binding_invalid")
        self._pool = pool
        self._candidate_authority = authority
        self._candidate_closure_authority = closure_authority
        self._closure_reconciler_id = closure_reconciler_id
        self._closure_checked_at = closure_checked_at
        self._candidate_rejection_authority = rejection_authority
        self._rejection_phase_owner_id = rejection_phase_owner_id
        self._rejection_checked_at = rejection_checked_at
        self._trusted_inventory_issuers = trusted_inventory_issuers
        self._trusted_stop_issuers = trusted_stop_issuers
        self._trusted_planned_scope_issuers = trusted_planned_scope_issuers
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._authority: BatchCancellationAuthority | None = None
        self._closure_authority: BatchClosureAuthority | None = None
        self._rejection_authority: BatchRejectionAuthority | None = None
        self._locked_batch_id: str | None = None
        self._materialized_run_set_digest: Digest | None = None
        self._snapshot: Batch | None = None
        self._aborted = False
        self._closed = False

    async def __aenter__(self) -> Self:
        if self._closed:
            self._state_error("closed")
        if self._connection is not None:
            self._state_error("already_active")
        connection = await self._pool.acquire()
        transaction = connection.transaction(isolation="read_committed")
        try:
            await transaction.start()
        except BaseException:
            await self._pool.release(connection)
            self._closed = True
            raise
        self._connection = connection
        self._transaction = transaction
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        transaction = self._transaction
        connection = self._connection
        if transaction is None or connection is None:
            self._state_error("not_active")
        try:
            if exception_type is None and not self._aborted:
                await transaction.commit()
            else:
                await transaction.rollback()
        finally:
            self._transaction = None
            self._connection = None
            self._closed = True
            await self._pool.release(connection)

    async def require_cancel_authority(self, *, batch_id: str) -> BatchCancellationAuthority:
        connection = self._require_connection()
        if self._authority is not None:
            if self._authority.batch_id != batch_id:
                self._state_error("aggregate_already_locked")
            return self._authority
        authority = self._candidate_authority
        if authority is None:
            self._state_error("cancel_authority_not_configured")
        if authority.projection.expires_at <= authority.recorded_at:
            raise AuthorityProjectionUnavailable
        row = await connection.fetchrow(
            """
            SELECT batch.id, batch.project_id, batch.suite_revision_id, batch.state
            FROM qep_batches AS batch
            JOIN qep_suite_revisions AS revision ON revision.id = batch.suite_revision_id
            JOIN qep_suites AS suite ON suite.id = revision.suite_id
            WHERE batch.id = $1
              AND suite.project_id = batch.project_id
            FOR UPDATE OF batch
            """,
            batch_id,
        )
        if row is None or (
            authority.batch_id != row["id"]
            or authority.project_id != row["project_id"]
            or authority.suite_revision_id != row["suite_revision_id"]
        ):
            raise AuthorityPermissionDenied
        self._authority = authority
        self._locked_batch_id = authority.batch_id
        return authority

    async def require_closure_authority(
        self,
        *,
        batch_id: str,
        reconciler_id: str,
        closure_epoch: int,
    ) -> BatchClosureAuthority:
        connection = self._require_connection()
        if self._closure_authority is not None:
            if self._closure_authority.batch_id != batch_id:
                self._state_error("aggregate_already_locked")
            if reconciler_id != self._closure_reconciler_id:
                raise InternalAuthorityRetired(reason="reconciler_authority_retired")
            if closure_epoch != self._closure_authority.write_epoch:
                raise AuthorityStateConflict(reason="closure_epoch_superseded")
            return self._closure_authority
        authority = self._candidate_closure_authority
        checked_at = self._closure_checked_at
        if authority is None or checked_at is None:
            self._state_error("closure_authority_not_configured")
        if authority.projection.expires_at <= checked_at:
            raise AuthorityProjectionUnavailable
        if reconciler_id != self._closure_reconciler_id:
            raise InternalAuthorityRetired(reason="reconciler_authority_retired")
        if closure_epoch != authority.write_epoch:
            raise AuthorityStateConflict(reason="closure_epoch_superseded")
        row = await connection.fetchrow(
            """
            SELECT
                batch.id,
                batch.project_id,
                batch.suite_revision_id,
                batch.version,
                batch.write_epoch
            FROM qep_batches AS batch
            JOIN qep_suite_revisions AS revision ON revision.id = batch.suite_revision_id
            JOIN qep_suites AS suite ON suite.id = revision.suite_id
            WHERE batch.id = $1
              AND suite.project_id = batch.project_id
            FOR UPDATE OF batch
            """,
            batch_id,
        )
        if row is None or (
            authority.batch_id != row["id"]
            or authority.project_id != row["project_id"]
            or authority.suite_revision_id != row["suite_revision_id"]
        ):
            raise AuthorityPermissionDenied
        if authority.write_epoch != row["write_epoch"]:
            raise AuthorityStateConflict(reason="closure_epoch_superseded")
        if authority.source_batch_version != row["version"]:
            raise AuthorityStateConflict(reason="source_binding_superseded")
        intent_rows = await connection.fetch(
            """
            SELECT
                project_id,
                suite_revision_id,
                source_batch_version,
                scope_kind,
                preplan_scope_digest,
                manifest_digest,
                shard_plan_version,
                shard_plan_digest,
                canonical_run_set_digest
            FROM qep_batch_cancellation_intents
            WHERE batch_id = $1
            ORDER BY id
            LIMIT 2
            """,
            batch_id,
        )
        if len(intent_rows) > 1:
            raise AuthorityStateConflict(reason="stored_cancellation_cardinality_invalid")
        if intent_rows:
            intent_row = intent_rows[0]
            scope = BatchCancellationScope(
                kind=BatchCancellationScopeKind(intent_row["scope_kind"]),
                preplan_scope_digest=_optional_digest(intent_row["preplan_scope_digest"]),
                manifest_digest=_optional_digest(intent_row["manifest_digest"]),
                shard_plan_version=intent_row["shard_plan_version"],
                shard_plan_digest=_optional_digest(intent_row["shard_plan_digest"]),
                canonical_run_set_digest=_optional_digest(intent_row["canonical_run_set_digest"]),
            )
            if (
                intent_row["project_id"] != authority.project_id
                or intent_row["suite_revision_id"] != authority.suite_revision_id
                or intent_row["source_batch_version"] + 1 != authority.source_batch_version
                or scope != authority.scope
            ):
                raise AuthorityStateConflict(reason="source_binding_superseded")
        self._closure_authority = authority
        self._locked_batch_id = authority.batch_id
        return authority

    async def require_rejection_authority(
        self,
        *,
        batch_id: str,
        phase_owner_id: str,
        rejection_epoch: int,
    ) -> BatchRejectionAuthority:
        connection = self._require_connection()
        if self._rejection_authority is not None:
            if self._rejection_authority.batch_id != batch_id:
                self._state_error("aggregate_already_locked")
            if phase_owner_id != self._rejection_phase_owner_id:
                raise InternalAuthorityRetired(reason="phase_owner_authority_retired")
            if rejection_epoch != self._rejection_authority.write_epoch:
                raise AuthorityStateConflict(reason="phase_epoch_superseded")
            return self._rejection_authority
        authority = self._candidate_rejection_authority
        checked_at = self._rejection_checked_at
        if authority is None or checked_at is None:
            self._state_error("rejection_authority_not_configured")
        if authority.projection.expires_at <= checked_at:
            raise AuthorityProjectionUnavailable
        if phase_owner_id != self._rejection_phase_owner_id:
            raise InternalAuthorityRetired(reason="phase_owner_authority_retired")
        if rejection_epoch != authority.write_epoch:
            raise AuthorityStateConflict(reason="phase_epoch_superseded")
        row = await connection.fetchrow(
            """
            SELECT
                batch.id,
                batch.project_id,
                batch.suite_revision_id,
                batch.state,
                batch.version,
                batch.write_epoch
            FROM qep_batches AS batch
            JOIN qep_suite_revisions AS revision ON revision.id = batch.suite_revision_id
            JOIN qep_suites AS suite ON suite.id = revision.suite_id
            WHERE batch.id = $1
              AND suite.project_id = batch.project_id
            FOR UPDATE OF batch
            """,
            batch_id,
        )
        if row is None or (
            authority.batch_id != row["id"]
            or authority.project_id != row["project_id"]
            or authority.suite_revision_id != row["suite_revision_id"]
        ):
            raise AuthorityPermissionDenied
        if authority.write_epoch != row["write_epoch"]:
            raise AuthorityStateConflict(reason="phase_epoch_superseded")
        if authority.source_batch_version != row["version"]:
            raise AuthorityStateConflict(reason="source_binding_superseded")
        expected_stage = {
            BatchState.VALIDATING: BatchRejectionStage.VALIDATION,
            BatchState.COLLECTING: BatchRejectionStage.COLLECTION,
            BatchState.PLANNING: BatchRejectionStage.PLANNING,
            BatchState.AWAITING_ADMISSION: BatchRejectionStage.ADMISSION,
        }.get(BatchState(row["state"]))
        if expected_stage is None:
            raise AuthorityStateConflict(reason="rejection_phase_not_current")
        if authority.stage is not expected_stage:
            raise AuthorityStateConflict(reason="source_binding_superseded")
        self._rejection_authority = authority
        self._locked_batch_id = authority.batch_id
        return authority

    async def get_batch_for_update(self, *, batch_id: str) -> Batch:
        connection = self._require_connection()
        self._require_locked_batch(batch_id)
        rows = await connection.fetch(
            """
            SELECT
                batch.id,
                batch.state,
                batch.version,
                intent.id AS cancellation_intent_id,
                intent.project_id AS cancellation_project_id,
                intent.suite_revision_id AS cancellation_suite_revision_id,
                intent.source_batch_version AS cancellation_source_batch_version,
                intent.idempotency_key AS cancellation_idempotency_key,
                intent.source AS cancellation_source,
                intent.actor_id AS cancellation_actor_id,
                intent.reason AS cancellation_reason,
                intent.request_digest AS cancellation_request_digest,
                intent.authorization_digest AS cancellation_authorization_digest,
                intent.scope_kind AS cancellation_scope_kind,
                intent.preplan_scope_digest AS cancellation_preplan_scope_digest,
                intent.manifest_digest AS cancellation_manifest_digest,
                intent.shard_plan_version AS cancellation_shard_plan_version,
                intent.shard_plan_digest AS cancellation_shard_plan_digest,
                intent.canonical_run_set_digest AS cancellation_run_set_digest,
                intent.intent_digest AS cancellation_intent_digest,
                intent.payload AS cancellation_payload,
                intent.recorded_at AS cancellation_recorded_at
            FROM qep_batches AS batch
            LEFT JOIN qep_batch_cancellation_intents AS intent ON intent.batch_id = batch.id
            WHERE batch.id = $1
            ORDER BY intent.id
            LIMIT 2
            """,
            batch_id,
        )
        if len(rows) > 1:
            raise AuthorityStateConflict(reason="stored_cancellation_cardinality_invalid")
        row = rows[0]
        intent = None
        if row["cancellation_intent_id"] is not None:
            scope = BatchCancellationScope(
                kind=BatchCancellationScopeKind(row["cancellation_scope_kind"]),
                preplan_scope_digest=_optional_digest(row["cancellation_preplan_scope_digest"]),
                manifest_digest=_optional_digest(row["cancellation_manifest_digest"]),
                shard_plan_version=row["cancellation_shard_plan_version"],
                shard_plan_digest=_optional_digest(row["cancellation_shard_plan_digest"]),
                canonical_run_set_digest=_optional_digest(row["cancellation_run_set_digest"]),
            )
            intent = BatchCancellationIntent(
                batch_id=row["id"],
                project_id=row["cancellation_project_id"],
                suite_revision_id=row["cancellation_suite_revision_id"],
                source_batch_version=row["cancellation_source_batch_version"],
                idempotency_key=row["cancellation_idempotency_key"],
                source=CancellationSource(row["cancellation_source"]),
                actor_id=row["cancellation_actor_id"],
                reason=row["cancellation_reason"],
                authorization_digest=_digest(row["cancellation_authorization_digest"]),
                scope=scope,
                recorded_at=row["cancellation_recorded_at"],
            )
            if (
                row["cancellation_request_digest"] != _digest_hex(intent.request_digest)
                or row["cancellation_intent_digest"] != _digest_hex(intent.digest)
                or json.loads(row["cancellation_payload"]) != _intent_payload(intent)
            ):
                raise AuthorityStateConflict(reason="stored_cancellation_integrity_invalid")
        rejection = await _read_rejection(connection, batch_id=batch_id)
        basis = await _read_preexecution_basis(connection, batch_id=batch_id)
        self._snapshot = Batch(
            id=row["id"],
            state=BatchState(row["state"]),
            version=row["version"],
            rejection_fact=rejection,
            cancellation_intent=intent,
            preexecution_closure_basis=basis,
        )
        return self._snapshot

    async def publish_cancellation(
        self,
        *,
        batch: Batch,
        intent: BatchCancellationIntent,
    ) -> None:
        try:
            await self._publish_cancellation(batch=batch, intent=intent)
        except BaseException:
            self._aborted = True
            raise

    async def scan_execution_children(self, *, batch_id: str) -> ExecutionChildInventory:
        connection = self._require_connection()
        self._require_locked_batch(batch_id)
        row = await connection.fetchrow(
            """
            SELECT
                ARRAY(
                    SELECT run.id
                    FROM qep_runs AS run
                    WHERE run.batch_id = $1
                    ORDER BY run.id
                ) AS run_ids,
                ARRAY(
                    SELECT assignment.id
                    FROM qep_assignments AS assignment
                    JOIN qep_runs AS run ON run.id = assignment.run_id
                    WHERE run.batch_id = $1
                    ORDER BY assignment.id
                ) AS assignment_ids,
                ARRAY(
                    SELECT attempt.start_commit_key
                    FROM qep_attempts AS attempt
                    JOIN qep_runs AS run ON run.id = attempt.run_id
                    WHERE run.batch_id = $1
                    ORDER BY attempt.start_commit_key
                ) AS start_commit_ids,
                ARRAY(
                    SELECT attempt.id
                    FROM qep_attempts AS attempt
                    JOIN qep_runs AS run ON run.id = attempt.run_id
                    WHERE run.batch_id = $1
                    ORDER BY attempt.id
                ) AS attempt_ids,
                ARRAY(
                    SELECT allocated.fence
                    FROM (
                        SELECT assignment.fence
                        FROM qep_assignments AS assignment
                        JOIN qep_runs AS run ON run.id = assignment.run_id
                        WHERE run.batch_id = $1 AND assignment.fence IS NOT NULL
                        UNION
                        SELECT attempt.fence
                        FROM qep_attempts AS attempt
                        JOIN qep_runs AS run ON run.id = attempt.run_id
                        WHERE run.batch_id = $1
                    ) AS allocated
                    ORDER BY allocated.fence
                ) AS fences,
                ARRAY(
                    SELECT retry.id
                    FROM qep_retry_intents AS retry
                    JOIN qep_runs AS run ON run.id = retry.run_id
                    WHERE run.batch_id = $1
                    ORDER BY retry.id
                ) AS retry_intent_ids
            """,
            batch_id,
        )
        inventory = ExecutionChildInventory(
            run_ids=tuple(row["run_ids"]),
            assignment_ids=tuple(row["assignment_ids"]),
            start_commit_ids=tuple(row["start_commit_ids"]),
            attempt_ids=tuple(row["attempt_ids"]),
            fences=tuple(row["fences"]),
            retry_intent_ids=tuple(row["retry_intent_ids"]),
        )
        self._materialized_run_set_digest = canonical_materialized_run_set_digest(
            batch_id=batch_id,
            run_ids=inventory.run_ids,
        )
        return inventory

    async def quarantine_integrity_failure(self, *, batch_id: str) -> None:
        self._require_locked_batch(batch_id)
        self._aborted = True

    async def read_current_task_ledger_position(self, *, batch_id: str) -> TaskLedgerPosition:
        connection = self._require_connection()
        self._require_locked_batch(batch_id)
        row = await connection.fetchrow(
            """
            SELECT ledger_version, high_watermark
            FROM qep_preexecution_task_ledgers
            WHERE batch_id = $1
            """,
            batch_id,
        )
        if row is None:
            return TaskLedgerPosition(ledger_version=0, high_watermark=0)
        return TaskLedgerPosition(
            ledger_version=row["ledger_version"],
            high_watermark=row["high_watermark"],
        )

    async def read_sealed_task_inventory(self, *, batch_id: str) -> SealedTaskInventory | None:
        connection = self._require_connection()
        self._require_locked_batch(batch_id)
        position = await self.read_current_task_ledger_position(batch_id=batch_id)
        seal_row = await connection.fetchrow(
            """
            SELECT project_id, suite_revision_id, task_count, task_set_digest, issuer_id, sealed_at
            FROM qep_preexecution_task_inventory_seals
            WHERE batch_id = $1 AND ledger_version = $2 AND high_watermark = $3
            """,
            batch_id,
            position.ledger_version,
            position.high_watermark,
        )
        if seal_row is None:
            return None
        task_rows = await connection.fetch(
            """
            SELECT
                phase_ordinal, task_kind, task_key, generation,
                project_id, suite_revision_id, task_issuer_id, started_at
            FROM qep_preexecution_tasks
            WHERE batch_id = $1
            ORDER BY phase_ordinal, task_kind, task_key, generation
            """,
            batch_id,
        )
        tasks = tuple(
            PreexecutionTaskGeneration(
                key=PreexecutionTaskKey(
                    phase_ordinal=task_row["phase_ordinal"],
                    task_kind=task_row["task_kind"],
                    task_key=task_row["task_key"],
                    generation=task_row["generation"],
                ),
                batch_id=batch_id,
                project_id=task_row["project_id"],
                suite_revision_id=task_row["suite_revision_id"],
                issuer_id=task_row["task_issuer_id"],
                started_at=task_row["started_at"],
            )
            for task_row in task_rows
        )
        return SealedTaskInventory(
            batch_id=batch_id,
            project_id=seal_row["project_id"],
            suite_revision_id=seal_row["suite_revision_id"],
            ledger_version=position.ledger_version,
            high_watermark=position.high_watermark,
            task_count=seal_row["task_count"],
            task_set_digest=_digest(seal_row["task_set_digest"]),
            issuer_id=seal_row["issuer_id"],
            sealed_at=seal_row["sealed_at"],
            tasks=tasks,
        )

    async def read_trusted_task_stops(self, *, batch_id: str) -> tuple[TrustedTaskStop, ...]:
        connection = self._require_connection()
        self._require_locked_batch(batch_id)
        rows = await connection.fetch(
            """
            SELECT
                phase_ordinal, task_kind, task_key, generation,
                project_id, stop_issuer_id, stopped_at, stop_fact_digest
            FROM qep_preexecution_tasks
            WHERE batch_id = $1 AND stopped_at IS NOT NULL
            ORDER BY phase_ordinal, task_kind, task_key, generation
            """,
            batch_id,
        )
        return tuple(
            TrustedTaskStop(
                key=PreexecutionTaskKey(
                    phase_ordinal=row["phase_ordinal"],
                    task_kind=row["task_kind"],
                    task_key=row["task_key"],
                    generation=row["generation"],
                ),
                batch_id=batch_id,
                project_id=row["project_id"],
                issuer_id=row["stop_issuer_id"],
                stopped_at=row["stopped_at"],
                digest=_digest(row["stop_fact_digest"]),
            )
            for row in rows
        )

    async def is_trusted_inventory_issuer(self, *, issuer_id: str) -> bool:
        return issuer_id in self._trusted_inventory_issuers

    async def is_trusted_stop_issuer(self, *, issuer_id: str) -> bool:
        return issuer_id in self._trusted_stop_issuers

    async def read_sealed_planned_scope_inventory(
        self, *, batch_id: str
    ) -> SealedPlannedScopeInventory | None:
        connection = self._require_connection()
        self._require_locked_batch(batch_id)
        plan_row = await connection.fetchrow(
            "SELECT version FROM qep_shard_plans WHERE batch_id = $1",
            batch_id,
        )
        if plan_row is None:
            return None
        seal_row = await connection.fetchrow(
            """
            SELECT
                project_id, suite_revision_id, manifest_id, manifest_digest,
                shard_plan_id, shard_plan_digest, item_count, issuer_id, sealed_at
            FROM qep_preexecution_planned_scope_seals
            WHERE batch_id = $1 AND shard_plan_version = $2
            """,
            batch_id,
            plan_row["version"],
        )
        if seal_row is None:
            return None
        item_rows = await connection.fetch(
            """
            SELECT stable_case_id
            FROM qep_manifest_items
            WHERE manifest_id = $1
            ORDER BY item_index
            """,
            seal_row["manifest_id"],
        )
        return SealedPlannedScopeInventory(
            batch_id=batch_id,
            project_id=seal_row["project_id"],
            suite_revision_id=seal_row["suite_revision_id"],
            manifest_id=seal_row["manifest_id"],
            manifest_digest=_digest(seal_row["manifest_digest"]),
            shard_plan_id=seal_row["shard_plan_id"],
            shard_plan_version=plan_row["version"],
            shard_plan_digest=_digest(seal_row["shard_plan_digest"]),
            item_count=seal_row["item_count"],
            manifest_item_keys=tuple(row["stable_case_id"] for row in item_rows),
            issuer_id=seal_row["issuer_id"],
            sealed_at=seal_row["sealed_at"],
        )

    async def is_trusted_planned_scope_issuer(self, *, issuer_id: str) -> bool:
        return issuer_id in self._trusted_planned_scope_issuers

    async def assemble_zero_child_snapshot(
        self, *, inputs: ZeroChildSnapshotInputs
    ) -> BatchPreexecutionSnapshot:
        run_absence_digest = canonical_digest(
            schema_version="qep.preexecution-materialized-run-absence.v1",
            payload={
                "batch_id": inputs.batch_id,
                "source_batch_version": inputs.source_batch_version,
            },
        )
        execution_absence_digest = canonical_digest(
            schema_version="qep.preexecution-execution-absence.v1",
            payload={
                "batch_id": inputs.batch_id,
                "source_batch_version": inputs.source_batch_version,
                "ledger_version": inputs.ledger_version,
                "high_watermark": inputs.high_watermark,
                "task_set_digest": inputs.task_set_digest.value,
                "stop_fact_digests": [digest.value for digest in inputs.stop_fact_digests],
            },
        )
        submission_digest = canonical_digest(
            schema_version="qep.preexecution-submission.v1",
            payload={
                "batch_id": inputs.batch_id,
                "source_batch_version": inputs.source_batch_version,
                "terminal_kind": (
                    inputs.terminal_kind.value if inputs.terminal_kind is not None else None
                ),
                "command_digest": (
                    inputs.command_digest.value if inputs.command_digest is not None else None
                ),
            },
        )
        common = {
            "batch_id": inputs.batch_id,
            "source_batch_version": inputs.source_batch_version,
            "submission_digest": submission_digest,
            "materialized_run_absence_digest": run_absence_digest,
            "execution_absence_snapshot_digest": execution_absence_digest,
            "task_stop_fact_digests": inputs.stop_fact_digests,
        }
        if inputs.planned_inventory is not None:
            assert inputs.scope is not None
            assert inputs.terminal_kind is not None
            assert inputs.command_digest is not None
            inventory = inputs.planned_inventory
            items = tuple(
                BatchPreexecutionScopeItem(
                    batch_id=inputs.batch_id,
                    source_batch_version=inputs.source_batch_version,
                    terminal_kind=inputs.terminal_kind,
                    rejection_fact_digest=(
                        inputs.command_digest
                        if inputs.terminal_kind is BatchPreexecutionTerminalKind.REJECTION
                        else None
                    ),
                    batch_cancellation_intent_digest=(
                        inputs.command_digest
                        if inputs.terminal_kind is BatchPreexecutionTerminalKind.PRESTART_CANCEL
                        else None
                    ),
                    manifest_id=inventory.manifest_id,
                    manifest_digest=inventory.manifest_digest,
                    manifest_item_key=item_key,
                    shard_plan_id=inventory.shard_plan_id,
                    shard_plan_version=inventory.shard_plan_version,
                    shard_plan_digest=inventory.shard_plan_digest,
                    materialized_run_absence_digest=run_absence_digest,
                    resolution="not_started",
                )
                for item_key in sorted(inventory.manifest_item_keys)
            )
            return BatchPreexecutionSnapshot(
                **common,
                scope_kind=BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED,
                preplan_scope_digest=None,
                manifest_digest=inventory.manifest_digest,
                shard_plan_version=inventory.shard_plan_version,
                shard_plan_digest=inventory.shard_plan_digest,
                canonical_run_set_digest=inputs.scope.canonical_run_set_digest,
                scope_items=items,
                item_coverage_proof_digest=canonical_digest(
                    schema_version="qep.preexecution-item-coverage.v1",
                    payload={"item_digests": [item.digest.value for item in items]},
                ),
            )
        preplan_scope_digest = (
            inputs.scope.preplan_scope_digest
            if inputs.scope is not None and inputs.scope.preplan_scope_digest is not None
            else canonical_digest(
                schema_version="qep.preexecution-preplan-scope.v1",
                payload={
                    "batch_id": inputs.batch_id,
                    "source_batch_version": inputs.source_batch_version,
                },
            )
        )
        return BatchPreexecutionSnapshot(
            **common,
            scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
            preplan_scope_digest=preplan_scope_digest,
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
            scope_items=(),
            item_coverage_proof_digest=None,
        )

    async def publish_materialized_handoff(
        self,
        *,
        handoff: BatchMaterializedScopeHandoff,
    ) -> ReplayResult[BatchMaterializedScopeHandoff]:
        try:
            return await self._publish_materialized_handoff(handoff=handoff)
        except BaseException:
            self._aborted = True
            raise

    async def _publish_materialized_handoff(
        self,
        *,
        handoff: BatchMaterializedScopeHandoff,
    ) -> ReplayResult[BatchMaterializedScopeHandoff]:
        connection = self._require_connection()
        self._require_locked_batch(handoff.batch_id)
        closure_authority = self._closure_authority
        rejection_authority = self._rejection_authority
        snapshot = self._snapshot
        if closure_authority is None and rejection_authority is None:
            self._state_error("closure_authority_not_locked")
        if snapshot is None:
            self._state_error("snapshot_not_loaded")
        intent = snapshot.cancellation_intent
        run_set_digest = self._materialized_run_set_digest
        if run_set_digest is None:
            raise AuthorityStateConflict(reason="materialized_handoff_source_missing")
        if closure_authority is not None:
            if intent is None:
                raise AuthorityStateConflict(reason="materialized_handoff_source_missing")
            expected = build_cancel_handoff(
                intent=intent,
                source_batch_version=closure_authority.source_batch_version,
                authoritative_run_set_digest=run_set_digest,
                authority_digest=closure_authority.authority_digest,
                write_epoch=closure_authority.write_epoch,
            )
        else:
            assert rejection_authority is not None
            scope = rejection_authority.scope
            expected = build_rejection_conflict_handoff_from_digest(
                rejection_digest=handoff.command_or_observation_digest,
                batch_id=rejection_authority.batch_id,
                source_batch_version=rejection_authority.source_batch_version,
                project_id=rejection_authority.project_id,
                suite_revision_id=rejection_authority.suite_revision_id,
                preplan_scope_digest=scope.preplan_scope_digest,
                manifest_digest=scope.manifest_digest,
                shard_plan_version=scope.shard_plan_version,
                shard_plan_digest=scope.shard_plan_digest,
                authoritative_run_set_digest=run_set_digest,
                authority_digest=rejection_authority.authority_digest,
                write_epoch=rejection_authority.write_epoch,
            )
        if handoff != expected:
            raise AuthorityStateConflict(reason="materialized_handoff_authority_superseded")
        row = await connection.fetchrow(
            """
            SELECT
                id, schema_version, batch_id, trigger_kind, trigger_digest,
                source_batch_version, materialized_run_set_digest, handoff_digest,
                event_id, payload
            FROM qep_materialized_scope_handoffs
            WHERE schema_version = $1
              AND batch_id = $2
              AND trigger_kind = $3
              AND trigger_digest = $4
            """,
            handoff.schema_version,
            handoff.batch_id,
            handoff.trigger_kind.value,
            _digest_hex(handoff.command_or_observation_digest),
        )
        if row is not None:
            if _stored_handoff_matches(row, handoff=handoff):
                return ReplayResult(value=handoff, replayed=True)
            stored_payload = _decode_handoff_payload(row["payload"])
            stored_handoff = _rebuild_stored_handoff(
                payload=stored_payload,
                intent=intent,
            )
            if not _stored_handoff_matches(row, handoff=stored_handoff):
                raise AuthorityStateConflict(
                    reason="stored_materialized_handoff_integrity_invalid"
                )
            raise IdempotencyConflict(
                scope=f"batch:{handoff.batch_id}:materialized_handoff",
                key=handoff.semantic_trigger_key.value,
                stored_digest=stored_handoff.handoff_digest,
                received_digest=handoff.handoff_digest,
            )
        await self._insert_handoff(connection, handoff)
        await self._insert_handoff_audit(connection, handoff)
        await self._insert_handoff_outbox(connection, handoff)
        return ReplayResult(value=handoff, replayed=False)

    async def publish_preexecution_closure(self, *, batch: Batch) -> None:
        try:
            await self._publish_preexecution_closure(batch=batch)
        except BaseException:
            self._aborted = True
            raise

    async def _publish_preexecution_closure(self, *, batch: Batch) -> None:
        connection = self._require_connection()
        self._require_locked_batch(batch.id)
        if self._closure_authority is None:
            self._state_error("closure_authority_not_locked")
        snapshot = self._snapshot
        if snapshot is None:
            self._state_error("snapshot_not_loaded")
        basis = batch.preexecution_closure_basis
        if (
            basis is None
            or basis.terminal_kind is not BatchPreexecutionTerminalKind.PRESTART_CANCEL
        ):
            self._state_error("preexecution_closure_basis_not_transitioned")

        status = await connection.execute(
            """
            UPDATE qep_batches
            SET state = 'cancelled',
                version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $1
              AND state = $2
              AND version = $3
            """,
            batch.id,
            snapshot.state.value,
            snapshot.version,
        )
        if status != "UPDATE 1":
            raise VersionConflict(
                entity_type="batch",
                entity_id=batch.id,
                current_version=snapshot.version,
                expected_version=snapshot.version,
            )
        await self._insert_preexecution_basis(connection, basis)
        await self._insert_preexecution_terminal_audit(
            connection,
            basis,
            actor_id=cast(str, self._closure_reconciler_id),
            action="reconcile_preexecution_cancellation",
            reason_code="batch_preexecution_closure_committed",
        )
        await self._insert_preexecution_terminal_outbox(
            connection,
            basis,
            event_type="batch.cancelled.v1",
            event_prefix="batch-cancelled",
        )

    async def publish_preexecution_rejection(
        self, *, batch: Batch, rejection: BatchRejection
    ) -> None:
        try:
            await self._publish_preexecution_rejection(batch=batch, rejection=rejection)
        except BaseException:
            self._aborted = True
            raise

    async def _publish_preexecution_rejection(
        self, *, batch: Batch, rejection: BatchRejection
    ) -> None:
        connection = self._require_connection()
        self._require_locked_batch(batch.id)
        if self._rejection_authority is None:
            self._state_error("rejection_authority_not_locked")
        snapshot = self._snapshot
        if snapshot is None:
            self._state_error("snapshot_not_loaded")
        basis = batch.preexecution_closure_basis
        if (
            basis is None
            or basis.terminal_kind is not BatchPreexecutionTerminalKind.REJECTION
            or batch.rejection_fact != rejection
        ):
            self._state_error("preexecution_rejection_basis_not_transitioned")

        status = await connection.execute(
            """
            UPDATE qep_batches
            SET state = 'rejected',
                version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $1
              AND state = $2
              AND version = $3
            """,
            batch.id,
            snapshot.state.value,
            snapshot.version,
        )
        if status != "UPDATE 1":
            raise VersionConflict(
                entity_type="batch",
                entity_id=batch.id,
                current_version=snapshot.version,
                expected_version=snapshot.version,
            )
        await self._insert_rejection(connection, rejection)
        await self._insert_preexecution_basis(connection, basis)
        await self._insert_preexecution_terminal_audit(
            connection,
            basis,
            actor_id=cast(str, self._rejection_phase_owner_id),
            action="record_preexecution_rejection",
            reason_code="batch_preexecution_rejection_committed",
        )
        await self._insert_preexecution_terminal_outbox(
            connection,
            basis,
            event_type="batch.rejected.v1",
            event_prefix="batch-rejected",
        )

    @staticmethod
    async def _insert_rejection(
        connection: asyncpg.Connection,
        rejection: BatchRejection,
    ) -> None:
        status = await connection.execute(
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
            _optional_digest_hex(rejection.authority_digest),
            _digest_hex(rejection.digest),
            _json(_rejection_payload(rejection)),
            rejection.recorded_at,
        )
        _require_inserted(status, reason="batch_rejection_write_missing")

    @staticmethod
    async def _insert_preexecution_basis(
        connection: asyncpg.Connection,
        basis: BatchPreexecutionClosureBasis,
    ) -> None:
        command_digest = (
            basis.rejection_fact_digest
            if basis.rejection_fact_digest is not None
            else basis.batch_cancellation_intent_digest
        )
        status = await connection.execute(
            """
            INSERT INTO qep_batch_preexecution_closure_bases (
                id, batch_id, source_batch_version, source_phase, terminal_kind,
                command_digest, scope_kind, materialized_run_absence_digest,
                execution_absence_snapshot_digest, basis_digest, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, transaction_timestamp())
            """,
            f"batch-preexecution-closure-basis-{_digest_hex(basis.digest)}",
            basis.batch_id,
            basis.source_batch_version,
            basis.source_phase.value,
            basis.terminal_kind.value,
            _digest_hex(command_digest),
            basis.scope_kind.value,
            _digest_hex(basis.materialized_run_absence_digest),
            _digest_hex(basis.execution_absence_snapshot_digest),
            _digest_hex(basis.digest),
            _json(_basis_payload(basis)),
        )
        _require_inserted(status, reason="batch_preexecution_closure_basis_write_missing")

    @staticmethod
    async def _insert_preexecution_terminal_audit(
        connection: asyncpg.Connection,
        basis: BatchPreexecutionClosureBasis,
        *,
        actor_id: str,
        action: str,
        reason_code: str,
    ) -> None:
        status = await connection.execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES (
                $1, $2, $3, 'batch', $4, 'allowed', $5, NULL, $6, $7,
                transaction_timestamp()
            )
            """,
            f"batch-preexecution-terminal-audit-{_digest_hex(basis.digest)}",
            actor_id,
            action,
            basis.batch_id,
            reason_code,
            _digest_hex(basis.digest),
            _json(
                {
                    "schema_version": "qep.batch-preexecution-terminal-audit.v1",
                    "batch_id": basis.batch_id,
                    "basis_digest": basis.digest.value,
                    "terminal_kind": basis.terminal_kind.value,
                    "batch_outcome": basis.batch_outcome.value,
                }
            ),
        )
        _require_inserted(status, reason="batch_preexecution_terminal_audit_write_missing")

    @staticmethod
    async def _insert_preexecution_terminal_outbox(
        connection: asyncpg.Connection,
        basis: BatchPreexecutionClosureBasis,
        *,
        event_type: str,
        event_prefix: str,
    ) -> None:
        payload = {
            "schema_version": event_type,
            "batch_id": basis.batch_id,
            "basis_digest": basis.digest.value,
            "terminal_kind": basis.terminal_kind.value,
        }
        payload_digest = canonical_digest(schema_version=event_type, payload=payload)
        event_id = f"{event_prefix}-{_digest_hex(basis.digest)}"
        status = await connection.execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, status, available_at, attempts, created_at
            ) VALUES (
                $1, $1, 'batch', $2, $3, $4, $5,
                'pending', transaction_timestamp(), 0, transaction_timestamp()
            )
            """,
            event_id,
            basis.batch_id,
            event_type,
            _digest_hex(payload_digest),
            _json(payload),
        )
        _require_inserted(status, reason="batch_preexecution_terminal_outbox_write_missing")

    async def _publish_cancellation(
        self,
        *,
        batch: Batch,
        intent: BatchCancellationIntent,
    ) -> None:
        connection = self._require_connection()
        self._require_locked_batch(batch.id)
        authority = self._authority
        if authority is None:
            self._state_error("cancel_authority_not_locked")
        snapshot = self._snapshot
        if snapshot is None:
            self._state_error("snapshot_not_loaded")
        expected = snapshot.request_cancel(intent=intent, expected_version=snapshot.version)
        if batch != expected or (
            intent.batch_id != authority.batch_id
            or intent.project_id != authority.project_id
            or intent.suite_revision_id != authority.suite_revision_id
            or intent.actor_id != authority.actor_id
            or intent.source is not authority.source
            or intent.authorization_digest != authority.authorization_digest
            or intent.scope != authority.scope
        ):
            raise AuthorityStateConflict(reason="cancel_publication_authority_superseded")

        status = await connection.execute(
            """
            UPDATE qep_batches
            SET version = $1,
                updated_at = transaction_timestamp()
            WHERE id = $2
              AND state = $3
              AND version = $4
            """,
            batch.version,
            batch.id,
            snapshot.state.value,
            snapshot.version,
        )
        if status != "UPDATE 1":
            raise VersionConflict(
                entity_type="batch",
                entity_id=batch.id,
                current_version=snapshot.version,
                expected_version=snapshot.version,
            )
        await self._insert_intent(connection, intent)
        await self._insert_audit(connection, intent)
        await self._insert_outbox(connection, intent)

    @staticmethod
    async def _insert_intent(
        connection: asyncpg.Connection,
        intent: BatchCancellationIntent,
    ) -> None:
        scope = intent.scope
        status = await connection.execute(
            """
            INSERT INTO qep_batch_cancellation_intents (
                id, batch_id, project_id, suite_revision_id, source_batch_version,
                idempotency_key, source, actor_id, reason, request_digest,
                authorization_digest, scope_kind, preplan_scope_digest, manifest_digest,
                shard_plan_version, shard_plan_digest, canonical_run_set_digest,
                intent_digest, payload, recorded_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19, $20
            )
            """,
            f"batch-cancellation-intent-{_digest_hex(intent.digest)}",
            intent.batch_id,
            intent.project_id,
            intent.suite_revision_id,
            intent.source_batch_version,
            intent.idempotency_key,
            intent.source.value,
            intent.actor_id,
            intent.reason,
            _digest_hex(intent.request_digest),
            _digest_hex(intent.authorization_digest),
            scope.kind.value,
            _optional_digest_hex(scope.preplan_scope_digest),
            _optional_digest_hex(scope.manifest_digest),
            scope.shard_plan_version,
            _optional_digest_hex(scope.shard_plan_digest),
            _optional_digest_hex(scope.canonical_run_set_digest),
            _digest_hex(intent.digest),
            _json(_intent_payload(intent)),
            intent.recorded_at,
        )
        _require_inserted(status, reason="batch_cancellation_intent_write_missing")

    @staticmethod
    async def _insert_audit(
        connection: asyncpg.Connection,
        intent: BatchCancellationIntent,
    ) -> None:
        status = await connection.execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES (
                $1, $2, 'request_batch_cancellation', 'batch', $3, 'allowed',
                'batch_cancellation_intent_committed', NULL, $4, $5,
                transaction_timestamp()
            )
            """,
            f"batch-cancellation-audit-{_digest_hex(intent.digest)}",
            intent.actor_id,
            intent.batch_id,
            _digest_hex(intent.digest),
            _json(
                {
                    "schema_version": "qep.batch-cancellation-audit.v1",
                    "batch_id": intent.batch_id,
                    "intent_digest": intent.digest.value,
                    "authorization_digest": intent.authorization_digest.value,
                    "source_batch_version": intent.source_batch_version,
                }
            ),
        )
        _require_inserted(status, reason="batch_cancellation_audit_write_missing")

    @staticmethod
    async def _insert_outbox(
        connection: asyncpg.Connection,
        intent: BatchCancellationIntent,
    ) -> None:
        payload = {
            "schema_version": "qep.batch-cancel-requested.v1",
            "batch_id": intent.batch_id,
            "source_batch_version": intent.source_batch_version,
            "intent_digest": intent.digest.value,
            "request_digest": intent.request_digest.value,
        }
        payload_digest = canonical_digest(
            schema_version="qep.batch-cancel-requested.v1",
            payload=payload,
        )
        event_id = f"batch-cancel-requested-{_digest_hex(intent.digest)}"
        status = await connection.execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, status, available_at, attempts, created_at
            ) VALUES (
                $1, $1, 'batch', $2, 'batch.cancel-requested.v1', $3, $4,
                'pending', transaction_timestamp(), 0, transaction_timestamp()
            )
            """,
            event_id,
            intent.batch_id,
            _digest_hex(payload_digest),
            _json(payload),
        )
        _require_inserted(status, reason="batch_cancellation_outbox_write_missing")

    @staticmethod
    async def _insert_handoff(
        connection: asyncpg.Connection,
        handoff: BatchMaterializedScopeHandoff,
    ) -> None:
        status = await connection.execute(
            """
            INSERT INTO qep_materialized_scope_handoffs (
                id, schema_version, batch_id, trigger_kind, trigger_digest,
                source_batch_version, materialized_run_set_digest, handoff_digest,
                event_id, payload, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                transaction_timestamp()
            )
            """,
            handoff.handoff_id,
            handoff.schema_version,
            handoff.batch_id,
            handoff.trigger_kind.value,
            _digest_hex(handoff.command_or_observation_digest),
            handoff.source_batch_version,
            _digest_hex(handoff.authoritative_run_set_digest),
            _digest_hex(handoff.handoff_digest),
            handoff.event_id,
            _json(_handoff_payload(handoff)),
        )
        _require_inserted(status, reason="batch_materialized_handoff_write_missing")

    async def _insert_handoff_audit(
        self,
        connection: asyncpg.Connection,
        handoff: BatchMaterializedScopeHandoff,
    ) -> None:
        actor_id = cast(str, self._closure_reconciler_id or self._rejection_phase_owner_id)
        reason_code = (
            "batch_cancellation_materialized_scope_handoff_committed"
            if self._closure_authority is not None
            else "batch_rejection_materialized_scope_handoff_committed"
        )
        status = await connection.execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES (
                $1, $2, 'publish_batch_materialized_handoff', 'batch', $3, 'allowed',
                $4, $5, $6, $7,
                transaction_timestamp()
            )
            """,
            f"batch-materialized-handoff-audit-{_digest_hex(handoff.handoff_digest)}",
            actor_id,
            handoff.batch_id,
            reason_code,
            _digest_hex(handoff.command_or_observation_digest),
            _digest_hex(handoff.handoff_digest),
            _json(
                {
                    "schema_version": "qep.batch-materialized-scope-handoff-audit.v1",
                    "batch_id": handoff.batch_id,
                    "handoff_id": handoff.handoff_id,
                    "handoff_digest": handoff.handoff_digest.value,
                    "authority_digest": handoff.authority_digest.value,
                    "write_epoch": handoff.write_epoch,
                }
            ),
        )
        _require_inserted(status, reason="batch_materialized_handoff_audit_write_missing")

    @staticmethod
    async def _insert_handoff_outbox(
        connection: asyncpg.Connection,
        handoff: BatchMaterializedScopeHandoff,
    ) -> None:
        payload = {"handoff_digest": handoff.handoff_digest.value}
        status = await connection.execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, status, available_at, attempts, created_at
            ) VALUES (
                $1, $1, 'batch', $2, 'batch.materialized-scope-handoff.v1', $3, $4,
                'pending', transaction_timestamp(), 0, transaction_timestamp()
            )
            """,
            handoff.event_id,
            handoff.batch_id,
            _digest_hex(handoff.payload_digest),
            _json(payload),
        )
        _require_inserted(status, reason="batch_materialized_handoff_outbox_write_missing")

    def _require_connection(self) -> asyncpg.Connection:
        if self._aborted:
            self._state_error("aborted")
        if self._connection is None:
            self._state_error("not_active" if not self._closed else "closed")
        return self._connection

    def _require_locked_batch(self, batch_id: str) -> None:
        if self._locked_batch_id != batch_id:
            self._state_error("authority_not_locked")

    @staticmethod
    def _state_error(reason: str) -> None:
        raise PortContractError(resource="unit_of_work", field="state", reason=reason)


async def _read_rejection(
    connection: asyncpg.Connection,
    *,
    batch_id: str,
) -> BatchRejection | None:
    rows = await connection.fetch(
        """
        SELECT
            id, batch_id, source_batch_version, stage, reason_class, reason_code,
            input_digest, authority_digest, rejection_digest, payload, recorded_at
        FROM qep_batch_rejections
        WHERE batch_id = $1
        ORDER BY id
        LIMIT 2
        """,
        batch_id,
    )
    if len(rows) > 1:
        raise AuthorityStateConflict(reason="stored_rejection_cardinality_invalid")
    if not rows:
        return None
    row = rows[0]
    rejection = BatchRejection(
        rejection_id=row["id"],
        batch_id=row["batch_id"],
        source_batch_version=row["source_batch_version"],
        stage=BatchRejectionStage(row["stage"]),
        reason_class=BatchRejectionReasonClass(row["reason_class"]),
        reason_code=row["reason_code"],
        input_digest=_digest(row["input_digest"]),
        authority_digest=_optional_digest(row["authority_digest"]),
        recorded_at=row["recorded_at"],
    )
    if row["rejection_digest"] != _digest_hex(rejection.digest) or json.loads(
        row["payload"]
    ) != _rejection_payload(rejection):
        raise AuthorityStateConflict(reason="stored_rejection_integrity_invalid")
    return rejection


async def _read_preexecution_basis(
    connection: asyncpg.Connection,
    *,
    batch_id: str,
) -> BatchPreexecutionClosureBasis | None:
    row = await connection.fetchrow(
        """
        SELECT
            batch_id, source_batch_version, source_phase, terminal_kind, command_digest,
            scope_kind, materialized_run_absence_digest,
            execution_absence_snapshot_digest, basis_digest, payload
        FROM qep_batch_preexecution_closure_bases
        WHERE batch_id = $1
        """,
        batch_id,
    )
    if row is None:
        return None
    payload = json.loads(row["payload"])
    basis = BatchPreexecutionClosureBasis(
        batch_id=payload["batch_id"],
        source_batch_version=payload["source_batch_version"],
        source_phase=BatchState(payload["source_phase"]),
        terminal_kind=BatchPreexecutionTerminalKind(payload["terminal_kind"]),
        rejection_fact_digest=_optional_digest_from_value(payload["rejection_fact_digest"]),
        batch_cancellation_intent_digest=_optional_digest_from_value(
            payload["batch_cancellation_intent_digest"]
        ),
        scope_kind=BatchPreexecutionScopeKind(payload["scope_kind"]),
        submission_digest=_digest_value(payload["submission_digest"]),
        preplan_scope_digest=_optional_digest_from_value(payload["preplan_scope_digest"]),
        manifest_digest=_optional_digest_from_value(payload["manifest_digest"]),
        shard_plan_version=payload["shard_plan_version"],
        shard_plan_digest=_optional_digest_from_value(payload["shard_plan_digest"]),
        canonical_run_set_digest=_optional_digest_from_value(payload["canonical_run_set_digest"]),
        materialized_run_absence_digest=_digest_value(payload["materialized_run_absence_digest"]),
        execution_absence_snapshot_digest=_digest_value(
            payload["execution_absence_snapshot_digest"]
        ),
        task_stop_fact_digests=tuple(
            _digest_value(value) for value in payload["task_stop_fact_digest"]
        ),
        preexecution_scope_item_fact_digests=tuple(
            _digest_value(value) for value in payload["preexecution_scope_item_fact_digest"]
        ),
        item_coverage_proof_digest=_optional_digest_from_value(
            payload["item_coverage_proof_digest"]
        ),
        batch_outcome=BatchState(payload["batch_outcome"]),
    )
    if (
        row["batch_id"] != basis.batch_id
        or row["source_batch_version"] != basis.source_batch_version
        or row["source_phase"] != basis.source_phase.value
        or row["terminal_kind"] != basis.terminal_kind.value
        or row["command_digest"]
        != _digest_hex(
            basis.rejection_fact_digest
            if basis.rejection_fact_digest is not None
            else basis.batch_cancellation_intent_digest
        )
        or row["scope_kind"] != basis.scope_kind.value
        or row["materialized_run_absence_digest"]
        != _digest_hex(basis.materialized_run_absence_digest)
        or row["execution_absence_snapshot_digest"]
        != _digest_hex(basis.execution_absence_snapshot_digest)
        or row["basis_digest"] != _digest_hex(basis.digest)
        or payload != _basis_payload(basis)
    ):
        raise AuthorityStateConflict(reason="stored_preexecution_basis_integrity_invalid")
    return basis


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
        "authority_digest": _optional_digest_value(rejection.authority_digest),
        "recorded_at": rejection.recorded_at.isoformat().replace("+00:00", "Z"),
        "rejection_digest": rejection.digest.value,
    }


def _basis_payload(basis: BatchPreexecutionClosureBasis) -> dict[str, object]:
    return {
        "schema_version": "qep.batch-preexecution-closure-basis.v1",
        "batch_id": basis.batch_id,
        "source_batch_version": basis.source_batch_version,
        "source_phase": basis.source_phase.value,
        "terminal_kind": basis.terminal_kind.value,
        "rejection_fact_digest": _optional_digest_value(basis.rejection_fact_digest),
        "batch_cancellation_intent_digest": _optional_digest_value(
            basis.batch_cancellation_intent_digest
        ),
        "scope_kind": basis.scope_kind.value,
        "submission_digest": basis.submission_digest.value,
        "preplan_scope_digest": _optional_digest_value(basis.preplan_scope_digest),
        "manifest_digest": _optional_digest_value(basis.manifest_digest),
        "shard_plan_version": basis.shard_plan_version,
        "shard_plan_digest": _optional_digest_value(basis.shard_plan_digest),
        "canonical_run_set_digest": _optional_digest_value(basis.canonical_run_set_digest),
        "materialized_run_absence_digest": basis.materialized_run_absence_digest.value,
        "execution_absence_snapshot_digest": basis.execution_absence_snapshot_digest.value,
        "task_stop_fact_digest": [value.value for value in basis.task_stop_fact_digests],
        "preexecution_scope_item_fact_digest": [
            value.value for value in basis.preexecution_scope_item_fact_digests
        ],
        "item_coverage_proof_digest": _optional_digest_value(basis.item_coverage_proof_digest),
        "batch_outcome": basis.batch_outcome.value,
        "basis_digest": basis.digest.value,
    }


def _intent_payload(intent: BatchCancellationIntent) -> dict[str, object]:
    scope = intent.scope
    return {
        "schema_version": "qep.batch-cancellation-intent.v1",
        "batch_id": intent.batch_id,
        "project_id": intent.project_id,
        "suite_revision_id": intent.suite_revision_id,
        "source_batch_version": intent.source_batch_version,
        "idempotency_key": intent.idempotency_key,
        "source": intent.source.value,
        "actor_id": intent.actor_id,
        "reason": intent.reason,
        "request_digest": intent.request_digest.value,
        "authorization_digest": intent.authorization_digest.value,
        "scope_kind": scope.kind.value,
        "preplan_scope_digest": _optional_digest_value(scope.preplan_scope_digest),
        "manifest_digest": _optional_digest_value(scope.manifest_digest),
        "shard_plan_version": scope.shard_plan_version,
        "shard_plan_digest": _optional_digest_value(scope.shard_plan_digest),
        "canonical_run_set_digest": _optional_digest_value(scope.canonical_run_set_digest),
        "recorded_at": intent.recorded_at.isoformat().replace("+00:00", "Z"),
        "intent_digest": intent.digest.value,
    }


def _handoff_payload(handoff: BatchMaterializedScopeHandoff) -> dict[str, object]:
    return {
        "schema_version": handoff.schema_version,
        "identity_algorithm_version": handoff.identity_algorithm_version,
        "semantic_trigger_key": handoff.semantic_trigger_key.value,
        "handoff_id": handoff.handoff_id,
        "handoff_digest": handoff.handoff_digest.value,
        "trigger_kind": handoff.trigger_kind.value,
        "command_or_observation_digest": handoff.command_or_observation_digest.value,
        "batch_id": handoff.batch_id,
        "source_batch_version": handoff.source_batch_version,
        "project_id": handoff.project_id,
        "suite_revision_id": handoff.suite_revision_id,
        "preplan_scope_digest": _optional_digest_value(handoff.preplan_scope_digest),
        "manifest_digest": _optional_digest_value(handoff.manifest_digest),
        "shard_plan_version": handoff.shard_plan_version,
        "shard_plan_digest": _optional_digest_value(handoff.shard_plan_digest),
        "authoritative_run_set_digest": handoff.authoritative_run_set_digest.value,
        "authority_digest": handoff.authority_digest.value,
        "write_epoch": handoff.write_epoch,
        "destination": handoff.destination,
        "event_id": handoff.event_id,
        "payload_digest": handoff.payload_digest.value,
    }


def _stored_handoff_matches(
    row: asyncpg.Record,
    *,
    handoff: BatchMaterializedScopeHandoff,
) -> bool:
    return (
        row["id"] == handoff.handoff_id
        and row["schema_version"] == handoff.schema_version
        and row["batch_id"] == handoff.batch_id
        and row["trigger_kind"] == handoff.trigger_kind.value
        and row["trigger_digest"] == _digest_hex(handoff.command_or_observation_digest)
        and row["source_batch_version"] == handoff.source_batch_version
        and row["materialized_run_set_digest"] == _digest_hex(handoff.authoritative_run_set_digest)
        and row["handoff_digest"] == _digest_hex(handoff.handoff_digest)
        and row["event_id"] == handoff.event_id
        and _decode_handoff_payload(row["payload"]) == _handoff_payload(handoff)
    )


def _decode_handoff_payload(value: str) -> dict[str, object]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise AuthorityStateConflict(reason="stored_materialized_handoff_integrity_invalid")
    return decoded


def _rebuild_stored_handoff(
    *,
    payload: dict[str, object],
    intent: BatchCancellationIntent | None,
) -> BatchMaterializedScopeHandoff:
    try:
        trigger_kind = payload["trigger_kind"]
        source_batch_version = payload["source_batch_version"]
        write_epoch = payload["write_epoch"]
        shard_plan_version = payload["shard_plan_version"]
        if (
            isinstance(source_batch_version, bool)
            or not isinstance(source_batch_version, int)
            or source_batch_version < 0
            or isinstance(write_epoch, bool)
            or not isinstance(write_epoch, int)
            or write_epoch < 0
            or (
                shard_plan_version is not None
                and (
                    isinstance(shard_plan_version, bool)
                    or not isinstance(shard_plan_version, int)
                    or shard_plan_version < 0
                )
            )
        ):
            raise ValueError("invalid stored handoff version")
        if trigger_kind == "cancel_intent":
            if intent is None:
                raise ValueError("stored cancellation intent missing")
            rebuilt = build_cancel_handoff(
                intent=intent,
                source_batch_version=source_batch_version,
                authoritative_run_set_digest=Digest(payload["authoritative_run_set_digest"]),
                authority_digest=Digest(payload["authority_digest"]),
                write_epoch=write_epoch,
            )
        elif trigger_kind == "rejection_conflict":
            text_values = (
                payload["batch_id"],
                payload["project_id"],
                payload["suite_revision_id"],
            )
            if any(not isinstance(value, str) for value in text_values):
                raise ValueError("invalid stored handoff ownership")
            batch_id, project_id, suite_revision_id = text_values
            rebuilt = build_rejection_conflict_handoff_from_digest(
                rejection_digest=Digest(payload["command_or_observation_digest"]),
                batch_id=batch_id,
                source_batch_version=source_batch_version,
                project_id=project_id,
                suite_revision_id=suite_revision_id,
                preplan_scope_digest=_optional_digest_from_value(payload["preplan_scope_digest"]),
                manifest_digest=_optional_digest_from_value(payload["manifest_digest"]),
                shard_plan_version=shard_plan_version,
                shard_plan_digest=_optional_digest_from_value(payload["shard_plan_digest"]),
                authoritative_run_set_digest=Digest(payload["authoritative_run_set_digest"]),
                authority_digest=Digest(payload["authority_digest"]),
                write_epoch=write_epoch,
            )
        else:
            raise ValueError("invalid stored handoff trigger")
    except (KeyError, TypeError, ValueError) as error:
        raise AuthorityStateConflict(
            reason="stored_materialized_handoff_integrity_invalid"
        ) from error
    if _handoff_payload(rebuilt) != payload:
        raise AuthorityStateConflict(reason="stored_materialized_handoff_integrity_invalid")
    return rebuilt


def _optional_digest_value(value: Digest | None) -> str | None:
    return None if value is None else value.value


def _optional_digest_hex(value: Digest | None) -> str | None:
    return None if value is None else _digest_hex(value)


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _digest(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _optional_digest(value: str | None) -> Digest | None:
    return None if value is None else _digest(value)


def _digest_value(value: object) -> Digest:
    if not isinstance(value, str):
        raise AuthorityStateConflict(reason="stored_digest_value_invalid")
    return Digest(value)


def _optional_digest_from_value(value: object) -> Digest | None:
    return None if value is None else _digest_value(value)


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _require_inserted(status: str, *, reason: str) -> None:
    if status != "INSERT 0 1":
        raise AuthorityStateConflict(reason=reason)
