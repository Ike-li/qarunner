"""Deterministic authoritative-fact Fake for pre-execution proof."""

from datetime import UTC, datetime

from qarunner.application.ports.preexecution_proof import (
    ExecutionChildInventory,
    PreexecutionTaskGeneration,
    PreexecutionTaskKey,
    SealedPlannedScopeInventory,
    SealedTaskInventory,
    TaskLedgerPosition,
    TrustedTaskStop,
    ZeroChildSnapshotInputs,
    canonical_task_set_digest,
)
from qarunner.domain.batch import (
    BatchPreexecutionScopeItem,
    BatchPreexecutionScopeKind,
    BatchPreexecutionSnapshot,
    BatchPreexecutionTerminalKind,
)
from qarunner.domain.digest import Digest, canonical_digest


class InMemoryPreexecutionProofGateway:
    def __init__(
        self,
        *,
        inventory_sealed: bool,
        task_generations: tuple[tuple[int, str, str, int], ...] = (),
        stopped_generations: tuple[tuple[int, str, str, int], ...] = (),
        run_ids: tuple[str, ...] = (),
        assignment_ids: tuple[str, ...] = (),
        start_commit_ids: tuple[str, ...] = (),
        attempt_ids: tuple[str, ...] = (),
        fences: tuple[int, ...] = (),
        retry_intent_ids: tuple[str, ...] = (),
        inventory_project_id: str = "project-001",
        inventory_suite_revision_id: str = "suite-revision-001",
        inventory_issuer_id: str = "coordinator-001",
        stop_issuer_id: str = "runtime-001",
        task_set_digest_override: Digest | None = None,
        ledger_version: int = 1,
        high_watermark: int | None = None,
        current_ledger_version: int | None = None,
        current_high_watermark: int | None = None,
        planned_manifest_id: str | None = None,
        planned_manifest_digest: Digest | None = None,
        planned_item_keys: tuple[str, ...] = (),
        planned_shard_plan_id: str | None = None,
        planned_shard_plan_version: int | None = None,
        planned_shard_plan_digest: Digest | None = None,
        planned_issuer_id: str = "coordinator-001",
        planned_item_count_override: int | None = None,
    ) -> None:
        self.inventory_sealed = inventory_sealed
        self.task_generations = task_generations
        self.stopped_generations = stopped_generations
        self.run_ids = run_ids
        self.assignment_ids = assignment_ids
        self.start_commit_ids = start_commit_ids
        self.attempt_ids = attempt_ids
        self.fences = fences
        self.retry_intent_ids = retry_intent_ids
        self.inventory_project_id = inventory_project_id
        self.inventory_suite_revision_id = inventory_suite_revision_id
        self.inventory_issuer_id = inventory_issuer_id
        self.stop_issuer_id = stop_issuer_id
        self.task_set_digest_override = task_set_digest_override
        self.ledger_version = ledger_version
        self.high_watermark = len(task_generations) if high_watermark is None else high_watermark
        self.current_ledger_version = (
            ledger_version if current_ledger_version is None else current_ledger_version
        )
        self.current_high_watermark = (
            self.high_watermark if current_high_watermark is None else current_high_watermark
        )
        self.planned_manifest_id = planned_manifest_id
        self.planned_manifest_digest = planned_manifest_digest
        self.planned_item_keys = planned_item_keys
        self.planned_shard_plan_id = planned_shard_plan_id
        self.planned_shard_plan_version = planned_shard_plan_version
        self.planned_shard_plan_digest = planned_shard_plan_digest
        self.planned_issuer_id = planned_issuer_id
        self.planned_item_count_override = planned_item_count_override
        self.child_scans = 0
        self.inventory_reads = 0
        self.stop_reads = 0
        self.ledger_position_reads = 0
        self.snapshot_assemblies = 0
        self.published_snapshots: tuple[object, ...] = ()
        self.quarantined_batches: tuple[str, ...] = ()
        self.materialized_run_set_digest = canonical_digest(
            schema_version="qep.authoritative-materialized-run-set.v1",
            payload={"batch_id": "batch-001", "run_ids": list(sorted(run_ids))},
        )

    async def scan_execution_children(self, *, batch_id: str) -> ExecutionChildInventory:
        del batch_id
        self.child_scans += 1
        return ExecutionChildInventory(
            run_ids=self.run_ids,
            assignment_ids=self.assignment_ids,
            start_commit_ids=self.start_commit_ids,
            attempt_ids=self.attempt_ids,
            fences=self.fences,
            retry_intent_ids=self.retry_intent_ids,
        )

    async def read_sealed_task_inventory(self, *, batch_id: str):
        self.inventory_reads += 1
        if not self.inventory_sealed:
            return None
        tasks = tuple(
            PreexecutionTaskGeneration(
                key=PreexecutionTaskKey(*key),
                batch_id=batch_id,
                project_id=self.inventory_project_id,
                suite_revision_id=self.inventory_suite_revision_id,
                issuer_id=self.inventory_issuer_id,
                started_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
            )
            for key in self.task_generations
        )
        return SealedTaskInventory(
            batch_id=batch_id,
            project_id=self.inventory_project_id,
            suite_revision_id=self.inventory_suite_revision_id,
            ledger_version=self.ledger_version,
            high_watermark=self.high_watermark,
            task_count=len(tasks),
            task_set_digest=(
                self.task_set_digest_override
                if self.task_set_digest_override is not None
                else canonical_task_set_digest(tuple(task.key for task in tasks))
            ),
            issuer_id=self.inventory_issuer_id,
            sealed_at=datetime(2026, 7, 15, 6, 1, tzinfo=UTC),
            tasks=tasks,
        )

    async def read_current_task_ledger_position(self, *, batch_id: str) -> TaskLedgerPosition:
        del batch_id
        self.ledger_position_reads += 1
        return TaskLedgerPosition(
            ledger_version=self.current_ledger_version,
            high_watermark=self.current_high_watermark,
        )

    async def read_trusted_task_stops(self, *, batch_id: str) -> tuple[TrustedTaskStop, ...]:
        self.stop_reads += 1
        return tuple(
            TrustedTaskStop(
                key=PreexecutionTaskKey(*key),
                batch_id=batch_id,
                project_id="project-001",
                issuer_id=self.stop_issuer_id,
                stopped_at=datetime(2026, 7, 15, 6, 2, tzinfo=UTC),
                digest=canonical_digest(
                    schema_version="qep.test-preexecution-task-stop.v1",
                    payload={"key": list(key)},
                ),
            )
            for key in self.stopped_generations
        )

    async def quarantine_integrity_failure(self, *, batch_id: str) -> None:
        self.quarantined_batches += (batch_id,)

    async def is_trusted_inventory_issuer(self, *, issuer_id: str) -> bool:
        return issuer_id == "coordinator-001"

    async def is_trusted_stop_issuer(self, *, issuer_id: str) -> bool:
        return issuer_id == "runtime-001"

    async def read_sealed_planned_scope_inventory(
        self, *, batch_id: str
    ) -> SealedPlannedScopeInventory | None:
        if self.planned_manifest_id is None:
            return None
        assert self.planned_manifest_digest is not None
        assert self.planned_shard_plan_id is not None
        assert self.planned_shard_plan_version is not None
        assert self.planned_shard_plan_digest is not None
        return SealedPlannedScopeInventory(
            batch_id=batch_id,
            project_id=self.inventory_project_id,
            suite_revision_id=self.inventory_suite_revision_id,
            manifest_id=self.planned_manifest_id,
            manifest_digest=self.planned_manifest_digest,
            shard_plan_id=self.planned_shard_plan_id,
            shard_plan_version=self.planned_shard_plan_version,
            shard_plan_digest=self.planned_shard_plan_digest,
            item_count=(
                len(self.planned_item_keys)
                if self.planned_item_count_override is None
                else self.planned_item_count_override
            ),
            manifest_item_keys=self.planned_item_keys,
            issuer_id=self.planned_issuer_id,
            sealed_at=datetime(2026, 7, 15, 6, 3, tzinfo=UTC),
        )

    async def is_trusted_planned_scope_issuer(self, *, issuer_id: str) -> bool:
        return issuer_id == "coordinator-001"

    async def assemble_zero_child_snapshot(
        self, *, inputs: ZeroChildSnapshotInputs
    ) -> BatchPreexecutionSnapshot:
        self.snapshot_assemblies += 1
        run_absence_digest = canonical_digest(
            schema_version="qep.preexecution-materialized-run-absence.v1",
            payload={
                "batch_id": inputs.batch_id,
                "source_batch_version": inputs.source_batch_version,
            },
        )
        common = {
            "batch_id": inputs.batch_id,
            "source_batch_version": inputs.source_batch_version,
            "submission_digest": canonical_digest(
                schema_version="qep.test-preexecution-submission.v1",
                payload={"batch_id": inputs.batch_id},
            ),
            "materialized_run_absence_digest": run_absence_digest,
            "execution_absence_snapshot_digest": canonical_digest(
                schema_version="qep.preexecution-execution-absence.v1",
                payload={
                    "batch_id": inputs.batch_id,
                    "source_batch_version": inputs.source_batch_version,
                    "ledger_version": inputs.ledger_version,
                    "high_watermark": inputs.high_watermark,
                    "task_set_digest": inputs.task_set_digest.value,
                    "stop_fact_digests": [digest.value for digest in inputs.stop_fact_digests],
                },
            ),
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
            snapshot = BatchPreexecutionSnapshot(
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
        else:
            preplan_scope_digest = (
                inputs.scope.preplan_scope_digest if inputs.scope is not None else None
            )
            snapshot = BatchPreexecutionSnapshot(
                **common,
                scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
                preplan_scope_digest=(
                    preplan_scope_digest
                    if preplan_scope_digest is not None
                    else canonical_digest(
                        schema_version="qep.test-batch-cancel.v1",
                        payload={"label": "preplan-scope"},
                    )
                ),
                manifest_digest=None,
                shard_plan_version=None,
                shard_plan_digest=None,
                canonical_run_set_digest=None,
                scope_items=(),
                item_coverage_proof_digest=None,
            )
        self.published_snapshots += (snapshot,)
        return snapshot
