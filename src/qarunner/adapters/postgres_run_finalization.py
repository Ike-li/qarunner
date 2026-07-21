"""PostgreSQL Run-local unit of work for immutable finalization."""

from __future__ import annotations

import json
from types import TracebackType
from typing import Self

import asyncpg

from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityStateConflict,
)
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.run_finalization import (
    RUN_FINALIZATION_BASIS_SCHEMA,
    FinalizeRunAuthority,
    RunFinalizationIdentityScope,
    RunFinalizationMutationSnapshot,
    RunFinalizationProjection,
    RunFinalizationPublication,
)
from qarunner.application.run_closed_handoff import RunClosedHandoff
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import IdempotencyConflict, VersionConflict
from qarunner.domain.run_finalization import (
    AttemptExecutionFact,
    RunDisposition,
    RunFinalizationState,
    RunOutcome,
    RunPhase,
)


class PostgresRunFinalizationUnitOfWork:
    """One-shot caller-owned transaction implementing RunFinalizationGateway."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._authority: FinalizeRunAuthority | None = None
        self._snapshot: RunFinalizationMutationSnapshot | None = None
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

    async def require_finalization_authority(self, *, run_id: str) -> FinalizeRunAuthority:
        connection = self._require_connection()
        if self._authority is not None:
            if self._authority.run_id != run_id:
                self._state_error("aggregate_already_locked")
            return self._authority
        row = await connection.fetchrow(
            """
            SELECT
                run.id,
                run.batch_id,
                run.version,
                run.current_fence,
                run.orchestration_phase,
                batch.write_epoch
            FROM qep_runs AS run
            JOIN qep_batches AS batch ON batch.id = run.batch_id
            WHERE run.id = $1
            FOR UPDATE OF run
            """,
            run_id,
        )
        if row is None:
            raise AuthorityPermissionDenied
        if row["write_epoch"] < 1:
            raise AuthorityStateConflict(reason="run_finalization_authority_missing")
        # A waiter needs a fresh READ COMMITTED snapshot after acquiring the Run lock so it can see
        # the winner's basis instead of retaining the pre-wait LEFT JOIN result.
        terminal_source_version = await connection.fetchval(
            "SELECT source_run_version FROM qep_run_finalization_bases WHERE run_id = $1",
            run_id,
        )
        if (row["orchestration_phase"] == RunPhase.CLOSED.value) != (
            terminal_source_version is not None
        ):
            raise AuthorityStateConflict(reason="run_finalization_terminal_binding_invalid")
        source_run_version = (
            row["version"] if terminal_source_version is None else terminal_source_version
        )
        authority_digest = canonical_digest(
            schema_version="qep.run-finalization-authority.v1",
            payload={
                "run_id": row["id"],
                "batch_id": row["batch_id"],
                "source_run_version": source_run_version,
                "current_fence": row["current_fence"],
                "write_epoch": row["write_epoch"],
            },
        )
        self._authority = FinalizeRunAuthority(
            run_id=row["id"],
            batch_id=row["batch_id"],
            source_run_version=source_run_version,
            authority_digest=authority_digest,
            write_epoch=row["write_epoch"],
        )
        return self._authority

    async def lookup_stored(
        self, *, identity_scope: RunFinalizationIdentityScope
    ) -> RunFinalizationProjection | None:
        connection = self._require_connection()
        schema_version, run_id, source_run_version = identity_scope
        if schema_version != RUN_FINALIZATION_BASIS_SCHEMA:
            raise PortContractError(
                resource="run_finalization_identity",
                field="schema_version",
                reason="unsupported",
            )
        self._require_locked_run(run_id)
        row = await connection.fetchrow(
            """
            SELECT
                run.orchestration_phase,
                run.disposition,
                run.outcome,
                basis.basis_digest,
                attempt.state AS attempt_state
            FROM qep_run_finalization_bases AS basis
            JOIN qep_runs AS run ON run.id = basis.run_id
            LEFT JOIN qep_attempts AS attempt ON attempt.id = basis.final_attempt_id
            WHERE basis.run_id = $1
              AND basis.source_run_version = $2
            """,
            run_id,
            source_run_version,
        )
        if row is None:
            return None
        latest_attempt_fact = (
            None if row["attempt_state"] is None else AttemptExecutionFact(row["attempt_state"])
        )
        state = RunFinalizationState(
            phase=RunPhase(row["orchestration_phase"]),
            disposition=RunDisposition(row["disposition"]),
            outcome=RunOutcome(row["outcome"]),
            finalization_basis_digest=_digest(row["basis_digest"]),
            latest_attempt_fact=latest_attempt_fact,
            current_assignment_id=None,
            pending_retry_intent_id=None,
        )
        return RunFinalizationProjection(
            run_id=run_id,
            source_run_version=source_run_version,
            state=state,
        )

    async def get_mutation_snapshot_for_update(
        self, *, run_id: str
    ) -> RunFinalizationMutationSnapshot:
        connection = self._require_connection()
        self._require_locked_run(run_id)
        run_version = await connection.fetchval(
            "SELECT version FROM qep_runs WHERE id = $1",
            run_id,
        )
        attempt = await connection.fetchrow(
            """
            SELECT attempt.id, attempt.version
            FROM qep_attempts AS attempt
            JOIN qep_assignments AS assignment ON assignment.id = attempt.assignment_id
            JOIN qep_runs AS run ON run.id = attempt.run_id
            WHERE attempt.run_id = $1
              AND attempt.fence = run.current_fence
              AND assignment.attempt_id = attempt.id
            FOR UPDATE OF attempt, assignment
            """,
            run_id,
        )
        self._snapshot = RunFinalizationMutationSnapshot(
            run_id=run_id,
            run_version=run_version,
            attempt_id=None if attempt is None else attempt["id"],
            attempt_version=None if attempt is None else attempt["version"],
        )
        return self._snapshot

    async def publish_finalization(
        self, *, publication: RunFinalizationPublication
    ) -> ReplayResult[RunFinalizationProjection]:
        try:
            return await self._publish_finalization(publication=publication)
        except BaseException:
            self._aborted = True
            raise

    async def _publish_finalization(
        self, *, publication: RunFinalizationPublication
    ) -> ReplayResult[RunFinalizationProjection]:
        connection = self._require_connection()
        authority = self._require_locked_run(publication.projection.run_id)
        if publication.authority != authority:
            raise AuthorityStateConflict(reason="publication_authority_superseded")

        stored = await connection.fetchrow(
            """
            SELECT source_run_version, basis_digest
            FROM qep_run_finalization_bases
            WHERE run_id = $1
            """,
            publication.projection.run_id,
        )
        if stored is not None:
            stored_digest = _digest(stored["basis_digest"])
            if (
                stored["source_run_version"] == publication.authority.source_run_version
                and stored_digest == publication.basis.basis_digest
            ):
                projection = await self.lookup_stored(identity_scope=publication.identity_scope)
                assert projection is not None
                return ReplayResult(value=projection, replayed=True)
            raise IdempotencyConflict(
                scope=f"run:{publication.projection.run_id}:finalization",
                key=str(publication.authority.source_run_version),
                stored_digest=stored_digest,
                received_digest=publication.basis.basis_digest,
            )

        if self._snapshot != publication.expected_snapshot:
            current = self._snapshot
            raise VersionConflict(
                entity_type="run",
                entity_id=publication.projection.run_id,
                current_version=0 if current is None else current.run_version,
                expected_version=publication.expected_snapshot.run_version,
            )
        await self._require_verified_terminal_input(publication)
        await self._insert_resolution_facts(publication)
        await self._insert_basis(publication)
        await self._publish_attempt_terminal(publication)
        await self._publish_run_projection(publication)
        await self._insert_audit(publication)
        await self._insert_outbox(publication)
        return ReplayResult(value=publication.projection, replayed=False)

    async def _require_verified_terminal_input(
        self, publication: RunFinalizationPublication
    ) -> None:
        basis = publication.basis
        if basis.final_attempt_id is None:
            return
        if basis.evidence_root_digest is None:
            raise AuthorityStateConflict(reason="run_finalization_evidence_binding_invalid")
        connection = self._require_connection()
        verified = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM qep_attempts AS attempt
                JOIN qep_evidence_index AS evidence ON evidence.attempt_id = attempt.id
                WHERE attempt.id = $1
                  AND attempt.run_id = $2
                  AND attempt.version = $3
                  AND attempt.fence = $4
                  AND attempt.worker_id = $5
                  AND attempt.worker_generation = $6
                  AND attempt.spec_digest = $7
                  AND evidence.fence = attempt.fence
                  AND evidence.root_digest = $8
            )
            """,
            basis.final_attempt_id,
            basis.run_id,
            basis.final_attempt_version,
            basis.final_attempt_fence,
            basis.worker_id,
            basis.worker_generation,
            _digest_hex(basis.execution_spec_digest),
            _digest_hex(basis.evidence_root_digest),
        )
        if not verified:
            raise AuthorityStateConflict(reason="run_finalization_evidence_binding_invalid")

    async def _insert_resolution_facts(self, publication: RunFinalizationPublication) -> None:
        connection = self._require_connection()
        values = [
            (
                publication.projection.run_id,
                entry.item_key.manifest_id,
                entry.item_key.item_index,
                publication.projection.source_run_version,
                _digest_hex(entry.original.digest),
                _digest_hex(entry.effective.digest),
                None
                if entry.unknown_lineage_digest is None
                else _digest_hex(entry.unknown_lineage_digest),
                entry.aggregation_class.value,
                _digest_hex(entry.item_resolution_digest),
                _json(entry.canonical_payload()),
            )
            for entry in publication.resolution_set.entries
        ]
        await connection.executemany(
            """
            INSERT INTO qep_run_item_resolutions (
                run_id, manifest_id, item_index, source_run_version,
                original_fact_digest, effective_fact_digest, unknown_lineage_digest,
                aggregation_class, item_resolution_digest, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, transaction_timestamp())
            """,
            values,
        )

    async def _insert_basis(self, publication: RunFinalizationPublication) -> None:
        basis = publication.basis
        await self._require_connection().execute(
            """
            INSERT INTO qep_run_finalization_bases (
                id, run_id, batch_id, source_run_version, final_attempt_id, final_fence,
                item_resolution_set_digest, terminal_input_kind, disposition, outcome,
                basis_digest, payload, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                transaction_timestamp()
            )
            """,
            f"run-finalization-basis-{_digest_hex(basis.basis_digest)}",
            basis.run_id,
            basis.batch_id,
            basis.source_run_version,
            basis.final_attempt_id,
            basis.final_attempt_fence,
            _digest_hex(basis.item_resolution_set_digest),
            basis.terminal_input_kind.value,
            basis.disposition.value,
            basis.outcome.value,
            _digest_hex(basis.basis_digest),
            _json(basis.canonical_payload()),
        )

    async def _publish_attempt_terminal(self, publication: RunFinalizationPublication) -> None:
        basis = publication.basis
        if basis.final_attempt_id is None:
            return
        status = await self._require_connection().execute(
            """
            UPDATE qep_attempts
            SET state = $1,
                version = version + 1,
                finished_at = transaction_timestamp()
            WHERE id = $2
              AND run_id = $3
              AND version = $4
              AND fence = $5
            """,
            basis.final_attempt_state.value,
            basis.final_attempt_id,
            basis.run_id,
            basis.final_attempt_version,
            basis.final_attempt_fence,
        )
        if status != "UPDATE 1":
            raise VersionConflict(
                entity_type="attempt",
                entity_id=basis.final_attempt_id,
                current_version=self._snapshot.attempt_version,
                expected_version=basis.final_attempt_version,
            )

    async def _publish_run_projection(self, publication: RunFinalizationPublication) -> None:
        projection = publication.projection
        state = projection.state
        status = await self._require_connection().execute(
            """
            UPDATE qep_runs
            SET orchestration_phase = $1,
                disposition = $2,
                outcome = $3,
                finalization_basis_digest = $4,
                version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $5
              AND batch_id = $6
              AND version = $7
            """,
            state.phase.value,
            state.disposition.value,
            state.outcome.value,
            _digest_hex(state.finalization_basis_digest),
            projection.run_id,
            publication.authority.batch_id,
            publication.expected_snapshot.run_version,
        )
        if status != "UPDATE 1":
            raise VersionConflict(
                entity_type="run",
                entity_id=projection.run_id,
                current_version=self._snapshot.run_version,
                expected_version=publication.expected_snapshot.run_version,
            )

    async def _insert_audit(self, publication: RunFinalizationPublication) -> None:
        basis = publication.basis
        side_effect = publication.side_effect
        await self._require_connection().execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES ($1, 'system', 'finalize_run', 'run', $2, 'allowed', $3, NULL, $4, $5,
                      transaction_timestamp())
            """,
            f"run-finalization-audit-{_digest_hex(basis.basis_digest)}",
            side_effect.run_id,
            "run_finalization_basis_committed",
            _digest_hex(side_effect.basis_digest),
            _json(
                {
                    "schema_version": "qep.run-finalization-audit.v1",
                    "run_id": side_effect.run_id,
                    "basis_digest": side_effect.basis_digest.value,
                    "outcome": side_effect.outcome.value,
                    "authority_digest": publication.authority.authority_digest.value,
                }
            ),
        )

    async def _insert_outbox(self, publication: RunFinalizationPublication) -> None:
        handoff = publication.handoff
        await self._require_connection().execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type, payload_digest,
                payload, status, available_at, attempts, created_at
            ) VALUES ($1, $2, 'run', $3, 'run.closed.v1', $4, $5, 'pending',
                      transaction_timestamp(), 0, transaction_timestamp())
            """,
            handoff.handoff_id,
            handoff.event_id,
            handoff.run_id,
            _digest_hex(handoff.payload_digest),
            _json(_handoff_payload(handoff)),
        )

    def _require_connection(self) -> asyncpg.Connection:
        if self._aborted:
            self._state_error("aborted")
        if self._connection is None:
            self._state_error("not_active" if not self._closed else "closed")
        return self._connection

    def _require_locked_run(self, run_id: str) -> FinalizeRunAuthority:
        if self._authority is None or self._authority.run_id != run_id:
            self._state_error("authority_not_locked")
        return self._authority

    @staticmethod
    def _state_error(reason: str) -> None:
        raise PortContractError(resource="unit_of_work", field="state", reason=reason)


