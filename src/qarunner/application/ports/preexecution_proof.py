"""Authoritative raw-fact ports for Batch pre-execution closure proof."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from qarunner.domain.batch import BatchPreexecutionSnapshot
from qarunner.domain.digest import Digest, canonical_digest


@dataclass(frozen=True, order=True, slots=True)
class PreexecutionTaskKey:
    """Stable business ordering and generation identity for one ledger task."""

    phase_ordinal: int
    task_kind: str
    task_key: str
    generation: int


def canonical_task_set_digest(keys: tuple[PreexecutionTaskKey, ...]) -> Digest:
    """Bind an authoritative seal to stable business-ordered task generations."""
    return canonical_digest(
        schema_version="qep.preexecution-task-set.v1",
        payload={
            "tasks": [
                {
                    "phase_ordinal": key.phase_ordinal,
                    "task_kind": key.task_kind,
                    "task_key": key.task_key,
                    "generation": key.generation,
                }
                for key in keys
            ]
        },
    )


@dataclass(frozen=True, slots=True)
class PreexecutionTaskGeneration:
    key: PreexecutionTaskKey
    batch_id: str
    project_id: str
    suite_revision_id: str
    issuer_id: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class TrustedTaskStop:
    key: PreexecutionTaskKey
    batch_id: str
    project_id: str
    issuer_id: str
    stopped_at: datetime
    digest: Digest


@dataclass(frozen=True, slots=True)
class ExecutionChildInventory:
    """Batch-scoped execution authorities observed under one serialization point."""

    run_ids: tuple[str, ...]
    assignment_ids: tuple[str, ...]
    start_commit_ids: tuple[str, ...]
    attempt_ids: tuple[str, ...]
    fences: tuple[int, ...]
    retry_intent_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SealedTaskInventory:
    """Authoritative task-ledger seal, including a provable empty set."""

    batch_id: str
    project_id: str
    suite_revision_id: str
    ledger_version: int
    high_watermark: int
    task_count: int
    task_set_digest: Digest
    issuer_id: str
    sealed_at: datetime
    tasks: tuple[PreexecutionTaskGeneration, ...]


@dataclass(frozen=True, slots=True)
class TaskLedgerPosition:
    ledger_version: int
    high_watermark: int


@dataclass(frozen=True, slots=True)
class ZeroChildSnapshotInputs:
    batch_id: str
    project_id: str
    suite_revision_id: str
    source_batch_version: int
    ledger_version: int
    high_watermark: int
    task_set_digest: Digest
    stop_fact_digests: tuple[Digest, ...]


@runtime_checkable
class PreexecutionProofGateway(Protocol):
    """Read proof facts under the dedicated Batch-local command boundary."""

    async def scan_execution_children(self, *, batch_id: str) -> ExecutionChildInventory: ...

    async def read_sealed_task_inventory(self, *, batch_id: str) -> SealedTaskInventory | None: ...

    async def read_current_task_ledger_position(self, *, batch_id: str) -> TaskLedgerPosition: ...

    async def read_trusted_task_stops(self, *, batch_id: str) -> tuple[TrustedTaskStop, ...]: ...

    async def is_trusted_inventory_issuer(self, *, issuer_id: str) -> bool: ...

    async def is_trusted_stop_issuer(self, *, issuer_id: str) -> bool: ...

    async def quarantine_integrity_failure(self, *, batch_id: str) -> None: ...

    async def assemble_zero_child_snapshot(
        self, *, inputs: ZeroChildSnapshotInputs
    ) -> BatchPreexecutionSnapshot: ...
