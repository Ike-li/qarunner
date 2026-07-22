"""PostgreSQL Batch-local unit of work for immutable finalization."""

from __future__ import annotations

import json
from types import TracebackType
from typing import Self, cast

import asyncpg

from qarunner.application.ports.batch_finalization import (
    BATCH_FINALIZATION_BASIS_SCHEMA,
    BatchFinalizationIdentityScope,
    BatchFinalizationMutationSnapshot,
    BatchFinalizationProjection,
    BatchFinalizationPublication,
    BatchFinalizationSourceSnapshot,
    FinalizeBatchAuthority,
)
from qarunner.application.ports.batch_finalization_readiness import READINESS_SCHEMA
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityStateConflict,
)
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain.batch import BatchState
from qarunner.domain.batch_finalization import (
    BatchItemClassification,
    BatchItemResolution,
    BatchItemResolutionSet,
    BatchItemSourceKind,
    BatchNonRunResolutionRef,
    BatchSuccessPolicy,
    BatchTerminalRunRef,
    BatchUnknownFactRef,
)
from qarunner.domain.cancellation import (
    BatchCancellationResolutionKind,
    BatchCancellationScopeItem,
    canonicalize_batch_cancellation_scope_items,
)
from qarunner.domain.digest import Digest, canonical_digest, canonical_materialized_run_set_digest
from qarunner.domain.errors import IdempotencyConflict, VersionConflict
from qarunner.domain.run_finalization import RunItemKey, RunOutcome


