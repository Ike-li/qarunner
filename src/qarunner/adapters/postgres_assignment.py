"""PostgreSQL first-attempt Assignment CAS gateway (ASGN-OFFER..ASGN-CLOSE).

Implements `AssignmentGateway` as a caller-owned one-shot transaction, mirroring
the sticky-abort / read_committed / FOR UPDATE / version-CAS pattern used by
`PostgresRunFinalizationUnitOfWork` and the other M1-B2 adapters.

M1 single-worker scope (`docs/greenfield/13_DIRECTION_REVIEW.md`, DIR-DEC-002-A):
worker identity is a seeded fixture and `offer_token_hash` is a deterministic
fixture digest. Real Worker registration, offer-token security, renew,
generation rotation, mTLS, and the physical second host defer to M3/E1.
"""

from __future__ import annotations

import json
from types import TracebackType
from typing import Self

import asyncpg

from qarunner.application.ports.assignment import AssignmentMutationSnapshot
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import Run
from qarunner.domain.digest import Digest
from qarunner.domain.errors import VersionConflict
from qarunner.domain.run import CommitStartResult, RunState


class PostgresAssignmentGateway:
    """One-shot caller-owned transaction implementing AssignmentGateway."""

    def __init__(self, pool: asyncpg.Pool, *, offer_token_hash: str) -> None:
        if not isinstance(offer_token_hash, str) or len(offer_token_hash) != 64:
            raise PortContractError(
                resource="assignment_gateway",
                field="offer_token_hash",
                reason="invalid_fixture_digest",
            )
        if any(ch not in "0123456789abcdef" for ch in offer_token_hash):
            raise PortContractError(
                resource="assignment_gateway",
                field="offer_token_hash",
                reason="invalid_fixture_digest",
            )
        self._pool = pool
        self._offer_token_hash = offer_token_hash
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._snapshot: AssignmentMutationSnapshot | None = None
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

    async def get_run_for_update(self, *, run_id: str) -> AssignmentMutationSnapshot:
        connection = self._require_connection()
        if self._snapshot is not None:
            if self._snapshot.run_id != run_id:
                self._state_error("aggregate_already_locked")
            return self._snapshot
        row = await connection.fetchrow(
            """
            SELECT
                id,
                orchestration_phase,
                current_fence,
                version
            FROM qep_runs AS run
            WHERE id = $1
            FOR UPDATE OF run
            """,
            run_id,
        )
        if row is None:
            raise PortContractError(
                resource="assignment_gateway", field="run_id", reason="not_found"
            )
        phase = row["orchestration_phase"]
        # Contenders that lost the race see the winner's committed phase after
        # FOR UPDATE serializes them. Map that to VersionConflict so the
        # ASGN-OFFER concurrent matrix stays on the CAS path (not a free-form
        # domain conflict).
        if phase != RunState.QUEUED.value:
            raise VersionConflict(
                entity_type="run",
                entity_id=run_id,
                current_version=row["version"],
                expected_version=max(row["version"] - 1, 0),
            )
        # QUEUED rehydration is a direct row→object build (no assignments/attempts).
        run = Run(
            id=row["id"],
            state=RunState.QUEUED,
            version=row["version"],
            current_fence=row["current_fence"],
            assignments=(),
            current_assignment_id=None,
            attempts=(),
            retry_intents=(),
            pending_retry_intent_id=None,
        )
        self._snapshot = AssignmentMutationSnapshot(run=run)
        return self._snapshot

    async def publish_offer(
        self, *, offered: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        try:
            return await self._publish_offer(offered=offered, expected=expected)
        except BaseException:
            self._aborted = True
            raise

    async def publish_claim(
        self, *, claimed: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        self._aborted = True
        raise PortContractError(
            resource="assignment_gateway", field="publish_claim", reason="not_implemented"
        )

    async def publish_commit_start(
        self, *, commit: CommitStartResult, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[CommitStartResult]:
        self._aborted = True
        raise PortContractError(
            resource="assignment_gateway",
            field="publish_commit_start",
            reason="not_implemented",
        )

    async def publish_close(
        self, *, closed: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        self._aborted = True
        raise PortContractError(
            resource="assignment_gateway", field="publish_close", reason="not_implemented"
        )

    async def _publish_offer(
        self, *, offered: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        connection = self._require_connection()
        if self._snapshot is None or self._snapshot != expected:
            current = self._snapshot
            raise VersionConflict(
                entity_type="run",
                entity_id=expected.run_id,
                current_version=0 if current is None else current.version,
                expected_version=expected.version,
            )
        if offered.id != expected.run_id:
            raise PortContractError(
                resource="assignment_gateway", field="offered", reason="run_id_mismatch"
            )
        if offered.state is not RunState.ASSIGNED:
            raise PortContractError(
                resource="assignment_gateway", field="offered", reason="not_assigned"
            )
        assignment = offered.assignment
        if assignment is None:
            raise PortContractError(
                resource="assignment_gateway", field="offered", reason="missing_assignment"
            )

        insert_status = await connection.execute(
            """
            INSERT INTO qep_assignments (
                id, run_id, worker_id, worker_generation, spec_digest, offer_token_hash,
                state, offered_at, expires_at, claimed_at, committed_at,
                attempt_id, fence, version, payload
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                'offered', $7, $8, NULL, NULL,
                NULL, NULL, 0, $9::jsonb
            )
            """,
            assignment.id,
            offered.id,
            assignment.worker.worker_id,
            assignment.worker.generation,
            _digest_hex(assignment.spec_digest),
            self._offer_token_hash,
            assignment.offered_at,
            assignment.expires_at,
            _assignment_payload(assignment),
        )
        if insert_status != "INSERT 0 1":
            raise PortContractError(
                resource="assignment_gateway",
                field="qep_assignments",
                reason="insert_suppressed",
            )

        # qep_runs has no current_assignment_id column; the active pointer is the
        # partial unique index qep_assignments_one_active_per_run + phase.
        update_status = await connection.execute(
            """
            UPDATE qep_runs
            SET orchestration_phase = $1,
                version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $2
              AND version = $3
            """,
            RunState.ASSIGNED.value,
            offered.id,
            expected.version,
        )
        if update_status != "UPDATE 1":
            raise VersionConflict(
                entity_type="run",
                entity_id=offered.id,
                current_version=expected.version,
                expected_version=expected.version,
            )

        self._snapshot = AssignmentMutationSnapshot(run=offered)
        return ReplayResult(value=offered, replayed=False)

    def _require_connection(self) -> asyncpg.Connection:
        if self._connection is None:
            self._state_error("not_active")
        return self._connection

    def _state_error(self, reason: str) -> None:
        raise PortContractError(resource="assignment_gateway", field="lifecycle", reason=reason)


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _assignment_payload(assignment) -> str:
    return json.dumps(
        {
            "schema_version": "qep.assignment.v1",
            "assignment_id": assignment.id,
            "worker_id": assignment.worker.worker_id,
            "worker_generation": assignment.worker.generation,
            "spec_digest": assignment.spec_digest.value,
            "state": assignment.state.value,
            "retry_intent_id": assignment.retry_intent_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
