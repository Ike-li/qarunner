"""PostgreSQL first-attempt Assignment CAS gateway (ASGN-OFFER..ASGN-CLOSE).

Implements `AssignmentGateway` as a caller-owned one-shot transaction, mirroring
the sticky-abort / read_committed / FOR UPDATE / version-CAS pattern used by
`PostgresRunFinalizationUnitOfWork` and the other M1-B2 adapters.

M1 single-worker scope (single-ECS same-host MVP; physical second host deferred):
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
from qarunner.domain import Assignment, Attempt, Digest, Run, WorkerRef
from qarunner.domain.assignment import AssignmentClosure, AssignmentClosureKind, AssignmentState
from qarunner.domain.attempt import AttemptState
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
        if phase == RunState.QUEUED.value:
            # QUEUED may still carry closed historical assignments (id-reuse + exact
            # precommit-closure replay). Active precommit rows are never present here.
            history_rows = await connection.fetch(
                """
                SELECT
                    id, worker_id, worker_generation, spec_digest, state,
                    offered_at, expires_at, claimed_at, committed_at, version, payload
                FROM qep_assignments
                WHERE run_id = $1
                ORDER BY offered_at ASC, id ASC
                """,
                run_id,
            )
            history = tuple(_assignment_from_row(item) for item in history_rows)
            run = Run(
                id=row["id"],
                state=RunState.QUEUED,
                version=row["version"],
                current_fence=row["current_fence"],
                assignments=history,
                current_assignment_id=None,
                attempts=(),
                retry_intents=(),
                pending_retry_intent_id=None,
            )
        elif phase == RunState.ASSIGNED.value:
            assignment_rows = await connection.fetch(
                """
                SELECT
                    id, worker_id, worker_generation, spec_digest, state,
                    offered_at, expires_at, claimed_at, committed_at, version, payload
                FROM qep_assignments AS assignment
                WHERE run_id = $1
                ORDER BY offered_at ASC, id ASC
                FOR UPDATE OF assignment
                """,
                run_id,
            )
            if not assignment_rows:
                raise PortContractError(
                    resource="assignment_gateway",
                    field="assignment",
                    reason="active_assignment_missing",
                )
            assignments = tuple(_assignment_from_row(item) for item in assignment_rows)
            active = next(
                (
                    item
                    for item in reversed(assignments)
                    if item.state in {AssignmentState.OFFERED, AssignmentState.CLAIMED}
                ),
                None,
            )
            if active is None:
                raise PortContractError(
                    resource="assignment_gateway",
                    field="assignment",
                    reason="active_assignment_missing",
                )
            run = Run(
                id=row["id"],
                state=RunState.ASSIGNED,
                version=row["version"],
                current_fence=row["current_fence"],
                assignments=assignments,
                current_assignment_id=active.id,
                attempts=(),
                retry_intents=(),
                pending_retry_intent_id=None,
            )
        elif phase == RunState.RUNNING.value:
            # Rehydrate for exact start_commit_key replay (no second attempt).
            assignment_row = await connection.fetchrow(
                """
                SELECT
                    id, worker_id, worker_generation, spec_digest, state,
                    offered_at, expires_at, claimed_at, committed_at, version, payload
                FROM qep_assignments AS assignment
                WHERE run_id = $1
                  AND state = $2
                FOR UPDATE OF assignment
                """,
                run_id,
                AssignmentState.COMMITTED.value,
            )
            if assignment_row is None:
                raise PortContractError(
                    resource="assignment_gateway",
                    field="assignment",
                    reason="active_assignment_missing",
                )
            attempt_row = await connection.fetchrow(
                """
                SELECT
                    id, run_id, attempt_no, fence, assignment_id, worker_id,
                    worker_generation, spec_digest, start_commit_key, state, version
                FROM qep_attempts AS attempt
                WHERE run_id = $1
                  AND fence = $2
                FOR UPDATE OF attempt
                """,
                run_id,
                row["current_fence"],
            )
            if attempt_row is None:
                raise PortContractError(
                    resource="assignment_gateway",
                    field="attempt",
                    reason="current_attempt_missing",
                )
            assignment = _assignment_from_row(assignment_row)
            attempt = _attempt_from_row(attempt_row)
            run = Run(
                id=row["id"],
                state=RunState.RUNNING,
                version=row["version"],
                current_fence=row["current_fence"],
                assignments=(assignment,),
                current_assignment_id=assignment.id,
                attempts=(attempt,),
                retry_intents=(),
                pending_retry_intent_id=None,
            )
        else:
            # Contenders that lost an offer race (or later phases) surface as CAS.
            raise VersionConflict(
                entity_type="run",
                entity_id=run_id,
                current_version=row["version"],
                expected_version=max(row["version"] - 1, 0),
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
        try:
            return await self._publish_claim(claimed=claimed, expected=expected)
        except BaseException:
            self._aborted = True
            raise

    async def publish_commit_start(
        self, *, commit: CommitStartResult, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[CommitStartResult]:
        try:
            return await self._publish_commit_start(commit=commit, expected=expected)
        except BaseException:
            self._aborted = True
            raise

    async def publish_close(
        self, *, closed: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        try:
            return await self._publish_close(closed=closed, expected=expected)
        except BaseException:
            self._aborted = True
            raise

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

    async def _publish_claim(
        self, *, claimed: Run, expected: AssignmentMutationSnapshot
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
        if claimed.id != expected.run_id:
            raise PortContractError(
                resource="assignment_gateway", field="claimed", reason="run_id_mismatch"
            )
        if claimed.state is not RunState.ASSIGNED:
            raise PortContractError(
                resource="assignment_gateway", field="claimed", reason="not_assigned"
            )
        assignment = claimed.assignment
        if assignment is None:
            raise PortContractError(
                resource="assignment_gateway", field="claimed", reason="missing_assignment"
            )
        if assignment.state is not AssignmentState.CLAIMED:
            raise PortContractError(
                resource="assignment_gateway", field="claimed", reason="not_claimed"
            )
        if assignment.claimed_at is None:
            raise PortContractError(
                resource="assignment_gateway", field="claimed", reason="missing_claimed_at"
            )

        update_assignment = await connection.execute(
            """
            UPDATE qep_assignments
            SET state = $1,
                claimed_at = $2,
                version = version + 1,
                payload = $3::jsonb
            WHERE id = $4
              AND run_id = $5
              AND state = $6
              AND version = 0
            """,
            AssignmentState.CLAIMED.value,
            assignment.claimed_at,
            _assignment_payload(assignment),
            assignment.id,
            claimed.id,
            AssignmentState.OFFERED.value,
        )
        if update_assignment != "UPDATE 1":
            raise VersionConflict(
                entity_type="assignment",
                entity_id=assignment.id,
                current_version=0,
                expected_version=0,
            )

        update_run = await connection.execute(
            """
            UPDATE qep_runs
            SET version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $1
              AND version = $2
              AND orchestration_phase = $3
            """,
            claimed.id,
            expected.version,
            RunState.ASSIGNED.value,
        )
        if update_run != "UPDATE 1":
            raise VersionConflict(
                entity_type="run",
                entity_id=claimed.id,
                current_version=expected.version,
                expected_version=expected.version,
            )

        self._snapshot = AssignmentMutationSnapshot(run=claimed)
        return ReplayResult(value=claimed, replayed=False)

    async def _publish_commit_start(
        self, *, commit: CommitStartResult, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[CommitStartResult]:
        connection = self._require_connection()
        if self._snapshot is None or self._snapshot != expected:
            current = self._snapshot
            raise VersionConflict(
                entity_type="run",
                entity_id=expected.run_id,
                current_version=0 if current is None else current.version,
                expected_version=expected.version,
            )
        if not isinstance(commit, CommitStartResult):
            raise PortContractError(
                resource="assignment_gateway", field="commit", reason="not_commit_result"
            )
        committed_run = commit.run
        attempt = commit.attempt
        if committed_run.id != expected.run_id:
            raise PortContractError(
                resource="assignment_gateway", field="commit", reason="run_id_mismatch"
            )
        if commit.replayed:
            # Domain already decided this is an exact replay; no durable write.
            self._snapshot = AssignmentMutationSnapshot(run=committed_run)
            return ReplayResult(value=commit, replayed=True)
        if committed_run.state is not RunState.RUNNING:
            raise PortContractError(
                resource="assignment_gateway", field="commit", reason="not_running"
            )
        assignment = committed_run.assignment
        if assignment is None or assignment.state is not AssignmentState.COMMITTED:
            raise PortContractError(
                resource="assignment_gateway", field="commit", reason="not_committed"
            )
        if assignment.committed_at is None:
            raise PortContractError(
                resource="assignment_gateway", field="commit", reason="missing_committed_at"
            )
        if commit.fence != committed_run.current_fence or commit.fence != attempt.fence:
            raise PortContractError(
                resource="assignment_gateway", field="commit", reason="fence_mismatch"
            )

        insert_status = await connection.execute(
            """
            INSERT INTO qep_attempts (
                id, run_id, attempt_no, fence, assignment_id, worker_id,
                worker_generation, spec_digest, start_commit_key, state,
                version, started_at, payload
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10,
                0, $11, $12::jsonb
            )
            """,
            attempt.id,
            attempt.run_id,
            attempt.attempt_no,
            attempt.fence,
            attempt.assignment_id,
            attempt.worker.worker_id,
            attempt.worker.generation,
            _digest_hex(attempt.spec_digest),
            attempt.start_commit_key,
            AttemptState.START_COMMITTED.value,
            assignment.committed_at,
            _attempt_payload(attempt),
        )
        if insert_status != "INSERT 0 1":
            raise PortContractError(
                resource="assignment_gateway",
                field="qep_attempts",
                reason="insert_suppressed",
            )

        # Claimed rows are version=1 after ASGN-CLAIM (offer wrote 0, claim bumped to 1).
        update_assignment = await connection.execute(
            """
            UPDATE qep_assignments
            SET state = $1,
                committed_at = $2,
                attempt_id = $3,
                fence = $4,
                version = version + 1,
                payload = $5::jsonb
            WHERE id = $6
              AND run_id = $7
              AND state = $8
              AND version = 1
            """,
            AssignmentState.COMMITTED.value,
            assignment.committed_at,
            attempt.id,
            attempt.fence,
            _assignment_payload(assignment),
            assignment.id,
            committed_run.id,
            AssignmentState.CLAIMED.value,
        )
        if update_assignment != "UPDATE 1":
            raise VersionConflict(
                entity_type="assignment",
                entity_id=assignment.id,
                current_version=1,
                expected_version=1,
            )

        update_run = await connection.execute(
            """
            UPDATE qep_runs
            SET orchestration_phase = $1,
                current_fence = $2,
                attempt_count = $3,
                version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $4
              AND version = $5
              AND orchestration_phase = $6
            """,
            RunState.RUNNING.value,
            commit.fence,
            attempt.attempt_no,
            committed_run.id,
            expected.version,
            RunState.ASSIGNED.value,
        )
        if update_run != "UPDATE 1":
            raise VersionConflict(
                entity_type="run",
                entity_id=committed_run.id,
                current_version=expected.version,
                expected_version=expected.version,
            )

        self._snapshot = AssignmentMutationSnapshot(run=committed_run)
        return ReplayResult(value=commit, replayed=False)

    async def _publish_close(
        self, *, closed: Run, expected: AssignmentMutationSnapshot
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
        if closed.id != expected.run_id:
            raise PortContractError(
                resource="assignment_gateway", field="closed", reason="run_id_mismatch"
            )
        # Domain exact-replay returns the same aggregate version (no mutation).
        if closed.version == expected.version:
            self._snapshot = AssignmentMutationSnapshot(run=closed)
            return ReplayResult(value=closed, replayed=True)
        if closed.state is not RunState.QUEUED:
            raise PortContractError(
                resource="assignment_gateway", field="closed", reason="not_queued"
            )
        if closed.current_assignment_id is not None or closed.assignment is not None:
            raise PortContractError(
                resource="assignment_gateway",
                field="closed",
                reason="current_assignment_not_cleared",
            )
        if not closed.assignments:
            raise PortContractError(
                resource="assignment_gateway", field="closed", reason="missing_closed_assignment"
            )
        closed_assignment = closed.assignments[-1]
        if closed_assignment.closure is None:
            raise PortContractError(
                resource="assignment_gateway", field="closed", reason="missing_closure"
            )
        if closed_assignment.state not in {
            AssignmentState.EXPIRED_PRESTART,
            AssignmentState.RELEASED_PRESTART,
            AssignmentState.CANCELLED_PRESTART,
        }:
            raise PortContractError(
                resource="assignment_gateway", field="closed", reason="not_prestart_closed"
            )
        expected_active = expected.run.assignment
        if expected_active is None or expected_active.id != closed_assignment.id:
            raise PortContractError(
                resource="assignment_gateway",
                field="closed",
                reason="closed_assignment_mismatch",
            )
        if expected_active.state not in {AssignmentState.OFFERED, AssignmentState.CLAIMED}:
            raise PortContractError(
                resource="assignment_gateway",
                field="closed",
                reason="expected_not_precommit",
            )

        update_assignment = await connection.execute(
            """
            UPDATE qep_assignments
            SET state = $1,
                version = version + 1,
                payload = $2::jsonb
            WHERE id = $3
              AND run_id = $4
              AND state = $5
            """,
            closed_assignment.state.value,
            _assignment_payload(closed_assignment),
            closed_assignment.id,
            closed.id,
            expected_active.state.value,
        )
        if update_assignment != "UPDATE 1":
            raise VersionConflict(
                entity_type="assignment",
                entity_id=closed_assignment.id,
                current_version=0,
                expected_version=0,
            )

        update_run = await connection.execute(
            """
            UPDATE qep_runs
            SET orchestration_phase = $1,
                version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $2
              AND version = $3
              AND orchestration_phase = $4
            """,
            RunState.QUEUED.value,
            closed.id,
            expected.version,
            RunState.ASSIGNED.value,
        )
        if update_run != "UPDATE 1":
            raise VersionConflict(
                entity_type="run",
                entity_id=closed.id,
                current_version=expected.version,
                expected_version=expected.version,
            )

        self._snapshot = AssignmentMutationSnapshot(run=closed)
        return ReplayResult(value=closed, replayed=False)

    def _require_connection(self) -> asyncpg.Connection:
        if self._connection is None:
            self._state_error("not_active")
        return self._connection

    def _state_error(self, reason: str) -> None:
        raise PortContractError(resource="assignment_gateway", field="lifecycle", reason=reason)


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _digest_from_hex(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _assignment_from_row(row: asyncpg.Record) -> Assignment:
    state = AssignmentState(row["state"])
    closure = None
    raw_payload = row.get("payload")
    if raw_payload is not None and state in {
        AssignmentState.EXPIRED_PRESTART,
        AssignmentState.RELEASED_PRESTART,
        AssignmentState.CANCELLED_PRESTART,
    }:
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
        closure = _closure_from_payload(payload.get("closure"))
    return Assignment(
        id=row["id"],
        worker=WorkerRef(
            worker_id=row["worker_id"],
            generation=row["worker_generation"],
        ),
        spec_digest=_digest_from_hex(row["spec_digest"]),
        state=state,
        offered_at=row["offered_at"],
        expires_at=row["expires_at"],
        claimed_at=row["claimed_at"],
        committed_at=row.get("committed_at"),
        closure=closure,
    )


def _closure_from_payload(raw: object) -> AssignmentClosure | None:
    if not isinstance(raw, dict):
        return None
    worker_raw = raw.get("worker")
    worker = None
    if isinstance(worker_raw, dict):
        worker = WorkerRef(
            worker_id=str(worker_raw["worker_id"]),
            generation=int(worker_raw["generation"]),
        )
    intent = raw.get("cancellation_intent_digest")
    return AssignmentClosure(
        assignment_id=str(raw["assignment_id"]),
        idempotency_key=str(raw["idempotency_key"]),
        kind=AssignmentClosureKind(str(raw["kind"])),
        effective_at=_parse_utc(str(raw["effective_at"])),
        recorded_at=_parse_utc(str(raw["recorded_at"])),
        worker=worker,
        cancellation_intent_digest=(None if intent is None else Digest(str(intent))),
    )


def _parse_utc(value: str):
    from datetime import datetime

    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _attempt_from_row(row: asyncpg.Record) -> Attempt:
    return Attempt(
        id=row["id"],
        run_id=row["run_id"],
        attempt_no=row["attempt_no"],
        fence=row["fence"],
        assignment_id=row["assignment_id"],
        worker=WorkerRef(
            worker_id=row["worker_id"],
            generation=row["worker_generation"],
        ),
        spec_digest=_digest_from_hex(row["spec_digest"]),
        start_commit_key=row["start_commit_key"],
        events=(),
        evidence=None,
        unknown_observation=None,
        adjudications=(),
        state=AttemptState(row["state"]),
        version=row["version"],
    )


def _assignment_payload(assignment: Assignment) -> str:
    payload: dict[str, object] = {
        "schema_version": "qep.assignment.v1",
        "assignment_id": assignment.id,
        "worker_id": assignment.worker.worker_id,
        "worker_generation": assignment.worker.generation,
        "spec_digest": assignment.spec_digest.value,
        "state": assignment.state.value,
        "retry_intent_id": assignment.retry_intent_id,
    }
    if assignment.closure is not None:
        closure = assignment.closure
        payload["closure"] = {
            "assignment_id": closure.assignment_id,
            "idempotency_key": closure.idempotency_key,
            "kind": closure.kind.value,
            "effective_at": closure.effective_at.isoformat().replace("+00:00", "Z"),
            "recorded_at": closure.recorded_at.isoformat().replace("+00:00", "Z"),
            "worker": (
                None
                if closure.worker is None
                else {
                    "worker_id": closure.worker.worker_id,
                    "generation": closure.worker.generation,
                }
            ),
            "cancellation_intent_digest": (
                None
                if closure.cancellation_intent_digest is None
                else closure.cancellation_intent_digest.value
            ),
        }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _attempt_payload(attempt: Attempt) -> str:
    return json.dumps(
        {
            "schema_version": "qep.attempt.v1",
            "attempt_id": attempt.id,
            "run_id": attempt.run_id,
            "attempt_no": attempt.attempt_no,
            "fence": attempt.fence,
            "assignment_id": attempt.assignment_id,
            "start_commit_key": attempt.start_commit_key,
            "state": attempt.state.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