class PostgresBatchFinalizationUnitOfWork:
    """One-shot caller-owned transaction implementing BatchFinalizationGateway."""

    def __init__(self, pool: asyncpg.Pool, *, authority: FinalizeBatchAuthority) -> None:
        self._pool = pool
        self._candidate_authority = authority
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._authority: FinalizeBatchAuthority | None = None
        self._snapshot: BatchFinalizationMutationSnapshot | None = None
        self._suite_id: str | None = None
        self._readiness_ref: str | None = None
        self._locked_batch_id: str | None = None
        self._locked_batch_version: int | None = None
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

    async def require_finalization_authority(self, *, batch_id: str) -> FinalizeBatchAuthority:
        connection = self._require_connection()
        if self._authority is not None:
            if self._authority.batch_id != batch_id:
                self._state_error("aggregate_already_locked")
            return self._authority
        candidate = self._candidate_authority
        if candidate.batch_id != batch_id:
            raise AuthorityPermissionDenied
        row = await connection.fetchrow(
            """
            SELECT
                batch.id,
                batch.version,
                batch.state,
                batch.write_epoch,
                batch.finalization_readiness_ref,
                revision.suite_id,
                readiness.source_batch_version AS readiness_source_batch_version,
                readiness.batch_version AS readiness_batch_version,
                readiness.manifest_digest AS readiness_manifest_digest,
                readiness.shard_plan_digest AS readiness_shard_plan_digest,
                readiness.canonical_run_set_digest AS readiness_run_set_digest,
                readiness.success_policy_digest AS readiness_policy_digest,
                readiness.readiness_digest,
                readiness.compatibility_epoch,
                readiness.state_model_version,
                readiness.payload AS readiness_payload
            FROM qep_batches AS batch
            JOIN qep_suite_revisions AS revision ON revision.id = batch.suite_revision_id
            JOIN qep_suites AS suite
              ON suite.id = revision.suite_id
             AND suite.project_id = batch.project_id
            LEFT JOIN qep_batch_finalization_readiness_facts AS readiness
              ON readiness.ref = batch.finalization_readiness_ref
             AND readiness.batch_id = batch.id
            WHERE batch.id = $1
            FOR UPDATE OF batch
            """,
            batch_id,
        )
        if row is None:
            raise AuthorityPermissionDenied
        if row["write_epoch"] != candidate.write_epoch:
            raise AuthorityStateConflict(reason="batch_finalization_authority_superseded")
        # A waiter needs a fresh READ COMMITTED snapshot after acquiring the Batch lock so it can
        # see a concurrent winner's basis, instead of retaining the pre-wait LEFT JOIN result that
        # `FOR UPDATE OF batch` alone does not refresh (mirrors
        # PostgresRunFinalizationUnitOfWork.require_finalization_authority's own fix for this).
        basis_row = await connection.fetchrow(
            """
            SELECT source_batch_version, batch_outcome, readiness_ref
            FROM qep_batch_finalization_bases
            WHERE batch_id = $1
            """,
            batch_id,
        )
        stored_source_version = None if basis_row is None else basis_row["source_batch_version"]
        if stored_source_version is None:
            if (
                row["state"] != BatchState.FINALIZING.value
                or row["version"] != candidate.source_batch_version
            ):
                raise AuthorityStateConflict(reason="batch_finalization_source_state_invalid")
        elif (
            stored_source_version != candidate.source_batch_version
            or row["version"] != candidate.source_batch_version + 1
            or row["state"] != basis_row["batch_outcome"]
            or row["state"] not in _TERMINAL_BATCH_STATES
            or basis_row["readiness_ref"] != row["finalization_readiness_ref"]
        ):
            raise AuthorityStateConflict(reason="stored_batch_finalization_binding_invalid")

        _validate_readiness_binding(row=row, expected=candidate.source_snapshot)

        self._locked_batch_id = batch_id
        self._locked_batch_version = row["version"]
        self._suite_id = row["suite_id"]
        self._readiness_ref = row["finalization_readiness_ref"]
        actual = await self._read_source_snapshot(expected=candidate.source_snapshot)
        if actual != candidate.source_snapshot:
            raise AuthorityStateConflict(reason="batch_finalization_source_snapshot_mismatch")
        self._authority = candidate
        return candidate

    async def lookup_stored(
        self, *, identity_scope: BatchFinalizationIdentityScope
    ) -> BatchFinalizationProjection | None:
        connection = self._require_connection()
        schema_version, batch_id, source_batch_version = identity_scope
        if schema_version != BATCH_FINALIZATION_BASIS_SCHEMA:
            raise PortContractError(
                resource="batch_finalization_identity",
                field="schema_version",
                reason="unsupported",
            )
        self._require_locked_batch(batch_id)
        row = await connection.fetchrow(
            """
            SELECT
                basis.source_batch_version,
                basis.manifest_digest,
                basis.shard_plan_digest,
                basis.canonical_run_set_digest,
                basis.batch_item_resolution_set_digest,
                basis.original_denominator,
                basis.batch_outcome,
                basis.basis_digest,
                basis.payload,
                basis.readiness_ref,
                batch.finalization_readiness_ref,
                batch.state,
                batch.version
            FROM qep_batch_finalization_bases AS basis
            JOIN qep_batches AS batch ON batch.id = basis.batch_id
            WHERE basis.batch_id = $1
              AND basis.source_batch_version = $2
            """,
            batch_id,
            source_batch_version,
        )
        if row is None:
            return None
        return _stored_projection(batch_id=batch_id, row=row)

    async def get_mutation_snapshot_for_update(
        self, *, batch_id: str
    ) -> BatchFinalizationMutationSnapshot:
        self._require_connection()
        authority = self._require_locked_batch(batch_id)
        actual = await self._read_source_snapshot(expected=authority.source_snapshot)
        if actual != authority.source_snapshot:
            raise AuthorityStateConflict(reason="batch_finalization_source_snapshot_mismatch")
        self._snapshot = BatchFinalizationMutationSnapshot(
            batch_id=batch_id,
            batch_version=cast(int, self._locked_batch_version),
            state=BatchState.FINALIZING,
            source_snapshot=actual,
        )
        return self._snapshot

    async def publish_finalization(
        self, *, publication: BatchFinalizationPublication
    ) -> ReplayResult[BatchFinalizationProjection]:
        try:
            return await self._publish_finalization(publication=publication)
        except BaseException:
            self._aborted = True
            raise

    async def _publish_finalization(
        self, *, publication: BatchFinalizationPublication
    ) -> ReplayResult[BatchFinalizationProjection]:
        connection = self._require_connection()
        authority = self._require_locked_batch(publication.projection.batch_id)
        if authority != publication.authority:
            raise AuthorityStateConflict(reason="publication_authority_superseded")

        stored = await connection.fetchrow(
            """
            SELECT
                basis.source_batch_version,
                basis.manifest_digest,
                basis.shard_plan_digest,
                basis.canonical_run_set_digest,
                basis.batch_item_resolution_set_digest,
                basis.original_denominator,
                basis.batch_outcome,
                basis.basis_digest,
                basis.payload,
                basis.readiness_ref,
                batch.finalization_readiness_ref,
                batch.state,
                batch.version
            FROM qep_batch_finalization_bases AS basis
            JOIN qep_batches AS batch ON batch.id = basis.batch_id
            WHERE basis.batch_id = $1
            """,
            publication.projection.batch_id,
        )
        if stored is not None:
            projection = _stored_projection(
                batch_id=publication.projection.batch_id,
                row=stored,
            )
            if (
                projection.source_batch_version == publication.authority.source_batch_version
                and projection.basis_digest == publication.basis.basis_digest
            ):
                return ReplayResult(value=projection, replayed=True)
            raise IdempotencyConflict(
                scope=f"batch:{publication.projection.batch_id}:finalization",
                key=str(publication.authority.source_batch_version),
                stored_digest=projection.basis_digest,
                received_digest=publication.basis.basis_digest,
            )

        if self._snapshot != publication.expected_snapshot:
            current_version = 0 if self._snapshot is None else self._snapshot.batch_version
            raise VersionConflict(
                entity_type="batch",
                entity_id=publication.projection.batch_id,
                current_version=current_version,
                expected_version=publication.expected_snapshot.batch_version,
            )
        actual = await self._read_source_snapshot(expected=authority.source_snapshot)
        if actual != authority.source_snapshot:
            raise AuthorityStateConflict(reason="batch_finalization_source_snapshot_mismatch")

        await self._insert_item_resolutions(publication)
        await self._insert_basis(publication)
        await self._publish_batch_projection(publication)
        await self._insert_audit(publication)
        await self._insert_outbox(publication)
        return ReplayResult(value=publication.projection, replayed=False)

    async def _read_source_snapshot(
        self,
        *,
        expected: BatchFinalizationSourceSnapshot,
    ) -> BatchFinalizationSourceSnapshot:
        try:
            return await self._rebuild_source_snapshot(expected=expected)
        except AuthorityStateConflict:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise AuthorityStateConflict(
                reason="batch_finalization_source_integrity_invalid"
            ) from error

    async def _rebuild_source_snapshot(
        self,
        *,
        expected: BatchFinalizationSourceSnapshot,
    ) -> BatchFinalizationSourceSnapshot:
        connection = self._require_connection()
        suite_id = cast(str, self._suite_id)

        manifest = await connection.fetchrow(
            """
            SELECT id, digest, item_count, status
            FROM qep_case_manifests
            WHERE id = $1 AND batch_id = $2
            """,
            expected.manifest_id,
            expected.batch_id,
        )
        item_rows = await connection.fetch(
            """
            SELECT item_index
            FROM qep_manifest_items
            WHERE manifest_id = $1
            ORDER BY item_index
            """,
            expected.manifest_id,
        )
        plan = await connection.fetchrow(
            """
            SELECT id, digest, version, status
            FROM qep_shard_plans
            WHERE id = $1 AND batch_id = $2
            """,
            expected.shard_plan_id,
            expected.batch_id,
        )
        policy_row = await connection.fetchrow(
            """
            SELECT
                id, policy_version, suite_id, max_test_failed_items,
                allow_authorized_retry_pass, policy_digest, payload
            FROM qep_batch_success_policies
            WHERE id = $1 AND policy_version = $2 AND suite_id = $3
            """,
            expected.success_policy_id,
            expected.success_policy_version,
            suite_id,
        )
        if (
            manifest is None
            or manifest["status"] != "approved"
            or plan is None
            or plan["status"] != "approved"
            or policy_row is None
        ):
            raise AuthorityStateConflict(reason="batch_finalization_source_missing")

        manifest_digest = _digest(manifest["digest"])
        shard_plan_digest = _digest(plan["digest"])
        expected_item_keys = tuple(
            RunItemKey(expected.manifest_id, row["item_index"]) for row in item_rows
        )
        if manifest["item_count"] != len(expected_item_keys):
            raise AuthorityStateConflict(reason="batch_finalization_manifest_coverage_invalid")
        policy = _policy_from_row(policy_row)

        run_rows = await connection.fetch(
            """
            SELECT
                run.id,
                run.orchestration_phase,
                run.outcome AS projected_outcome,
                run.finalization_basis_digest,
                basis.source_run_version,
                basis.item_resolution_set_digest,
                basis.outcome,
                basis.basis_digest,
                basis.payload
            FROM qep_runs AS run
            LEFT JOIN qep_run_finalization_bases AS basis ON basis.run_id = run.id
            WHERE run.batch_id = $1
            ORDER BY run.id
            """,
            expected.batch_id,
        )
        run_ids = tuple(row["id"] for row in run_rows)
        canonical_run_set_digest = canonical_materialized_run_set_digest(
            batch_id=expected.batch_id,
            run_ids=run_ids,
        )
        terminal_refs: list[BatchTerminalRunRef] = []
        terminal_by_run: dict[str, BatchTerminalRunRef] = {}
        for row in run_rows:
            ref = _terminal_run_ref(row, batch_id=expected.batch_id)
            terminal_refs.append(ref)
            terminal_by_run[ref.run_id] = ref

        intent_rows = await connection.fetch(
            """
            SELECT
                intent_digest, scope_kind, manifest_digest, shard_plan_version,
                shard_plan_digest, canonical_run_set_digest
            FROM qep_batch_cancellation_intents
            WHERE batch_id = $1
            ORDER BY id
            LIMIT 2
            """,
            expected.batch_id,
        )
        if len(intent_rows) > 1:
            raise AuthorityStateConflict(reason="stored_cancellation_cardinality_invalid")
        cancellation_intent_digest = (
            None if not intent_rows else _digest(intent_rows[0]["intent_digest"])
        )
        if intent_rows:
            intent = intent_rows[0]
            if (
                intent["scope_kind"] != "frozen_plan"
                or _digest(intent["manifest_digest"]) != manifest_digest
                or intent["shard_plan_version"] != plan["version"]
                or _digest(intent["shard_plan_digest"]) != shard_plan_digest
                or _digest(intent["canonical_run_set_digest"]) != canonical_run_set_digest
            ):
                raise AuthorityStateConflict(
                    reason="batch_finalization_cancellation_source_invalid"
                )

        scope_rows = await connection.fetch(
            """
            SELECT
                batch_id, cancellation_intent_digest, manifest_id, item_index,
                resolution_kind, run_id, source_run_version, scope_item_digest,
                payload, recorded_at
            FROM qep_batch_cancellation_scope_items
            WHERE batch_id = $1
            ORDER BY manifest_id, item_index
            """,
            expected.batch_id,
        )
        scope_items = tuple(
            _scope_item_from_row(
                row,
                manifest_digest=manifest_digest,
                shard_plan_version=plan["version"],
                shard_plan_digest=shard_plan_digest,
            )
            for row in scope_rows
        )
        if cancellation_intent_digest is None:
            if scope_items:
                raise AuthorityStateConflict(
                    reason="batch_finalization_cancellation_source_invalid"
                )
        else:
            scope_items = canonicalize_batch_cancellation_scope_items(
                batch_id=expected.batch_id,
                batch_cancellation_intent_digest=cancellation_intent_digest,
                manifest_id=expected.manifest_id,
                manifest_digest=manifest_digest,
                shard_plan_version=plan["version"],
                shard_plan_digest=shard_plan_digest,
                expected_item_keys=expected_item_keys,
                items=scope_items,
            )

        run_resolution_rows = await connection.fetch(
            """
            SELECT
                resolution.run_id,
                resolution.manifest_id,
                resolution.item_index,
                resolution.source_run_version,
                resolution.unknown_lineage_digest,
                resolution.aggregation_class,
                resolution.item_resolution_digest,
                resolution.payload
            FROM qep_run_item_resolutions AS resolution
            JOIN qep_runs AS run ON run.id = resolution.run_id
            WHERE run.batch_id = $1
            ORDER BY resolution.manifest_id, resolution.item_index, resolution.run_id
            """,
            expected.batch_id,
        )
        entries: dict[RunItemKey, BatchItemResolution] = {}
        unknown_refs: list[BatchUnknownFactRef] = []
        for row in run_resolution_rows:
            ref = terminal_by_run[row["run_id"]]
            payload = _json_object(row["payload"])
            item_key = RunItemKey(row["manifest_id"], row["item_index"])
            if (
                payload.get("item_key") != item_key.canonical_payload()
                or payload.get("aggregation_class") != row["aggregation_class"]
                or row["source_run_version"] != ref.source_run_version
                or canonical_digest(
                    schema_version="qep.run-item-resolution-set.v1",
                    payload={"projection_kind": "item", **payload},
                )
                != _digest(row["item_resolution_digest"])
            ):
                raise AuthorityStateConflict(reason="batch_finalization_run_resolution_invalid")
            entry = BatchItemResolution(
                item_key=item_key,
                source_kind=BatchItemSourceKind.RUN_RESOLUTION,
                source_run_id=ref.run_id,
                source_run_version=ref.source_run_version,
                source_run_basis_digest=ref.run_basis_digest,
                source_run_item_resolution_set_digest=ref.item_resolution_set_digest,
                source_item_resolution_digest=_digest(row["item_resolution_digest"]),
                not_executed_fact_schema=None,
                not_executed_fact_digest=None,
                cancellation_scope_item_digest=None,
                classification=BatchItemClassification(row["aggregation_class"]),
            )
            entries[item_key] = entry
            if row["unknown_lineage_digest"] is not None:
                effective = payload.get("effective")
                if not isinstance(effective, dict):
                    raise AuthorityStateConflict(
                        reason="batch_finalization_unknown_source_invalid"
                    )
                unknown_refs.append(
                    BatchUnknownFactRef(
                        item_key=item_key,
                        source_item_resolution_digest=entry.source_item_resolution_digest,
                        unknown_lineage_digest=_digest(row["unknown_lineage_digest"]),
                        adjudication_digest=_digest_value(effective["adjudication_digest"]),
                    )
                )

        non_run_refs: list[BatchNonRunResolutionRef] = []
        for item in scope_items:
            if item.resolution_kind is BatchCancellationResolutionKind.NOT_EXECUTED:
                entry = BatchItemResolution.from_not_executed(fact=item)
                if entry.item_key in entries:
                    raise AuthorityStateConflict(reason="batch_finalization_item_overlap")
                entries[entry.item_key] = entry
                non_run_refs.append(BatchNonRunResolutionRef.from_scope_item(fact=item))
                continue
            run_entry = entries.get(item.manifest_item_key)
            if (
                run_entry is None
                or run_entry.source_run_id != item.run_id
                or run_entry.source_run_version != item.source_run_version
            ):
                raise AuthorityStateConflict(reason="batch_finalization_run_fanout_invalid")

        resolution_set = BatchItemResolutionSet.build(
            batch_id=expected.batch_id,
            source_batch_version=expected.source_batch_version,
            manifest_id=manifest["id"],
            manifest_digest=manifest_digest,
            shard_plan_id=plan["id"],
            shard_plan_version=plan["version"],
            shard_plan_digest=shard_plan_digest,
            canonical_run_set_digest=canonical_run_set_digest,
            expected_item_keys=expected_item_keys,
            entries=tuple(entries.values()),
        )
        terminal_refs_tuple = tuple(sorted(terminal_refs, key=lambda ref: ref.run_id))
        non_run_refs_tuple = tuple(sorted(non_run_refs, key=lambda ref: ref.item_key))
        unknown_refs_tuple = tuple(sorted(unknown_refs, key=lambda ref: ref.item_key))
        return BatchFinalizationSourceSnapshot(
            batch_id=expected.batch_id,
            source_batch_version=expected.source_batch_version,
            manifest_id=manifest["id"],
            manifest_digest=manifest_digest,
            item_count=manifest["item_count"],
            shard_plan_id=plan["id"],
            shard_plan_version=plan["version"],
            shard_plan_digest=shard_plan_digest,
            canonical_run_set_digest=canonical_run_set_digest,
            success_policy_id=policy.policy_id,
            success_policy_version=policy.policy_version,
            success_policy_digest=policy.policy_digest,
            batch_item_resolution_set_digest=resolution_set.resolution_set_digest,
            completeness_proof_digest=_completeness_proof_digest(
                resolution_set=resolution_set,
                terminal_run_refs=terminal_refs_tuple,
                unknown_fact_refs=unknown_refs_tuple,
            ),
            batch_cancellation_intent_digest=cancellation_intent_digest,
            terminal_run_refs=terminal_refs_tuple,
            non_run_refs=non_run_refs_tuple,
            unknown_fact_refs=unknown_refs_tuple,
        )

    async def _insert_item_resolutions(
        self,
        publication: BatchFinalizationPublication,
    ) -> None:
        connection = self._require_connection()
        batch_id = publication.basis.batch_id
        entries = publication.basis.resolution_set.entries
        values = [
            (
                batch_id,
                entry.item_key.manifest_id,
                entry.item_key.item_index,
                entry.source_kind.value,
                entry.source_run_id,
                _optional_digest_hex(entry.source_run_basis_digest),
                _optional_digest_hex(entry.source_item_resolution_digest),
                _optional_digest_hex(entry.not_executed_fact_digest),
                entry.classification.value,
                _digest_hex(entry.batch_item_resolution_digest),
                _json(entry.canonical_payload()),
            )
            for entry in entries
        ]
        await connection.executemany(
            """
            INSERT INTO qep_batch_item_resolutions (
                batch_id, manifest_id, item_index, source_kind,
                source_run_id, source_run_basis_digest, source_item_resolution_digest,
                not_executed_fact_digest, classification, resolution_digest,
                payload, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                transaction_timestamp()
            )
            """,
            values,
        )
        # executemany reports no per-row status, unlike the single-row execute() this replaced,
        # so the "silently swallowed insert" check (a BEFORE INSERT trigger returning NULL) has
        # to be a post-insert count comparison instead of a per-row "INSERT 0 1" check.
        inserted = await connection.fetchval(
            "SELECT count(*) FROM qep_batch_item_resolutions WHERE batch_id = $1", batch_id
        )
        if inserted != len(entries):
            raise AuthorityStateConflict(reason="batch_item_resolution_write_missing")

    async def _insert_basis(self, publication: BatchFinalizationPublication) -> None:
        basis = publication.basis
        status = await self._require_connection().execute(
            """
            INSERT INTO qep_batch_finalization_bases (
                id, batch_id, source_batch_version, manifest_digest, shard_plan_digest,
                canonical_run_set_digest, batch_item_resolution_set_digest,
                original_denominator, batch_outcome, basis_digest, payload,
                readiness_ref, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, transaction_timestamp()
            )
            """,
            f"batch-finalization-basis-{_digest_hex(basis.basis_digest)}",
            basis.batch_id,
            basis.source_batch_version,
            _digest_hex(basis.manifest_digest),
            _digest_hex(basis.shard_plan_digest),
            _digest_hex(basis.canonical_run_set_digest),
            _digest_hex(basis.batch_item_resolution_set_digest),
            basis.counts.original_denominator,
            basis.batch_outcome.value,
            _digest_hex(basis.basis_digest),
            _json(basis.canonical_payload()),
            self._readiness_ref,
        )
        _require_inserted(status, reason="batch_finalization_basis_write_missing")

    async def _publish_batch_projection(
        self,
        publication: BatchFinalizationPublication,
    ) -> None:
        status = await self._require_connection().execute(
            """
            UPDATE qep_batches
            SET state = $1,
                version = version + 1,
                updated_at = transaction_timestamp()
            WHERE id = $2
              AND state = 'finalizing'
              AND version = $3
              AND write_epoch = $4
            """,
            publication.projection.outcome.value,
            publication.projection.batch_id,
            publication.expected_snapshot.batch_version,
            publication.authority.write_epoch,
        )
        if status != "UPDATE 1":
            current_version = (
                publication.expected_snapshot.batch_version
                if self._snapshot is None
                else self._snapshot.batch_version
            )
            raise VersionConflict(
                entity_type="batch",
                entity_id=publication.projection.batch_id,
                current_version=current_version,
                expected_version=publication.expected_snapshot.batch_version,
            )

    async def _insert_audit(self, publication: BatchFinalizationPublication) -> None:
        side_effect = publication.side_effect
        payload = {
            "schema_version": "qep.batch-finalization-audit.v1",
            "batch_id": side_effect.batch_id,
            "source_batch_version": publication.projection.source_batch_version,
            "batch_version": publication.projection.batch_version,
            "basis_digest": side_effect.basis_digest.value,
            "outcome": side_effect.outcome.value,
            "authority_digest": publication.authority.authority_digest.value,
            "write_epoch": publication.authority.write_epoch,
        }
        status = await self._require_connection().execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES (
                $1, 'system', 'finalize_batch', 'batch', $2, 'allowed',
                'batch_finalization_basis_committed', NULL, $3, $4,
                transaction_timestamp()
            )
            """,
            f"batch-finalization-audit-{_digest_hex(side_effect.basis_digest)}",
            side_effect.batch_id,
            _digest_hex(side_effect.basis_digest),
            _json(payload),
        )
        _require_inserted(status, reason="batch_finalization_audit_write_missing")

    async def _insert_outbox(self, publication: BatchFinalizationPublication) -> None:
        payload = _finalized_event_payload(publication)
        identity = canonical_digest(
            schema_version="qep.batch-finalized-identity.v1",
            payload={
                "batch_id": publication.projection.batch_id,
                "source_batch_version": publication.projection.source_batch_version,
                "basis_digest": publication.projection.basis_digest.value,
            },
        )
        event_id = f"batch-finalized-event-{_digest_hex(identity)}"
        payload_digest = canonical_digest(
            schema_version="qep.batch-finalized-event-payload.v1",
            payload=payload,
        )
        status = await self._require_connection().execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, status, available_at, attempts, created_at
            ) VALUES (
                $1, $1, 'batch', $2, 'batch.finalized.v1', $3, $4, 'pending',
                transaction_timestamp(), 0, transaction_timestamp()
            )
            """,
            event_id,
            publication.projection.batch_id,
            _digest_hex(payload_digest),
            _json(payload),
        )
        _require_inserted(status, reason="batch_finalization_outbox_write_missing")

    def _require_connection(self) -> asyncpg.Connection:
        if self._aborted:
            self._state_error("aborted")
        if self._connection is None:
            self._state_error("not_active" if not self._closed else "closed")
        return self._connection

    def _require_locked_batch(self, batch_id: str) -> FinalizeBatchAuthority:
        if (
            self._locked_batch_id != batch_id
            or self._authority is None
            or self._authority.batch_id != batch_id
        ):
            self._state_error("authority_not_locked")
        return self._authority

    @staticmethod
    def _state_error(reason: str) -> None:
        raise PortContractError(resource="unit_of_work", field="state", reason=reason)