def _handoff_payload(handoff: RunClosedHandoff) -> dict[str, object]:
    return {
        "schema_version": handoff.schema_version,
        "identity_algorithm_version": handoff.identity_algorithm_version,
        "semantic_trigger_key": handoff.semantic_trigger_key.value,
        "handoff_id": handoff.handoff_id,
        "event_id": handoff.event_id,
        "batch_id": handoff.batch_id,
        "run_id": handoff.run_id,
        "source_run_version": handoff.source_run_version,
        "run_basis_digest": handoff.run_basis_digest.value,
        "run_outcome": handoff.run_outcome.value,
        "terminal_input_kind": handoff.terminal_input_kind.value,
        "manifest_digest": handoff.manifest_digest.value,
        "shard_plan_digest": handoff.shard_plan_digest.value,
        "run_item_set_digest": handoff.run_item_set_digest.value,
        "original_resolution_set_digest": handoff.original_resolution_set_digest.value,
        "effective_resolution_set_digest": handoff.effective_resolution_set_digest.value,
        "item_resolution_set_digest": handoff.item_resolution_set_digest.value,
        "item_count": handoff.item_count,
        "cancellation_intent_digest": _optional_digest_value(handoff.cancellation_intent_digest),
        "unknown_observation_digest": _optional_digest_value(handoff.unknown_observation_digest),
        "adjudication_chain_digest": _optional_digest_value(handoff.adjudication_chain_digest),
        "retry_chain_digest": _optional_digest_value(handoff.retry_chain_digest),
        "authority_digest": handoff.authority_digest.value,
        "write_epoch": handoff.write_epoch,
        "destination": handoff.destination,
        "payload_digest": handoff.payload_digest.value,
    }


def _optional_digest_value(value: Digest | None) -> str | None:
    return None if value is None else value.value


def _digest(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
