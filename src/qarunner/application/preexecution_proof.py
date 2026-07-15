"""Application proof assembly for zero-child pre-execution closure."""

from dataclasses import dataclass

from qarunner.application.ports.preexecution_proof import (
    PreexecutionProofGateway,
    ZeroChildSnapshotInputs,
    canonical_task_set_digest,
)
from qarunner.domain.batch import BatchPreexecutionSnapshot


class ClosureNotReady(RuntimeError):
    """Trusted completeness is not yet available for a terminal decision."""

    code = "CLOSURE_NOT_READY"
    retryable = True

    def __init__(self) -> None:
        super().__init__("pre-execution closure proof is not ready")


class IntegrityFailure(RuntimeError):
    """Authoritative facts contain an impossible execution-child topology."""

    code = "INTEGRITY_FAILURE"
    retryable = False

    def __init__(self) -> None:
        super().__init__("pre-execution proof integrity failure")


@dataclass(frozen=True, slots=True)
class ProvePreexecutionClosureCommand:
    batch_id: str
    project_id: str
    suite_revision_id: str
    source_batch_version: int


@dataclass(frozen=True, slots=True)
class MaterializedExecutionScope:
    """Proof branch requiring an execution-path handoff, never zero-child closure."""

    batch_id: str
    run_ids: tuple[str, ...]


class ProvePreexecutionClosure:
    """Apply the fixed child-scan then completeness-proof ordering."""

    def __init__(self, *, gateway: PreexecutionProofGateway) -> None:
        self._gateway = gateway

    async def execute(
        self, command: ProvePreexecutionClosureCommand
    ) -> BatchPreexecutionSnapshot | MaterializedExecutionScope:
        children = await self._gateway.scan_execution_children(batch_id=command.batch_id)
        orphan_authority = not children.run_ids and any(
            (
                children.assignment_ids,
                children.start_commit_ids,
                children.attempt_ids,
                children.fences,
                children.retry_intent_ids,
            )
        )
        if orphan_authority:
            await self._gateway.quarantine_integrity_failure(batch_id=command.batch_id)
            raise IntegrityFailure
        if children.run_ids:
            return MaterializedExecutionScope(
                batch_id=command.batch_id,
                run_ids=children.run_ids,
            )
        inventory = await self._gateway.read_sealed_task_inventory(batch_id=command.batch_id)
        if inventory is None:
            raise ClosureNotReady
        invalid_position = (
            isinstance(inventory.ledger_version, bool)
            or not isinstance(inventory.ledger_version, int)
            or inventory.ledger_version < 1
            or isinstance(inventory.high_watermark, bool)
            or not isinstance(inventory.high_watermark, int)
            or inventory.high_watermark < 0
            or isinstance(inventory.task_count, bool)
            or not isinstance(inventory.task_count, int)
            or inventory.task_count < 0
        )
        if invalid_position:
            await self._gateway.quarantine_integrity_failure(batch_id=command.batch_id)
            raise IntegrityFailure
        position = await self._gateway.read_current_task_ledger_position(batch_id=command.batch_id)
        if (
            position.ledger_version != inventory.ledger_version
            or position.high_watermark != inventory.high_watermark
        ):
            raise ClosureNotReady
        issuer_trusted = await self._gateway.is_trusted_inventory_issuer(
            issuer_id=inventory.issuer_id
        )
        if (
            inventory.batch_id != command.batch_id
            or inventory.project_id != command.project_id
            or inventory.suite_revision_id != command.suite_revision_id
            or not issuer_trusted
        ):
            await self._gateway.quarantine_integrity_failure(batch_id=command.batch_id)
            raise IntegrityFailure
        task_keys = tuple(task.key for task in inventory.tasks)
        if (
            inventory.task_count != len(task_keys)
            or len(set(task_keys)) != len(task_keys)
            or task_keys != tuple(sorted(task_keys))
            or inventory.task_set_digest != canonical_task_set_digest(task_keys)
        ):
            await self._gateway.quarantine_integrity_failure(batch_id=command.batch_id)
            raise IntegrityFailure
        stops = await self._gateway.read_trusted_task_stops(batch_id=command.batch_id)
        for stop in stops:
            issuer_trusted = await self._gateway.is_trusted_stop_issuer(issuer_id=stop.issuer_id)
            if (
                stop.batch_id != command.batch_id
                or stop.project_id != command.project_id
                or not issuer_trusted
            ):
                await self._gateway.quarantine_integrity_failure(batch_id=command.batch_id)
                raise IntegrityFailure
        stop_keys = tuple(stop.key for stop in stops)
        if task_keys != stop_keys:
            raise ClosureNotReady
        return await self._gateway.assemble_zero_child_snapshot(
            inputs=ZeroChildSnapshotInputs(
                batch_id=inventory.batch_id,
                project_id=inventory.project_id,
                suite_revision_id=inventory.suite_revision_id,
                source_batch_version=command.source_batch_version,
                ledger_version=inventory.ledger_version,
                high_watermark=inventory.high_watermark,
                task_set_digest=inventory.task_set_digest,
                stop_fact_digests=tuple(stop.digest for stop in stops),
            )
        )