_TERMINAL_BATCH_STATES = frozenset(
    {
        BatchState.SUCCEEDED.value,
        BatchState.FAILED.value,
        BatchState.PARTIAL.value,
        BatchState.CANCELLED.value,
    }
)


def _terminal_run_ref(row: asyncpg.Record, *, batch_id: str) -> BatchTerminalRunRef:
    payload = _json_object(row["payload"])
    required = (
        "run_id",
        "batch_id",
        "source_run_version",
        "run_item_set_digest",
        "original_resolution_set_digest",
        "effective_resolution_set_digest",
        "item_resolution_set_digest",
        "outcome",
    )
    if any(field not in payload for field in required):
        raise AuthorityStateConflict(reason="stored_run_finalization_integrity_invalid")
    basis_digest = canonical_digest(
        schema_version="qep.run-finalization-basis.v1",
        payload=payload,
    )
    if (
        row["orchestration_phase"] != "closed"
        or row["basis_digest"] is None
        or row["finalization_basis_digest"] != row["basis_digest"]
        or row["projected_outcome"] != row["outcome"]
        or payload["run_id"] != row["id"]
        or payload["batch_id"] != batch_id
        or payload["source_run_version"] != row["source_run_version"]
        or payload["item_resolution_set_digest"]
        != _digest(row["item_resolution_set_digest"]).value
        or payload["outcome"] != row["outcome"]
        or basis_digest != _digest(row["basis_digest"])
    ):
        raise AuthorityStateConflict(reason="stored_run_finalization_integrity_invalid")
    return BatchTerminalRunRef(
        run_id=row["id"],
        source_run_version=row["source_run_version"],
        run_basis_digest=basis_digest,
        run_outcome=RunOutcome(row["outcome"]),
        run_item_set_digest=_digest_value(payload["run_item_set_digest"]),
        original_resolution_set_digest=_digest_value(payload["original_resolution_set_digest"]),
        effective_resolution_set_digest=_digest_value(payload["effective_resolution_set_digest"]),
        item_resolution_set_digest=_digest(row["item_resolution_set_digest"]),
    )


