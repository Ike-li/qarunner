"""Deterministic authoritative-fact Fake for pre-execution proof."""

from datetime import UTC, datetime

from qarunner.application.ports.preexecution_proof import (
    ExecutionChildInventory,
    PreexecutionTaskGeneration,
    PreexecutionTaskKey,
    SealedTaskInventory,
    TrustedTaskStop,
)
from qarunner.domain.digest import canonical_digest


class InMemoryPreexecutionProofGateway:
    def __init__(
        self,
        *,
        inventory_sealed: bool,
        task_generations: tuple[tuple[int, str, str, int], ...] = (),
        stopped_generations: tuple[tuple[int, str, str, int], ...] = (),
        run_ids: tuple[str, ...] = (),
        assignment_ids: tuple[str, ...] = (),
        inventory_project_id: str = "project-001",
        inventory_suite_revision_id: str = "suite-revision-001",
        inventory_issuer_id: str = "coordinator-001",
        stop_issuer_id: str = "runtime-001",
    ) -> None:
        self.inventory_sealed = inventory_sealed
        self.task_generations = task_generations
        self.stopped_generations = stopped_generations
        self.run_ids = run_ids
        self.assignment_ids = assignment_ids
        self.inventory_project_id = inventory_project_id
        self.inventory_suite_revision_id = inventory_suite_revision_id
        self.inventory_issuer_id = inventory_issuer_id
        self.stop_issuer_id = stop_issuer_id
        self.child_scans = 0
        self.inventory_reads = 0
        self.stop_reads = 0
        self.snapshot_assemblies = 0
        self.published_snapshots: tuple[object, ...] = ()
        self.quarantined_batches: tuple[str, ...] = ()

    async def scan_execution_children(self, *, batch_id: str) -> ExecutionChildInventory:
        del batch_id
        self.child_scans += 1
        return ExecutionChildInventory(
            run_ids=self.run_ids,
            assignment_ids=self.assignment_ids,
            start_commit_ids=(),
            attempt_ids=(),
            fences=(),
            retry_intent_ids=(),
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
            ledger_version=1,
            high_watermark=len(tasks),
            task_count=len(tasks),
            task_set_digest=canonical_digest(
                schema_version="qep.test-preexecution-task-set.v1",
                payload={"keys": [list(key) for key in self.task_generations]},
            ),
            issuer_id=self.inventory_issuer_id,
            sealed_at=datetime(2026, 7, 15, 6, 1, tzinfo=UTC),
            tasks=tasks,
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
