"""PostgreSQL unit of work for the fact-aware Batch finalization boundary."""

from __future__ import annotations

import json
from types import TracebackType
from typing import Self, cast

import asyncpg

from qarunner.application.batch_finalization_migration_compatibility import (
    COMPATIBILITY_EPOCH,
    STATE_MODEL_VERSION,
)
from qarunner.application.ports.batch_finalization_readiness import (
    READINESS_SCHEMA,
    BatchFinalizationReadinessAuthority,
    BatchFinalizationReadinessGateway,
    BatchFinalizationReadinessIdentityScope,
    BatchFinalizationReadinessMutationSnapshot,
    BatchFinalizationReadinessProjection,
    BatchFinalizationReadinessPublication,
    BatchFinalizationReadinessSnapshot,
)
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityStateConflict,
)
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain.batch import BatchState
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import IdempotencyConflict, VersionConflict


class PostgresBatchFinalizationReadinessUnitOfWork(BatchFinalizationReadinessGateway):
    """One-shot caller-owned transaction for ``RUNNING -> FINALIZING``."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        authority: BatchFinalizationReadinessAuthority,
    ) -> None:
        self._pool = pool
        self._candidate_authority = authority
        self._connection: asyncpg.Connection | None = None
        self._transaction: asyncpg.Transaction | None = None
        self._authority: BatchFinalizationReadinessAuthority | None = None
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

    async def require_readiness_authority(
        self, *, batch_id: str
    ) -> BatchFinalizationReadinessAuthority:
        connection = self._require_connection()
        if self._authority is not None:
            if self._authority.snapshot.batch_id != batch_id:
                self._state_error("aggregate_already_locked")
            return self._authority

        candidate = self._candidate_authority
        if candidate.snapshot.batch_id != batch_id:
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
                fact.ref AS stored_ref,
                fact.source_batch_version AS stored_source_batch_version,
                fact.readiness_digest AS stored_readiness_digest,
                fact.authority_digest AS stored_authority_digest,
                fact.write_epoch AS stored_write_epoch,
                fact.compatibility_epoch AS stored_compatibility_epoch,
                fact.state_model_version AS stored_state_model_version,
                fact.payload AS stored_payload
            FROM qep_batches AS batch
            JOIN qep_suite_revisions AS revision ON revision.id = batch.suite_revision_id
            JOIN qep_suites AS suite
              ON suite.id = revision.suite_id
             AND suite.project_id = batch.project_id
            LEFT JOIN qep_batch_finalization_readiness_facts AS fact
              ON fact.ref = batch.finalization_readiness_ref
             AND fact.batch_id = batch.id
            WHERE batch.id = $1
            FOR UPDATE OF batch
            """,
            batch_id,
        )
        if row is None:
            raise AuthorityPermissionDenied
        if row["write_epoch"] != candidate.write_epoch:
            raise AuthorityStateConflict(reason="batch_readiness_authority_superseded")

        stored_ref = row["stored_ref"]
        if stored_ref is None:
            if (
                row["state"] != BatchState.RUNNING.value
                or row["version"] != candidate.snapshot.source_batch_version
            ):
                raise AuthorityStateConflict(reason="batch_readiness_source_state_invalid")
        else:
            self._validate_stored_fact_binding(row=row, candidate=candidate)

        self._locked_batch_id = batch_id
        self._locked_batch_version = row["version"]
        await self._read_source_snapshot(expected=candidate.snapshot)
        self._authority = candidate
        return candidate

    async def lookup_stored(
        self, *, identity_scope: BatchFinalizationReadinessIdentityScope
    ) -> BatchFinalizationReadinessProjection | None:
        connection = self._require_connection()
        schema_version, batch_id, source_batch_version = identity_scope
        if schema_version != READINESS_SCHEMA:
            raise PortContractError(
                resource="batch_finalization_readiness_identity",
                field="schema_version",
                reason="unsupported",
            )
        self._require_locked_batch(batch_id)
        row = await connection.fetchrow(
            """
            SELECT
                fact.ref,
                fact.batch_id,
                fact.source_batch_version,
                fact.batch_version,
                fact.readiness_digest,
                fact.payload,
                batch.state,
                batch.version,
                batch.finalization_readiness_ref
            FROM qep_batch_finalization_readiness_facts AS fact
            JOIN qep_batches AS batch ON batch.id = fact.batch_id
            WHERE fact.batch_id = $1
              AND fact.source_batch_version = $2
            """,
            batch_id,
            source_batch_version,
        )
        if row is None:
            return None
        return _stored_projection(batch_id=batch_id, row=row)

    async def get_mutation_snapshot_for_update(
        self, *, batch_id: str
    ) -> BatchFinalizationReadinessMutationSnapshot:
        self._require_connection()
        authority = self._require_locked_batch(batch_id)
        actual = await self._read_source_snapshot(expected=authority.snapshot)
        return BatchFinalizationReadinessMutationSnapshot(
            batch_id=batch_id,
            batch_version=cast(int, self._locked_batch_version),
            state=BatchState.RUNNING,
            readiness_snapshot=actual,
        )

    async def publish_readiness(
        self, *, publication: BatchFinalizationReadinessPublication
    ) -> ReplayResult[BatchFinalizationReadinessProjection]:
        try:
            return await self._publish_readiness(publication=publication)
        except BaseException:
            self._aborted = True
            raise

    async def _publish_readiness(
        self, *, publication: BatchFinalizationReadinessPublication
    ) -> ReplayResult[BatchFinalizationReadinessProjection]:
        connection = self._require_connection()
        authority = self._require_locked_batch(publication.projection.batch_id)
        if authority != publication.authority:
            raise AuthorityStateConflict(reason="publication_authority_superseded")

        stored = await connection.fetchrow(
            """
            SELECT
                fact.ref,
                fact.batch_id,
                fact.source_batch_version,
                fact.batch_version,
                fact.readiness_digest,
                fact.payload,
                batch.state,
                batch.version,
                batch.finalization_readiness_ref
            FROM qep_batch_finalization_readiness_facts AS fact
            JOIN qep_batches AS batch ON batch.id = fact.batch_id
            WHERE fact.batch_id = $1
            """,
            publication.projection.batch_id,
        )
        if stored is not None:
            projection = _stored_projection(
                batch_id=publication.projection.batch_id,
                row=stored,
            )
            if (
                projection.source_batch_version
                == publication.authority.snapshot.source_batch_version
                and projection.readiness_digest == publication.projection.readiness_digest
            ):
                return ReplayResult(value=projection, replayed=True)
            raise IdempotencyConflict(
                scope=f"batch:{publication.projection.batch_id}:begin-finalization",
                key=str(publication.authority.snapshot.source_batch_version),
                stored_digest=projection.readiness_digest,
                received_digest=publication.projection.readiness_digest,
            )

        if publication.expected_snapshot.batch_version != self._locked_batch_version:
            raise VersionConflict(
                entity_type="batch",
                entity_id=publication.projection.batch_id,
                current_version=self._locked_batch_version or 0,
                expected_version=publication.expected_snapshot.batch_version,
            )
        await self._read_source_snapshot(expected=authority.snapshot)
        await self._insert_readiness_fact(publication)
        await self._publish_batch_projection(publication)
        await self._insert_audit(publication)
        await self._insert_outbox(publication)
        return ReplayResult(value=publication.projection, replayed=False)

    async def _insert_readiness_fact(
        self, publication: BatchFinalizationReadinessPublication
    ) -> None:
        snapshot = publication.authority.snapshot
        digest = _digest_hex(snapshot.readiness_digest)
        ref = f"batch-finalization-readiness-{digest}"
        status = await self._require_connection().execute(
            """
            INSERT INTO qep_batch_finalization_readiness_facts (
                ref, batch_id, source_batch_version, batch_version,
                manifest_digest, shard_plan_digest, canonical_run_set_digest,
                success_policy_digest, batch_cancellation_intent_digest,
                readiness_digest, authority_digest, write_epoch,
                compatibility_epoch, state_model_version, payload, recorded_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15, transaction_timestamp()
            )
            """,
            ref,
            snapshot.batch_id,
            snapshot.source_batch_version,
            snapshot.source_batch_version + 1,
            _digest_hex(snapshot.manifest_digest),
            _digest_hex(snapshot.shard_plan_digest),
            _digest_hex(snapshot.canonical_run_set_digest),
            _digest_hex(snapshot.success_policy.policy_digest),
            _optional_digest_hex(snapshot.batch_cancellation_intent_digest),
            digest,
            _digest_hex(publication.authority.authority_digest),
            publication.authority.write_epoch,
            COMPATIBILITY_EPOCH,
            STATE_MODEL_VERSION,
            _json(_readiness_payload(snapshot)),
        )
        _require_inserted(status, reason="batch_readiness_fact_write_missing")

    async def _publish_batch_projection(
        self, publication: BatchFinalizationReadinessPublication
    ) -> None:
        status = await self._require_connection().execute(
            """
            UPDATE qep_batches
            SET state = 'finalizing',
                version = version + 1,
                finalization_readiness_ref = $1,
                updated_at = transaction_timestamp()
            WHERE id = $2
              AND state = 'running'
              AND version = $3
              AND write_epoch = $4
              AND finalization_readiness_ref IS NULL
            """,
            f"batch-finalization-readiness-{_digest_hex(publication.projection.readiness_digest)}",
            publication.projection.batch_id,
            publication.expected_snapshot.batch_version,
            publication.authority.write_epoch,
        )
        if status != "UPDATE 1":
            raise VersionConflict(
                entity_type="batch",
                entity_id=publication.projection.batch_id,
                current_version=publication.expected_snapshot.batch_version,
                expected_version=publication.expected_snapshot.batch_version,
            )

    async def _insert_audit(self, publication: BatchFinalizationReadinessPublication) -> None:
        snapshot = publication.authority.snapshot
        status = await self._require_connection().execute(
            """
            INSERT INTO qep_audit_events (
                id, actor_id, action, object_type, object_id, decision, reason_code,
                before_digest, after_digest, payload, occurred_at
            ) VALUES (
                $1, 'system', 'begin_batch_finalization', 'batch', $2, 'allowed',
                'batch_finalization_readiness_committed', NULL, $3, $4,
                transaction_timestamp()
            )
            """,
            f"batch-readiness-audit-{_digest_hex(snapshot.readiness_digest)}",
            snapshot.batch_id,
            _digest_hex(snapshot.readiness_digest),
            _json(
                {
                    "schema_version": "qep.batch-finalization-readiness-audit.v1",
                    "batch_id": snapshot.batch_id,
                    "source_batch_version": snapshot.source_batch_version,
                    "batch_version": snapshot.source_batch_version + 1,
                    "readiness_digest": snapshot.readiness_digest.value,
                    "authority_digest": publication.authority.authority_digest.value,
                    "write_epoch": publication.authority.write_epoch,
                }
            ),
        )
        _require_inserted(status, reason="batch_readiness_audit_write_missing")

    async def _insert_outbox(self, publication: BatchFinalizationReadinessPublication) -> None:
        snapshot = publication.authority.snapshot
        payload = {
            "schema_version": "qep.batch-finalization-readiness-started.v1",
            "batch_id": snapshot.batch_id,
            "source_batch_version": snapshot.source_batch_version,
            "batch_version": snapshot.source_batch_version + 1,
            "readiness_digest": snapshot.readiness_digest.value,
            "authority_digest": publication.authority.authority_digest.value,
            "write_epoch": publication.authority.write_epoch,
        }
        identity = canonical_digest(
            schema_version="qep.batch-finalization-readiness-started-identity.v1",
            payload={
                "batch_id": snapshot.batch_id,
                "source_batch_version": snapshot.source_batch_version,
                "readiness_digest": snapshot.readiness_digest.value,
            },
        )
        event_id = f"batch-finalization-readiness-started-{_digest_hex(identity)}"
        payload_digest = canonical_digest(
            schema_version="qep.batch-finalization-readiness-started-payload.v1",
            payload=payload,
        )
        status = await self._require_connection().execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, status, available_at, attempts, created_at
            ) VALUES (
                $1, $1, 'batch', $2, 'batch.finalization.started.v1', $3, $4,
                'pending', transaction_timestamp(), 0, transaction_timestamp()
            )
            """,
            event_id,
            snapshot.batch_id,
            _digest_hex(payload_digest),
            _json(payload),
        )
        _require_inserted(status, reason="batch_readiness_outbox_write_missing")

    async def _read_source_snapshot(
        self, *, expected: BatchFinalizationReadinessSnapshot
    ) -> BatchFinalizationReadinessSnapshot:
        try:
            await self._validate_source_snapshot(expected=expected)
            return expected
        except AuthorityStateConflict:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AuthorityStateConflict(
                reason="batch_readiness_source_integrity_invalid"
            ) from error

    async def _validate_source_snapshot(
        self, *, expected: BatchFinalizationReadinessSnapshot
    ) -> None:
        connection = self._require_connection()
        manifest = await connection.fetchrow(
            """
            SELECT id, batch_id, schema_version, digest, item_count, status, payload
            FROM qep_case_manifests
            WHERE id = $1 AND batch_id = $2
            """,
            expected.manifest_id,
            expected.batch_id,
        )
        if manifest is None:
            raise AuthorityStateConflict(reason="batch_readiness_source_missing")
        if (
            manifest["schema_version"]
            != expected.frozen_plan.bound_plan.plan.manifest.schema_version
            or manifest["digest"] != _digest_hex(expected.manifest_digest)
            or manifest["item_count"] != expected.item_count
            or manifest["status"] != "approved"
            or _json_object(manifest["payload"])
            != _manifest_payload(expected.frozen_plan.bound_plan.plan.manifest)
        ):
            raise AuthorityStateConflict(reason="batch_readiness_source_snapshot_mismatch")
        item_rows = await connection.fetch(
            """
            SELECT item_index, stable_case_id, framework_locator, atomic_group_id,
                   estimated_duration_ms, resource_profile_id, constraints, tags
            FROM qep_manifest_items
            WHERE manifest_id = $1
            ORDER BY item_index
            """,
            expected.manifest_id,
        )
        manifest_items = expected.frozen_plan.bound_plan.plan.manifest.items
        if len(item_rows) != len(manifest_items):
            raise AuthorityStateConflict(reason="batch_readiness_manifest_coverage_invalid")
        for row, item in zip(item_rows, manifest_items, strict=True):
            if (
                row["item_index"] != item.item_index
                or row["stable_case_id"] != item.stable_case_id
                or _json_object(row["framework_locator"])
                != {
                    "schema_version": item.framework_locator.schema_version,
                    "kind": item.framework_locator.kind,
                    "parts": [list(part) for part in item.framework_locator.parts],
                }
                or row["atomic_group_id"] != item.atomic_group_id
                or row["estimated_duration_ms"] != item.estimate.duration_ms
                or row["resource_profile_id"] != item.resource_profile_id
                or _json_object(row["constraints"])
                != {
                    "serial_group": item.constraints.serial_group,
                    "environment_requirements": list(item.constraints.environment_requirements),
                    "account_requirements": list(item.constraints.account_requirements),
                    "data_lease_requirements": list(item.constraints.data_lease_requirements),
                }
                or _json_value(row["tags"]) != list(item.tags)
            ):
                raise AuthorityStateConflict(reason="batch_readiness_source_snapshot_mismatch")

        plan = await connection.fetchrow(
            """
            SELECT id, batch_id, algorithm_version, digest, run_count,
                   total_estimated_duration_ms, status, version, payload
            FROM qep_shard_plans
            WHERE id = $1 AND batch_id = $2
            """,
            expected.shard_plan_id,
            expected.batch_id,
        )
        expected_plan = expected.frozen_plan.bound_plan.plan
        if plan is None:
            raise AuthorityStateConflict(reason="batch_readiness_source_missing")
        if (
            plan["algorithm_version"] != expected_plan.algorithm_version
            or plan["digest"] != _digest_hex(expected_plan.digest)
            or plan["run_count"] != expected_plan.run_count
            or plan["total_estimated_duration_ms"] != expected_plan.total_estimated_duration_ms
            or plan["status"] != "approved"
            or plan["version"] != expected.shard_plan_version
            or _json_object(plan["payload"]) != _plan_payload(expected_plan)
        ):
            raise AuthorityStateConflict(reason="batch_readiness_source_snapshot_mismatch")

        policy = await connection.fetchrow(
            """
            SELECT id, policy_version, suite_id, max_test_failed_items,
                   allow_authorized_retry_pass, policy_digest, payload
            FROM qep_batch_success_policies
            WHERE id = $1 AND policy_version = $2 AND suite_id = $3
            """,
            expected.success_policy.policy_id,
            expected.success_policy.policy_version,
            expected.success_policy.suite_id,
        )
        expected_policy = expected.success_policy
        if policy is None:
            raise AuthorityStateConflict(reason="batch_readiness_source_missing")
        if (
            policy["max_test_failed_items"] != expected_policy.max_test_failed_items
            or policy["allow_authorized_retry_pass"] != expected_policy.allow_authorized_retry_pass
            or policy["policy_digest"] != _digest_hex(expected_policy.policy_digest)
            or _json_object(policy["payload"]) != _policy_payload(expected_policy)
        ):
            raise AuthorityStateConflict(reason="batch_readiness_source_snapshot_mismatch")

        intent_rows = await connection.fetch(
            """
            SELECT intent_digest, scope_kind, manifest_digest, shard_plan_version,
                   shard_plan_digest, canonical_run_set_digest
            FROM qep_batch_cancellation_intents
            WHERE batch_id = $1
            ORDER BY id
            LIMIT 2
            """,
            expected.batch_id,
        )
        expected_intent_digest = expected.batch_cancellation_intent_digest
        if len(intent_rows) != (0 if expected_intent_digest is None else 1):
            raise AuthorityStateConflict(reason="batch_readiness_cancellation_source_mismatch")
        if intent_rows:
            intent = intent_rows[0]
            if (
                intent["intent_digest"] != _digest_hex(expected_intent_digest)
                or intent["scope_kind"] != "frozen_plan"
                or intent["manifest_digest"] != _digest_hex(expected.manifest_digest)
                or intent["shard_plan_version"] != expected.shard_plan_version
                or intent["shard_plan_digest"] != _digest_hex(expected.shard_plan_digest)
                or intent["canonical_run_set_digest"]
                != _digest_hex(expected.canonical_run_set_digest)
            ):
                raise AuthorityStateConflict(reason="batch_readiness_cancellation_source_mismatch")

        run_rows = await connection.fetch(
            """
            SELECT run.id, run.batch_id, run.plan_id, run.shard_index,
                   run.resource_profile_id, run.orchestration_phase, run.disposition,
                   run.outcome, run.version, run.run_item_set_digest,
                   run.finalization_basis_digest,
                   basis.source_run_version, basis.item_resolution_set_digest,
                   basis.outcome AS basis_outcome, basis.basis_digest,
                   basis.payload AS basis_payload
            FROM qep_runs AS run
            LEFT JOIN qep_run_finalization_bases AS basis ON basis.run_id = run.id
            WHERE run.batch_id = $1
            ORDER BY run.id
            """,
            expected.batch_id,
        )
        expected_closures = {closure.run_id: closure for closure in expected.run_closures}
        if tuple(row["id"] for row in run_rows) != expected.canonical_run_ids:
            raise AuthorityStateConflict(reason="batch_readiness_run_set_mismatch")
        bindings = {
            binding.run_id: binding.shard_index
            for binding in expected.frozen_plan.bound_plan.bindings
        }
        for row in run_rows:
            closure = expected_closures[row["id"]]
            basis = closure.basis
            if (
                row["plan_id"] != expected_plan.id
                or row["shard_index"] != bindings[row["id"]]
                or row["resource_profile_id"]
                != expected_plan.shards[row["shard_index"]].resource_profile_id
                or row["orchestration_phase"] != "closed"
                or row["disposition"] != "closed_no_retry"
                or row["outcome"] != basis.outcome.value
                or row["version"] != basis.source_run_version + 1
                or row["run_item_set_digest"] != _digest_hex(basis.run_item_set_digest)
                or row["finalization_basis_digest"] != _digest_hex(basis.basis_digest)
                or row["source_run_version"] != basis.source_run_version
                or row["item_resolution_set_digest"]
                != _digest_hex(basis.item_resolution_set_digest)
                or row["basis_outcome"] != basis.outcome.value
                or row["basis_digest"] != _digest_hex(basis.basis_digest)
                or _json_object(row["basis_payload"]) != basis.canonical_payload()
            ):
                raise AuthorityStateConflict(reason="batch_readiness_run_closure_mismatch")
            resolution_rows = await connection.fetch(
                """
                SELECT manifest_id, item_index, source_run_version,
                       original_fact_digest, effective_fact_digest,
                       unknown_lineage_digest, aggregation_class,
                       item_resolution_digest, payload
                FROM qep_run_item_resolutions
                WHERE run_id = $1
                ORDER BY manifest_id, item_index
                """,
                row["id"],
            )
            entries = closure.resolution_set.entries
            if len(resolution_rows) != len(entries):
                raise AuthorityStateConflict(reason="batch_readiness_run_resolution_mismatch")
            for resolution_row, entry in zip(resolution_rows, entries, strict=True):
                if (
                    resolution_row["manifest_id"] != entry.item_key.manifest_id
                    or resolution_row["item_index"] != entry.item_key.item_index
                    or resolution_row["source_run_version"] != basis.source_run_version
                    or resolution_row["item_resolution_digest"]
                    != _digest_hex(entry.item_resolution_digest)
                    or resolution_row["aggregation_class"] != entry.aggregation_class.value
                    or _json_object(resolution_row["payload"]) != entry.canonical_payload()
                ):
                    raise AuthorityStateConflict(reason="batch_readiness_run_resolution_mismatch")
            handoff_row = await connection.fetchrow(
                """
                SELECT id, event_id, aggregate_id, event_type, payload_digest, payload
                FROM qep_outbox_events
                WHERE aggregate_type = 'run'
                  AND aggregate_id = $1
                  AND event_type = 'run.closed.v1'
                """,
                row["id"],
            )
            if handoff_row is None or (
                handoff_row["id"] != closure.handoff.handoff_id
                or handoff_row["event_id"] != closure.handoff.event_id
                or handoff_row["aggregate_id"] != closure.handoff.run_id
                or handoff_row["payload_digest"] != _digest_hex(closure.handoff.payload_digest)
                or _json_object(handoff_row["payload"]) != _handoff_payload(closure.handoff)
            ):
                raise AuthorityStateConflict(reason="batch_readiness_run_handoff_mismatch")

        retry_rows = await connection.fetch(
            """
            SELECT retry.id, retry.run_id, retry.source_attempt_id,
                   retry.source_attempt_no, retry.source_fence,
                   retry.source_run_version, retry.decision_source_kind,
                   retry.source_item_set_digest, retry.target_item_set_digest,
                   retry.authority_digest, retry.retry_intent_digest,
                   retry.status, retry.payload, retry.created_at
            FROM qep_retry_intents AS retry
            JOIN qep_runs AS run ON run.id = retry.run_id
            WHERE run.batch_id = $1 AND retry.status = 'pending'
            ORDER BY retry.run_id, retry.id
            """,
            expected.batch_id,
        )
        pending_retry_intents = expected.pending_retry_intents
        if len(retry_rows) != len(pending_retry_intents):
            raise AuthorityStateConflict(reason="batch_readiness_retry_opportunity_mismatch")
        closures_by_run = {value.run_id: value for value in expected.run_closures}
        for row, intent in zip(retry_rows, pending_retry_intents, strict=True):
            authority_payload = intent.authority.canonical_payload()
            source_item_set_digest = cast(
                str,
                authority_payload.get(
                    "source_item_set_digest",
                    closures_by_run[intent.run_id].basis.run_item_set_digest.value,
                ),
            )
            target_item_set_digest = cast(
                str,
                authority_payload.get("target_item_set_digest", source_item_set_digest),
            )
            authority_digest = cast(
                str,
                authority_payload.get(
                    "authority_digest", authority_payload.get("adjudication_digest")
                ),
            )
            if (
                row["id"] != intent.id
                or row["run_id"] != intent.run_id
                or row["source_attempt_id"] != intent.source_attempt_id
                or row["source_attempt_no"] != intent.source_attempt_no
                or row["source_fence"] != intent.source_fence
                or row["source_run_version"]
                != closures_by_run[intent.run_id].basis.source_run_version
                or row["decision_source_kind"]
                != authority_payload.get("decision_source_kind", "unknown_adjudication")
                or row["source_item_set_digest"] != source_item_set_digest.removeprefix("sha256:")
                or row["target_item_set_digest"] != target_item_set_digest.removeprefix("sha256:")
                or row["authority_digest"] != authority_digest.removeprefix("sha256:")
                or row["retry_intent_digest"] != _digest_hex(intent.digest)
                or row["status"] != "pending"
                or _json_object(row["payload"]) != _retry_intent_payload(intent)
                or row["created_at"] != intent.created_at
            ):
                raise AuthorityStateConflict(reason="batch_readiness_retry_opportunity_mismatch")
        opportunity_rows = await connection.fetch(
            """
            SELECT assignment.run_id, assignment.state, assignment.attempt_id,
                   assignment.fence, assignment.payload
            FROM qep_assignments AS assignment
            JOIN qep_runs AS run ON run.id = assignment.run_id
            WHERE run.batch_id = $1
              AND assignment.state IN ('offered', 'claimed')
            ORDER BY assignment.run_id, assignment.id
            """,
            expected.batch_id,
        )
        opportunities = expected.attempt_creation_opportunities
        if len(opportunity_rows) != len(opportunities):
            raise AuthorityStateConflict(reason="batch_readiness_attempt_opportunity_mismatch")
        for row, opportunity in zip(opportunity_rows, opportunities, strict=True):
            payload = _json_object(row["payload"])
            if (
                row["run_id"] != opportunity.run_id
                or row["attempt_id"] is not None
                or row["fence"] is not None
                or payload.get("opportunity_kind") != opportunity.kind.value
                or payload.get("authority_digest") != opportunity.authority_digest.value
            ):
                raise AuthorityStateConflict(reason="batch_readiness_attempt_opportunity_mismatch")

    def _validate_stored_fact_binding(
        self,
        *,
        row: asyncpg.Record,
        candidate: BatchFinalizationReadinessAuthority,
    ) -> None:
        snapshot = candidate.snapshot
        if (
            row["state"] != BatchState.FINALIZING.value
            or row["version"] != snapshot.source_batch_version + 1
            or row["stored_source_batch_version"] != snapshot.source_batch_version
            or row["stored_readiness_digest"] != _digest_hex(snapshot.readiness_digest)
            or row["stored_authority_digest"] != _digest_hex(candidate.authority_digest)
            or row["stored_write_epoch"] != candidate.write_epoch
            or row["stored_compatibility_epoch"] != COMPATIBILITY_EPOCH
            or row["stored_state_model_version"] != STATE_MODEL_VERSION
            or row["stored_payload"] is None
        ):
            raise AuthorityStateConflict(reason="batch_readiness_stored_binding_invalid")
        payload = _json_object(row["stored_payload"])
        if payload != _readiness_payload(snapshot):
            raise AuthorityStateConflict(reason="batch_readiness_stored_integrity_invalid")

    def _require_connection(self) -> asyncpg.Connection:
        if self._aborted:
            self._state_error("aborted")
        if self._connection is None:
            self._state_error("not_active" if not self._closed else "closed")
        return self._connection

    def _require_locked_batch(self, batch_id: str) -> BatchFinalizationReadinessAuthority:
        if (
            self._locked_batch_id != batch_id
            or self._authority is None
            or self._authority.snapshot.batch_id != batch_id
        ):
            self._state_error("authority_not_locked")
        return self._authority

    @staticmethod
    def _state_error(reason: str) -> None:
        raise PortContractError(resource="unit_of_work", field="state", reason=reason)


def _stored_projection(
    *, batch_id: str, row: asyncpg.Record
) -> BatchFinalizationReadinessProjection:
    try:
        payload = _json_object(row["payload"])
        if (
            row["batch_id"] != batch_id
            or row["finalization_readiness_ref"] != row["ref"]
            or row["batch_version"] != row["source_batch_version"] + 1
            or row["state"] != BatchState.FINALIZING.value
            or row["version"] != row["batch_version"]
            or row["readiness_digest"]
            != _digest_hex(canonical_digest(schema_version=READINESS_SCHEMA, payload=payload))
        ):
            raise AuthorityStateConflict(reason="batch_readiness_stored_integrity_invalid")
        return BatchFinalizationReadinessProjection(
            batch_id=batch_id,
            source_batch_version=row["source_batch_version"],
            batch_version=row["batch_version"],
            state=BatchState.FINALIZING,
            readiness_digest=_digest(row["readiness_digest"]),
        )
    except AuthorityStateConflict:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise AuthorityStateConflict(reason="batch_readiness_stored_integrity_invalid") from error


def _manifest_payload(manifest) -> dict[str, object]:
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


def _plan_payload(plan) -> dict[str, object]:
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


def _policy_payload(policy) -> dict[str, object]:
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


def _readiness_payload(snapshot: BatchFinalizationReadinessSnapshot) -> dict[str, object]:
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
        "batch_cancellation_intent_digest": _optional_digest_value(
            snapshot.batch_cancellation_intent_digest
        ),
        "run_basis_digests": [value.basis.basis_digest.value for value in snapshot.run_closures],
        "pending_retry_intents": [
            {"run_id": value.run_id, "intent_digest": value.digest.value}
            for value in snapshot.pending_retry_intents
        ],
        "attempt_creation_opportunities": [
            {
                "run_id": value.run_id,
                "kind": value.kind.value,
                "authority_digest": value.authority_digest.value,
            }
            for value in snapshot.attempt_creation_opportunities
        ],
    }


def _handoff_payload(handoff) -> dict[str, object]:
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


def _retry_intent_payload(intent) -> dict[str, object]:
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


def _json_object(value: object) -> dict[str, object]:
    decoded = _json_value(value)
    if not isinstance(decoded, dict):
        raise ValueError("stored payload must be an object")
    return decoded


def _json_value(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _require_inserted(status: str, *, reason: str) -> None:
    if status != "INSERT 0 1":
        raise AuthorityStateConflict(reason=reason)


def _digest(value: str) -> Digest:
    return Digest(f"sha256:{value}")


def _digest_hex(value: Digest) -> str:
    return value.value.removeprefix("sha256:")


def _optional_digest_hex(value: Digest | None) -> str | None:
    return None if value is None else _digest_hex(value)


def _optional_digest_value(value: Digest | None) -> str | None:
    return None if value is None else value.value