def _scope_item_from_row(
    row: asyncpg.Record,
    *,
    manifest_digest: Digest,
    shard_plan_version: int,
    shard_plan_digest: Digest,
) -> BatchCancellationScopeItem:
    item = BatchCancellationScopeItem(
        batch_id=row["batch_id"],
        batch_cancellation_intent_digest=_digest(row["cancellation_intent_digest"]),
        manifest_id=row["manifest_id"],
        manifest_digest=manifest_digest,
        manifest_item_key=RunItemKey(row["manifest_id"], row["item_index"]),
        shard_plan_version=shard_plan_version,
        shard_plan_digest=shard_plan_digest,
        resolution_kind=BatchCancellationResolutionKind(row["resolution_kind"]),
        run_id=row["run_id"],
        source_run_version=row["source_run_version"],
        recorded_at=row["recorded_at"],
    )
    if (
        _digest(row["scope_item_digest"]) != item.scope_item_digest
        or _json_object(row["payload"]) != item.canonical_payload()
    ):
        raise AuthorityStateConflict(reason="stored_cancellation_scope_integrity_invalid")
    return item


def _policy_from_row(row: asyncpg.Record) -> BatchSuccessPolicy:
    payload = _json_object(row["payload"])
    policy = BatchSuccessPolicy(
        schema_version=payload["schema_version"],
        policy_id=payload["policy_id"],
        policy_version=payload["policy_version"],
        suite_id=payload["suite_id"],
        max_test_failed_items=payload["max_test_failed_items"],
        allow_authorized_retry_pass=payload["allow_authorized_retry_pass"],
        allowed_test_failure_selector_digest=_optional_digest_value(
            payload["allowed_test_failure_selector_digest"]
        ),
        result_mapping_schema=payload["result_mapping_schema"],
        result_mapping_version=payload["result_mapping_version"],
        result_mapping_digest=_digest_value(payload["result_mapping_digest"]),
        approval_record_digest=_digest_value(payload["approval_record_digest"]),
    )
    if (
        row["id"] != policy.policy_id
        or row["policy_version"] != policy.policy_version
        or row["suite_id"] != policy.suite_id
        or row["max_test_failed_items"] != policy.max_test_failed_items
        or row["allow_authorized_retry_pass"] != policy.allow_authorized_retry_pass
        or _digest(row["policy_digest"]) != policy.policy_digest
        or payload != _policy_payload(policy)
    ):
        raise AuthorityStateConflict(reason="stored_batch_success_policy_integrity_invalid")
    return policy


