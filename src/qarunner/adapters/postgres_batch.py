"""PostgreSQL Batch create gateway with scoped idempotency (BATCH-IDEM).

Caller-owned one-shot transaction: sticky-abort / read_committed / FOR UPDATE on
suite authority, insert under UNIQUE (idempotency_scope, idempotency_key).
Exact digest replay returns the stored Batch; digest mismatch raises
IdempotencyConflict. Mirrors PostgresSuiteGateway lifecycle conventions.
"""

from __future__ import annotations

import json
from types import TracebackType
from typing import Self

import asyncpg

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import Batch, BatchState, Digest, IdempotencyConflict
from qarunner.domain.errors import SuiteConflict


class PostgresBatchGateway:
    """One-shot caller-owned transaction implementing BatchGateway.create."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
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

    async def create(self, *, batch: Batch) -> ReplayResult[Batch]:
        try:
            return await self._create(batch=batch)
        except BaseException:
            self._aborted = True
            raise

    async def get_batch(self, *, batch_id: str) -> Batch:
        # Read-only lookup: leave sticky-abort to create mutations.
        return await self._get_batch(batch_id=batch_id)

    async def get_by_idempotency(
        self, *, idempotency_scope: str, idempotency_key: str
    ) -> Batch | None:
        # Read-only lookup: leave sticky-abort to create mutations.
        return await self._get_by_idempotency(
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
        )

    async def _create(self, *, batch: Batch) -> ReplayResult[Batch]:
        connection = self._require_connection()
        if not batch.has_create_identity:
            raise PortContractError(
                resource="batch_gateway", field="create_identity", reason="missing"
            )
        assert batch.project_id is not None
        assert batch.suite_revision_id is not None
        assert batch.request_digest is not None
        assert batch.idempotency_scope is not None
        assert batch.idempotency_key is not None
        assert batch.created_at is not None
        if batch.version != 0 or batch.state is not BatchState.DRAFT:
            raise PortContractError(
                resource="batch_gateway", field="batch", reason="not_initial_create"
            )

        await self._require_suite_accepts(
            connection,
            suite_revision_id=batch.suite_revision_id,
            project_id=batch.project_id,
        )

        try:
            # Savepoint: a UniqueViolation aborts the subxact only; recovery SELECT
            # must still run in the outer caller-owned transaction.
            await connection.execute("SAVEPOINT batch_create_insert")
            insert_status = await connection.execute(
                """
                INSERT INTO qep_batches (
                    id, project_id, suite_revision_id, request_digest, idempotency_scope,
                    idempotency_key, state, deadline_at, priority_class, version, write_epoch,
                    created_at, updated_at, payload
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 0, $11, $11, $12::jsonb
                )
                """,
                batch.id,
                batch.project_id,
                batch.suite_revision_id,
                _digest_hex(batch.request_digest),
                batch.idempotency_scope,
                batch.idempotency_key,
                batch.state.value,
                batch.deadline_at,
                batch.priority_class,
                batch.version,
                batch.created_at,
                _batch_payload(batch),
            )
            await connection.execute("RELEASE SAVEPOINT batch_create_insert")
        except asyncpg.UniqueViolationError as error:
            await connection.execute("ROLLBACK TO SAVEPOINT batch_create_insert")
            # Exact replay (same id + same key) may surface either the pkey or the
            # (scope, key) unique first; always recover via scoped idempotency lookup.
            winner = await connection.fetchrow(
                """
                SELECT
                    id, project_id, suite_revision_id, request_digest, idempotency_scope,
                    idempotency_key, state, version, created_at, priority_class, deadline_at
                FROM qep_batches
                WHERE idempotency_scope = $1
                  AND idempotency_key = $2
                FOR UPDATE
                """,
                batch.idempotency_scope,
                batch.idempotency_key,
            )
            if winner is not None:
                stored = _batch_from_row(winner)
                assert stored.request_digest is not None
                if stored.request_digest != batch.request_digest:
                    raise IdempotencyConflict(
                        scope=batch.idempotency_scope,
                        key=batch.idempotency_key,
                        stored_digest=stored.request_digest,
                        received_digest=batch.request_digest,
                    ) from error
                return ReplayResult(value=stored, replayed=True)
            # Missing scoped winner: either primary-key collision or a vanishing race.
            # Both are stable already_exists for the create command.
            raise PortContractError(
                resource="batch_gateway",
                field="batch_id",
                reason="already_exists",
            ) from error

        if insert_status != "INSERT 0 1":
            raise PortContractError(
                resource="batch_gateway", field="qep_batches", reason="insert_suppressed"
            )
        return ReplayResult(value=batch, replayed=False)

    async def _get_batch(self, *, batch_id: str) -> Batch:
        connection = self._require_connection()
        row = await connection.fetchrow(
            """
            SELECT
                id, project_id, suite_revision_id, request_digest, idempotency_scope,
                idempotency_key, state, version, created_at, priority_class, deadline_at
            FROM qep_batches
            WHERE id = $1
            """,
            batch_id,
        )
        if row is None:
            raise PortContractError(resource="batch_gateway", field="batch_id", reason="not_found")
        return _batch_from_row(row)

    async def _get_by_idempotency(
        self, *, idempotency_scope: str, idempotency_key: str
    ) -> Batch | None:
        connection = self._require_connection()
        row = await connection.fetchrow(
            """
            SELECT
                id, project_id, suite_revision_id, request_digest, idempotency_scope,
                idempotency_key, state, version, created_at, priority_class, deadline_at
            FROM qep_batches
            WHERE idempotency_scope = $1
              AND idempotency_key = $2
            """,
            idempotency_scope,
            idempotency_key,
        )
        if row is None:
            return None
        return _batch_from_row(row)

    async def _require_suite_accepts(
        self,
        connection: asyncpg.Connection,
        *,
        suite_revision_id: str,
        project_id: str,
    ) -> None:
        row = await connection.fetchrow(
            """
            SELECT suite.id AS suite_id, suite.project_id, suite.status
            FROM qep_suite_revisions AS revision
            JOIN qep_suites AS suite ON suite.id = revision.suite_id
            WHERE revision.id = $1
            FOR UPDATE OF suite
            """,
            suite_revision_id,
        )
        if row is None:
            raise PortContractError(
                resource="batch_gateway",
                field="suite_revision_id",
                reason="not_found",
            )
        if row["project_id"] != project_id:
            raise PortContractError(
                resource="batch_gateway",
                field="project_id",
                reason="suite_project_mismatch",
            )
        if row["status"] != "active":
            raise SuiteConflict(suite_id=row["suite_id"], reason="suite_retired")

    def _require_connection(self) -> asyncpg.Connection:
        if self._connection is None:
            self._state_error("not_active")
        return self._connection

    def _state_error(self, reason: str) -> None:
        raise PortContractError(resource="batch_gateway", field="lifecycle", reason=reason)


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _digest_from_hex(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _batch_from_row(row: asyncpg.Record) -> Batch:
    return Batch(
        id=row["id"],
        state=BatchState(row["state"]),
        version=row["version"],
        project_id=row["project_id"],
        suite_revision_id=row["suite_revision_id"],
        request_digest=_digest_from_hex(row["request_digest"]),
        idempotency_scope=row["idempotency_scope"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        priority_class=row["priority_class"] or "background",
        deadline_at=row["deadline_at"],
    )


def _batch_payload(batch: Batch) -> str:
    return json.dumps(
        {
            "schema_version": "qep.batch-create.v1",
            "batch_id": batch.id,
            "project_id": batch.project_id,
            "suite_revision_id": batch.suite_revision_id,
            "request_digest": None if batch.request_digest is None else batch.request_digest.value,
            "idempotency_scope": batch.idempotency_scope,
            "idempotency_key": batch.idempotency_key,
            "priority_class": batch.priority_class,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
