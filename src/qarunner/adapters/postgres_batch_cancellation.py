"""PostgreSQL Batch-local unit of work for cancellation intent."""

from __future__ import annotations

import json
from types import TracebackType
from typing import Self

import asyncpg

from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
    BatchCancellationAuthority,
)
from qarunner.application.ports.common import PortContractError
from qarunner.domain.batch import (
    Batch,
    BatchPreexecutionClosureBasis,
    BatchPreexecutionScopeKind,
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
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import VersionConflict


class PostgresBatchCancellationUnitOfWork:
    """One-shot transaction for the RequestBatchCancellation application slice."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        authority: BatchCancellationAuthority,
    ) -> None:
        self._pool = pool
        self._candidate_authority = authority
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._authority: BatchCancellationAuthority | None = None
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

    async def _publish_cancellation(
        self,
        *,
        batch: Batch,
        intent: BatchCancellationIntent,
    ) -> None:
        connection = self._require_connection()
        authority = self._require_locked_batch(batch.id)
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

    def _require_connection(self) -> asyncpg.Connection:
        if self._aborted:
            self._state_error("aborted")
        if self._connection is None:
            self._state_error("not_active" if not self._closed else "closed")
        return self._connection

    def _require_locked_batch(self, batch_id: str) -> BatchCancellationAuthority:
        if self._authority is None or self._authority.batch_id != batch_id:
            self._state_error("authority_not_locked")
        return self._authority

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