def _policy_payload(policy: BatchSuccessPolicy) -> dict[str, object]:
    return {
        "schema_version": policy.schema_version,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "suite_id": policy.suite_id,
        "max_test_failed_items": policy.max_test_failed_items,
        "allow_authorized_retry_pass": policy.allow_authorized_retry_pass,
        "allowed_test_failure_selector_digest": _optional_digest_value_text(
            policy.allowed_test_failure_selector_digest
        ),
        "result_mapping_schema": policy.result_mapping_schema,
        "result_mapping_version": policy.result_mapping_version,
        "result_mapping_digest": policy.result_mapping_digest.value,
        "approval_record_digest": policy.approval_record_digest.value,
    }


def _completeness_proof_digest(
    *,
    resolution_set: BatchItemResolutionSet,
    terminal_run_refs: tuple[BatchTerminalRunRef, ...],
    unknown_fact_refs: tuple[BatchUnknownFactRef, ...],
) -> Digest:
    unknown_by_key = {ref.item_key: ref for ref in unknown_fact_refs}
    item_bindings = []
    for entry in resolution_set.entries:
        unknown = unknown_by_key.get(entry.item_key)
        item_bindings.append(
            {
                **entry.canonical_payload(),
                "batch_item_resolution_digest": entry.batch_item_resolution_digest.value,
                "unknown_lineage_digest": (
                    None if unknown is None else unknown.unknown_lineage_digest.value
                ),
                "adjudication_digest": (
                    None if unknown is None else unknown.adjudication_digest.value
                ),
            }
        )
    return canonical_digest(
        schema_version="qep.batch-completeness-proof.v1",
        payload={
            "batch_id": resolution_set.batch_id,
            "source_batch_version": resolution_set.source_batch_version,
            "manifest_id": resolution_set.manifest_id,
            "manifest_digest": resolution_set.manifest_digest.value,
            "item_count": resolution_set.item_count,
            "shard_plan_id": resolution_set.shard_plan_id,
            "shard_plan_version": resolution_set.shard_plan_version,
            "shard_plan_digest": resolution_set.shard_plan_digest.value,
            "canonical_run_set_digest": resolution_set.canonical_run_set_digest.value,
            "batch_item_resolution_set_digest": resolution_set.resolution_set_digest.value,
            "item_bindings": item_bindings,
            "terminal_run_refs": [ref.canonical_payload() for ref in terminal_run_refs],
        },
    )


