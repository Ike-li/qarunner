"""Deterministic authoritative-fact Fake for pre-execution proof."""

from datetime import UTC, datetime

from qarunner.application.ports.preexecution_proof import (
    ExecutionChildInventory,
    PreexecutionTaskGeneration,
    PreexecutionTaskKey,
    SealedTaskInventory,
    TaskLedgerPosition,
    TrustedTaskStop,
    ZeroChildSnapshotInputs,
    canonical_task_set_digest,
)
from qarunner.domain.batch import BatchPreexecutionScopeKind, BatchPreexecutionSnapshot
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

    async def assemble_zero_child_snapshot(
        self, *, inputs: ZeroChildSnapshotInputs
    ) -> BatchPreexecutionSnapshot:
        self.snapshot_assemblies += 1
        snapshot = BatchPreexecutionSnapshot(
            batch_id=inputs.batch_id,
            source_batch_version=inputs.source_batch_version,
            scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
            submission_digest=canonical_digest(
                schema_version="qep.test-preexecution-submission.v1",
                payload={"batch_id": inputs.batch_id},
            ),
            preplan_scope_digest=canonical_digest(
                schema_version="qep.test-batch-cancel.v1",
                payload={"label": "preplan-scope"},
            ),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
            materialized_run_absence_digest=canonical_digest(
                schema_version="qep.preexecution-materialized-run-absence.v1",
                payload={
                    "batch_id": inputs.batch_id,
                    "source_batch_version": inputs.source_batch_version,
                },
            ),
            execution_absence_snapshot_digest=canonical_digest(
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
            task_stop_fact_digests=inputs.stop_fact_digests,
            scope_items=(),
            item_coverage_proof_digest=None,
        )
        self.published_snapshots += (snapshot,)
        return snapshot
