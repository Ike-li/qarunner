"""PostgreSQL Suite aggregate CAS gateway (SUITE-PERSIST).

Caller-owned one-shot transaction: sticky-abort / read_committed / FOR UPDATE /
version-CAS, mirroring PostgresAssignmentGateway.
"""

from __future__ import annotations

import json
from types import TracebackType
from typing import Self

import asyncpg

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.suite import SuiteMutationSnapshot
from qarunner.domain import Digest, Suite, SuiteRevision, SuiteRevisionStatus, SuiteStatus
from qarunner.domain.errors import VersionConflict


class PostgresSuiteGateway:
    """One-shot caller-owned transaction implementing SuiteGateway."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._snapshot: SuiteMutationSnapshot | None = None
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

    async def get_suite_for_update(self, *, suite_id: str) -> SuiteMutationSnapshot:
        connection = self._require_connection()
        if self._snapshot is not None:
            if self._snapshot.suite_id != suite_id:
                self._state_error("aggregate_already_locked")
            return self._snapshot
        row = await connection.fetchrow(
            """
            SELECT id, project_id, name, status, version, created_at, retired_at
            FROM qep_suites AS suite
            WHERE id = $1
            FOR UPDATE OF suite
            """,
            suite_id,
        )
        if row is None:
            raise PortContractError(resource="suite_gateway", field="suite_id", reason="not_found")
        revision_rows = await connection.fetch(
            """
            SELECT
                id, suite_id, revision_no, source_spec_digest, config_digest,
                framework, resource_profile_id, status, created_at
            FROM qep_suite_revisions
            WHERE suite_id = $1
            ORDER BY revision_no ASC
            """,
            suite_id,
        )
        if not revision_rows:
            raise PortContractError(
                resource="suite_gateway",
                field="revisions",
                reason="empty",
            )
        revisions = tuple(_revision_from_row(item) for item in revision_rows)
        suite = Suite(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            status=SuiteStatus(row["status"]),
            version=row["version"],
            revisions=revisions,
            current_revision_id=revisions[-1].id,
            retired_at=row["retired_at"],
        )
        self._snapshot = SuiteMutationSnapshot(suite=suite)
        return self._snapshot

    async def register(self, *, suite: Suite) -> ReplayResult[Suite]:
        try:
            return await self._register(suite=suite)
        except BaseException:
            self._aborted = True
            raise

    async def publish_revision(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]:
        try:
            return await self._publish_revision(suite=suite, expected=expected)
        except BaseException:
            self._aborted = True
            raise

    async def publish_retire(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]:
        try:
            return await self._publish_retire(suite=suite, expected=expected)
        except BaseException:
            self._aborted = True
            raise

    async def _register(self, *, suite: Suite) -> ReplayResult[Suite]:
        connection = self._require_connection()
        if suite.version != 0 or len(suite.revisions) != 1:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="not_initial_register"
            )
        existing = await connection.fetchrow(
            "SELECT id, version FROM qep_suites WHERE id = $1",
            suite.id,
        )
        if existing is not None:
            # Exact register replay: same id already present with version 0.
            loaded = await self.get_suite_for_update(suite_id=suite.id)
            if (
                loaded.suite.project_id == suite.project_id
                and loaded.suite.name == suite.name
                and loaded.suite.current_revision_id == suite.current_revision_id
                and loaded.suite.revisions[0].source_spec_digest
                == suite.revisions[0].source_spec_digest
                and loaded.suite.revisions[0].config_digest == suite.revisions[0].config_digest
            ):
                return ReplayResult(value=loaded.suite, replayed=True)
            raise PortContractError(
                resource="suite_gateway", field="suite_id", reason="already_exists"
            )

        revision = suite.revisions[0]
        insert_suite = await connection.execute(
            """
            INSERT INTO qep_suites (
                id, project_id, name, status, version, created_at, retired_at
            ) VALUES ($1, $2, $3, $4, $5, $6, NULL)
            """,
            suite.id,
            suite.project_id,
            suite.name,
            suite.status.value,
            suite.version,
            revision.created_at,
        )
        if insert_suite != "INSERT 0 1":
            raise PortContractError(
                resource="suite_gateway", field="qep_suites", reason="insert_suppressed"
            )
        insert_revision = await connection.execute(
            """
            INSERT INTO qep_suite_revisions (
                id, suite_id, revision_no, source_spec_digest, config_digest,
                framework, resource_profile_id, status, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
            """,
            revision.id,
            revision.suite_id,
            revision.revision_no,
            _digest_hex(revision.source_spec_digest),
            _digest_hex(revision.config_digest),
            revision.framework,
            revision.resource_profile_id,
            revision.status.value,
            _revision_payload(revision),
            revision.created_at,
        )
        if insert_revision != "INSERT 0 1":
            raise PortContractError(
                resource="suite_gateway",
                field="qep_suite_revisions",
                reason="insert_suppressed",
            )
        self._snapshot = SuiteMutationSnapshot(suite=suite)
        return ReplayResult(value=suite, replayed=False)

    async def _publish_revision(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]:
        connection = self._require_connection()
        self._require_expected(expected)
        if suite.id != expected.suite_id:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="suite_id_mismatch"
            )
        if suite.version != expected.version + 1:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="version_not_monotonic"
            )
        if len(suite.revisions) != len(expected.suite.revisions) + 1:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="revision_count_mismatch"
            )
        new_revision = suite.revisions[-1]
        # Historical rows must be identical (immutability).
        if suite.revisions[:-1] != expected.suite.revisions:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="history_rewritten"
            )

        insert_revision = await connection.execute(
            """
            INSERT INTO qep_suite_revisions (
                id, suite_id, revision_no, source_spec_digest, config_digest,
                framework, resource_profile_id, status, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
            """,
            new_revision.id,
            new_revision.suite_id,
            new_revision.revision_no,
            _digest_hex(new_revision.source_spec_digest),
            _digest_hex(new_revision.config_digest),
            new_revision.framework,
            new_revision.resource_profile_id,
            new_revision.status.value,
            _revision_payload(new_revision),
            new_revision.created_at,
        )
        if insert_revision != "INSERT 0 1":
            raise PortContractError(
                resource="suite_gateway",
                field="qep_suite_revisions",
                reason="insert_suppressed",
            )
        update_status = await connection.execute(
            """
            UPDATE qep_suites
            SET version = $1
            WHERE id = $2
              AND version = $3
              AND status = 'active'
            """,
            suite.version,
            suite.id,
            expected.version,
        )
        if update_status != "UPDATE 1":
            raise VersionConflict(
                entity_type="suite",
                entity_id=suite.id,
                current_version=expected.version,
                expected_version=expected.version,
            )
        self._snapshot = SuiteMutationSnapshot(suite=suite)
        return ReplayResult(value=suite, replayed=False)

    async def _publish_retire(
        self, *, suite: Suite, expected: SuiteMutationSnapshot
    ) -> ReplayResult[Suite]:
        connection = self._require_connection()
        self._require_expected(expected)
        if suite.id != expected.suite_id:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="suite_id_mismatch"
            )
        if suite.status is not SuiteStatus.RETIRED or suite.retired_at is None:
            raise PortContractError(resource="suite_gateway", field="suite", reason="not_retired")
        if suite.version != expected.version + 1:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="version_not_monotonic"
            )
        if suite.revisions != expected.suite.revisions:
            raise PortContractError(
                resource="suite_gateway", field="suite", reason="history_rewritten"
            )
        update_status = await connection.execute(
            """
            UPDATE qep_suites
            SET status = 'retired',
                retired_at = $1,
                version = $2
            WHERE id = $3
              AND version = $4
              AND status = 'active'
            """,
            suite.retired_at,
            suite.version,
            suite.id,
            expected.version,
        )
        if update_status != "UPDATE 1":
            raise VersionConflict(
                entity_type="suite",
                entity_id=suite.id,
                current_version=expected.version,
                expected_version=expected.version,
            )
        self._snapshot = SuiteMutationSnapshot(suite=suite)
        return ReplayResult(value=suite, replayed=False)

    def _require_expected(self, expected: SuiteMutationSnapshot) -> None:
        if self._snapshot is None or self._snapshot != expected:
            current = self._snapshot
            raise VersionConflict(
                entity_type="suite",
                entity_id=expected.suite_id,
                current_version=0 if current is None else current.version,
                expected_version=expected.version,
            )

    def _require_connection(self) -> asyncpg.Connection:
        if self._connection is None:
            self._state_error("not_active")
        return self._connection

    def _state_error(self, reason: str) -> None:
        raise PortContractError(resource="suite_gateway", field="lifecycle", reason=reason)


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _digest_from_hex(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _revision_from_row(row: asyncpg.Record) -> SuiteRevision:
    return SuiteRevision(
        id=row["id"],
        suite_id=row["suite_id"],
        revision_no=row["revision_no"],
        source_spec_digest=_digest_from_hex(row["source_spec_digest"]),
        config_digest=_digest_from_hex(row["config_digest"]),
        framework=row["framework"],
        resource_profile_id=row["resource_profile_id"] or "profile-default",
        status=SuiteRevisionStatus(row["status"]),
        created_at=row["created_at"],
    )


def _revision_payload(revision: SuiteRevision) -> str:
    return json.dumps(
        {
            "schema_version": "qep.suite-revision.v1",
            "revision_id": revision.id,
            "suite_id": revision.suite_id,
            "revision_no": revision.revision_no,
            "source_spec_digest": revision.source_spec_digest.value,
            "config_digest": revision.config_digest.value,
            "framework": revision.framework,
            "resource_profile_id": revision.resource_profile_id,
            "status": revision.status.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