def _stored_projection(*, batch_id: str, row: asyncpg.Record) -> BatchFinalizationProjection:
    try:
        return _validated_stored_projection(batch_id=batch_id, row=row)
    except AuthorityStateConflict:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise AuthorityStateConflict(
            reason="stored_batch_finalization_integrity_invalid"
        ) from error


def _validate_readiness_binding(
    *, expected: BatchFinalizationSourceSnapshot, row: asyncpg.Record
) -> None:
    ref = row["finalization_readiness_ref"]
    if not ref or row["readiness_payload"] is None:
        raise AuthorityStateConflict(reason="batch_finalization_readiness_binding_missing")
    if expected.source_batch_version < 1:
        raise AuthorityStateConflict(reason="batch_finalization_readiness_binding_invalid")
    payload = _json_object(row["readiness_payload"])
    expected_run_basis_digests = [
        value.run_basis_digest.value for value in expected.terminal_run_refs
    ]
    required = {
        "batch_id",
        "source_batch_version",
        "manifest_id",
        "manifest_digest",
        "item_count",
        "shard_plan_id",
        "shard_plan_version",
        "shard_plan_digest",
        "canonical_run_set_digest",
        "success_policy_digest",
        "batch_cancellation_intent_digest",
        "run_basis_digests",
        "pending_retry_intents",
        "attempt_creation_opportunities",
    }
    if not required.issubset(payload):
        raise AuthorityStateConflict(reason="batch_finalization_readiness_integrity_invalid")
    readiness_digest = canonical_digest(schema_version=READINESS_SCHEMA, payload=payload)
    if (
        row["readiness_source_batch_version"] != expected.source_batch_version - 1
        or row["readiness_batch_version"] != expected.source_batch_version
        or row["readiness_manifest_digest"] != _digest_hex(expected.manifest_digest)
        or row["readiness_shard_plan_digest"] != _digest_hex(expected.shard_plan_digest)
        or row["readiness_run_set_digest"] != _digest_hex(expected.canonical_run_set_digest)
        or row["readiness_policy_digest"] != _digest_hex(expected.success_policy_digest)
        or row["readiness_digest"] != _digest_hex(readiness_digest)
        or row["compatibility_epoch"] != "M0-STATE-V1"
        or row["state_model_version"] != 1
        or payload["batch_id"] != expected.batch_id
        or payload["source_batch_version"] != expected.source_batch_version - 1
        or payload["manifest_id"] != expected.manifest_id
        or payload["manifest_digest"] != expected.manifest_digest.value
        or payload["item_count"] != expected.item_count
        or payload["shard_plan_id"] != expected.shard_plan_id
        or payload["shard_plan_version"] != expected.shard_plan_version
        or payload["shard_plan_digest"] != expected.shard_plan_digest.value
        or payload["canonical_run_set_digest"] != expected.canonical_run_set_digest.value
        or payload["success_policy_digest"] != expected.success_policy_digest.value
        or payload["batch_cancellation_intent_digest"]
        != _optional_digest_value_text(expected.batch_cancellation_intent_digest)
        or payload["run_basis_digests"] != expected_run_basis_digests
        or payload["pending_retry_intents"] != []
        or payload["attempt_creation_opportunities"] != []
        or ref != f"batch-finalization-readiness-{_digest_hex(readiness_digest)}"
    ):
        raise AuthorityStateConflict(reason="batch_finalization_readiness_binding_invalid")


def _validated_stored_projection(
    *, batch_id: str, row: asyncpg.Record
) -> BatchFinalizationProjection:
    payload = _json_object(row["payload"])
    basis_digest = canonical_digest(
        schema_version=BATCH_FINALIZATION_BASIS_SCHEMA,
        payload=payload,
    )
    required = (
        "batch_id",
        "source_batch_version",
        "manifest_digest",
        "shard_plan_digest",
        "canonical_run_set_digest",
        "batch_item_resolution_set_digest",
        "original_denominator",
        "batch_outcome",
    )
    if any(field not in payload for field in required) or (
        payload["batch_id"] != batch_id
        or payload["source_batch_version"] != row["source_batch_version"]
        or payload["manifest_digest"] != _digest(row["manifest_digest"]).value
        or payload["shard_plan_digest"] != _digest(row["shard_plan_digest"]).value
        or payload["canonical_run_set_digest"] != _digest(row["canonical_run_set_digest"]).value
        or payload["batch_item_resolution_set_digest"]
        != _digest(row["batch_item_resolution_set_digest"]).value
        or payload["original_denominator"] != row["original_denominator"]
        or payload["batch_outcome"] != row["batch_outcome"]
        or basis_digest != _digest(row["basis_digest"])
        or row["readiness_ref"] != row["finalization_readiness_ref"]
        or row["state"] != row["batch_outcome"]
        or row["version"] != row["source_batch_version"] + 1
    ):
        raise AuthorityStateConflict(reason="stored_batch_finalization_integrity_invalid")
    return BatchFinalizationProjection(
        batch_id=batch_id,
        source_batch_version=row["source_batch_version"],
        batch_version=row["version"],
        outcome=BatchState(row["batch_outcome"]),
        basis_digest=basis_digest,
    )


def _finalized_event_payload(
    publication: BatchFinalizationPublication,
) -> dict[str, object]:
    projection = publication.projection
    return {
        "schema_version": "qep.batch-finalized.v1",
        "batch_id": projection.batch_id,
        "source_batch_version": projection.source_batch_version,
        "batch_version": projection.batch_version,
        "basis_digest": projection.basis_digest.value,
        "outcome": projection.outcome.value,
        "authority_digest": publication.authority.authority_digest.value,
        "write_epoch": publication.authority.write_epoch,
    }


def _json_object(value: str) -> dict[str, object]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("stored payload must be an object")
    return decoded


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _require_inserted(status: str, *, reason: str) -> None:
    if status != "INSERT 0 1":
        raise AuthorityStateConflict(reason=reason)


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _optional_digest_hex(value: Digest | None) -> str | None:
    return None if value is None else _digest_hex(value)


def _digest(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _digest_value(value: object) -> Digest:
    if not isinstance(value, str):
        raise ValueError("stored digest must be a string")
    return Digest(value)


def _optional_digest_value(value: object) -> Digest | None:
    return None if value is None else _digest_value(value)


def _optional_digest_value_text(value: Digest | None) -> str | None:
    return None if value is None else value.value
