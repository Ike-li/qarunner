"""M0 proof contract for zero-child Batch pre-execution closure."""

import pytest


@pytest.mark.asyncio
async def test_unsealed_empty_inventory_cannot_prove_closure_ready() -> None:
    """A query returning no task rows is not an authoritative empty inventory."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        ClosureNotReady,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(inventory_sealed=False)
    command = ProvePreexecutionClosureCommand(
        batch_id="batch-001",
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=4,
    )

    with pytest.raises(ClosureNotReady) as caught:
        await ProvePreexecutionClosure(gateway=gateway).execute(command)

    assert caught.value.code == "CLOSURE_NOT_READY"
    assert caught.value.retryable is True
    assert gateway.child_scans == 1
    assert gateway.inventory_reads == 1
    assert gateway.snapshot_assemblies == 0
    assert gateway.published_snapshots == ()


@pytest.mark.asyncio
async def test_prior_phase_unstopped_generation_blocks_current_phase_closure() -> None:
    """Source phase cannot filter an earlier task generation from completeness."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        ClosureNotReady,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        task_generations=(
            (2, "collection", "source-001", 1),
            (3, "planning", "plan-001", 1),
        ),
        stopped_generations=((3, "planning", "plan-001", 1),),
    )

    with pytest.raises(ClosureNotReady):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.child_scans == 1
    assert gateway.inventory_reads == 1
    assert gateway.stop_reads == 1
    assert gateway.snapshot_assemblies == 0
    assert gateway.published_snapshots == ()


@pytest.mark.asyncio
async def test_explicitly_sealed_empty_inventory_proves_task_completeness() -> None:
    """An authoritative count-zero seal is distinct from a query returning no rows."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    proof = await ProvePreexecutionClosure(gateway=gateway).execute(
        ProvePreexecutionClosureCommand(
            batch_id="batch-001",
            project_id="project-001",
            suite_revision_id="suite-revision-001",
            source_batch_version=4,
        )
    )

    from qarunner.domain import BatchPreexecutionSnapshot

    assert isinstance(proof, BatchPreexecutionSnapshot)
    assert proof.batch_id == "batch-001"
    assert proof.source_batch_version == 4
    assert proof.task_stop_fact_digests == ()
    assert gateway.child_scans == 1
    assert gateway.inventory_reads == 1
    assert gateway.stop_reads == 1
    assert gateway.snapshot_assemblies == 1


@pytest.mark.asyncio
async def test_orphan_assignment_quarantines_before_zero_child_proof() -> None:
    """An execution authority without its Run is corruption, never an empty scope."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        assignment_ids=("assignment-orphan",),
    )

    with pytest.raises(IntegrityFailure) as caught:
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert caught.value.code == "INTEGRITY_FAILURE"
    assert caught.value.retryable is False
    assert gateway.quarantined_batches == ("batch-001",)
    assert gateway.inventory_reads == 0
    assert gateway.stop_reads == 0


@pytest.mark.asyncio
async def test_materialized_run_short_circuits_task_completeness_for_handoff() -> None:
    """A materialized Run selects handoff and never evaluates zero-child proof."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        MaterializedExecutionScope,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
        assignment_ids=("assignment-001",),
    )

    result = await ProvePreexecutionClosure(gateway=gateway).execute(
        ProvePreexecutionClosureCommand(
            batch_id="batch-001",
            project_id="project-001",
            suite_revision_id="suite-revision-001",
            source_batch_version=4,
        )
    )

    assert isinstance(result, MaterializedExecutionScope)
    assert result.batch_id == "batch-001"
    assert result.run_ids == ("run-001",)
    assert gateway.inventory_reads == 0
    assert gateway.stop_reads == 0
    assert gateway.published_snapshots == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inventory_project_id", "inventory_suite_revision_id", "inventory_issuer_id"),
    [
        pytest.param("project-other", "suite-revision-001", "coordinator-001", id="project"),
        pytest.param("project-001", "suite-revision-other", "coordinator-001", id="suite"),
        pytest.param("project-001", "suite-revision-001", "unknown-issuer", id="issuer"),
    ],
)
async def test_inventory_provenance_mismatch_quarantines_before_stop_evaluation(
    inventory_project_id: str,
    inventory_suite_revision_id: str,
    inventory_issuer_id: str,
) -> None:
    """Ownership and issuer provenance bind the seal before it can prove emptiness."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        inventory_project_id=inventory_project_id,
        inventory_suite_revision_id=inventory_suite_revision_id,
        inventory_issuer_id=inventory_issuer_id,
    )

    with pytest.raises(IntegrityFailure):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.quarantined_batches == ("batch-001",)
    assert gateway.stop_reads == 0


@pytest.mark.asyncio
async def test_task_inventory_must_use_stable_business_order_not_digest_order() -> None:
    """A seal with reordered task identities is corrupt even if every task stopped."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    reordered = (
        (3, "planning", "plan-001", 1),
        (2, "collection", "source-001", 1),
    )
    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        task_generations=reordered,
        stopped_generations=reordered,
    )

    with pytest.raises(IntegrityFailure):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.quarantined_batches == ("batch-001",)
    assert gateway.stop_reads == 0


@pytest.mark.asyncio
async def test_untrusted_stop_issuer_cannot_satisfy_task_completeness() -> None:
    """A matching task key does not make a runtime self-report a trusted stop fact."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    key = (2, "collection", "source-001", 1)
    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        task_generations=(key,),
        stopped_generations=(key,),
        stop_issuer_id="untrusted-runtime",
    )

    with pytest.raises(IntegrityFailure):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.quarantined_batches == ("batch-001",)


@pytest.mark.asyncio
async def test_all_prior_and_current_generations_with_trusted_stops_are_complete() -> None:
    """Exact stable-key equality preserves every phase generation in the proof."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )
    from qarunner.domain import BatchPreexecutionSnapshot

    generations = (
        (2, "collection", "source-001", 1),
        (3, "planning", "plan-001", 1),
    )
    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        task_generations=generations,
        stopped_generations=generations,
    )

    result = await ProvePreexecutionClosure(gateway=gateway).execute(
        ProvePreexecutionClosureCommand(
            batch_id="batch-001",
            project_id="project-001",
            suite_revision_id="suite-revision-001",
            source_batch_version=4,
        )
    )

    assert isinstance(result, BatchPreexecutionSnapshot)
    assert len(result.task_stop_fact_digests) == 2
    assert gateway.snapshot_assemblies == 1


@pytest.mark.asyncio
async def test_task_set_digest_is_rebuilt_from_stable_ordered_keys() -> None:
    """A signed-looking seal cannot substitute a digest for different ledger content."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )
    from qarunner.domain import canonical_digest

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        task_set_digest_override=canonical_digest(
            schema_version="qep.preexecution-task-set.v1",
            payload={"tasks": []},
        ),
        task_generations=((2, "collection", "source-001", 1),),
        stopped_generations=((2, "collection", "source-001", 1),),
    )

    with pytest.raises(IntegrityFailure):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.stop_reads == 0
    assert gateway.quarantined_batches == ("batch-001",)


@pytest.mark.asyncio
async def test_stale_task_seal_cannot_survive_a_new_ledger_generation() -> None:
    """A changed ledger version/high-watermark invalidates the previous sealed view."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        ClosureNotReady,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        ledger_version=1,
        high_watermark=0,
        current_ledger_version=2,
        current_high_watermark=1,
    )

    with pytest.raises(ClosureNotReady):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.ledger_position_reads == 1
    assert gateway.stop_reads == 0
    assert gateway.quarantined_batches == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ledger_version", "high_watermark"),
    [
        pytest.param(0, 0, id="zero-version"),
        pytest.param(True, 0, id="bool-version"),
        pytest.param(1, -1, id="negative-high-watermark"),
        pytest.param(1, True, id="bool-high-watermark"),
    ],
)
async def test_invalid_task_seal_position_is_integrity_failure(
    ledger_version: int,
    high_watermark: int,
) -> None:
    """Malformed persisted seal positions cannot become a retryable empty proof."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        ledger_version=ledger_version,
        high_watermark=high_watermark,
    )

    with pytest.raises(IntegrityFailure):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.quarantined_batches == ("batch-001",)
    assert gateway.stop_reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("child_field", "value"),
    [
        pytest.param("assignment_ids", ("assignment-orphan",), id="assignment"),
        pytest.param("start_commit_ids", ("commit-orphan",), id="start-commit"),
        pytest.param("attempt_ids", ("attempt-orphan",), id="attempt"),
        pytest.param("fences", (1,), id="fence"),
        pytest.param("retry_intent_ids", ("retry-orphan",), id="retry-intent"),
    ],
)
async def test_each_orphan_execution_authority_is_quarantined(
    child_field: str,
    value: tuple[object, ...],
) -> None:
    """Every execution authority requires a materialized Run lineage."""
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
    )

    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        **{child_field: value},
    )

    with pytest.raises(IntegrityFailure):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=4,
            )
        )

    assert gateway.quarantined_batches == ("batch-001",)


@pytest.mark.asyncio
@pytest.mark.parametrize("planned_inventory_present", [False, True])
async def test_planned_scope_requires_complete_unique_authoritative_inventory(
    planned_inventory_present: bool,
) -> None:
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        ClosureNotReady,
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
        canonical_materialized_run_set_digest,
    )
    from qarunner.domain import (
        BatchCancellationScope,
        BatchCancellationScopeKind,
        BatchPreexecutionTerminalKind,
        canonical_digest,
    )

    def digest(label: str):
        return canonical_digest(
            schema_version="qep.test-planned-proof.v1",
            payload={"label": label},
        )

    scope = BatchCancellationScope(
        kind=BatchCancellationScopeKind.FROZEN_PLAN,
        preplan_scope_digest=None,
        manifest_digest=digest("manifest"),
        shard_plan_version=2,
        shard_plan_digest=digest("plan"),
        canonical_run_set_digest=canonical_materialized_run_set_digest(
            batch_id="batch-001",
            run_ids=(),
        ),
    )
    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        planned_manifest_id="manifest-001" if planned_inventory_present else None,
        planned_manifest_digest=scope.manifest_digest if planned_inventory_present else None,
        planned_item_keys=("case-001", "case-001") if planned_inventory_present else (),
        planned_shard_plan_id="plan-001" if planned_inventory_present else None,
        planned_shard_plan_version=2 if planned_inventory_present else None,
        planned_shard_plan_digest=scope.shard_plan_digest if planned_inventory_present else None,
    )
    expected = IntegrityFailure if planned_inventory_present else ClosureNotReady

    with pytest.raises(expected):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=3,
                scope=scope,
                terminal_kind=BatchPreexecutionTerminalKind.PRESTART_CANCEL,
                command_digest=digest("intent"),
            )
        )

    assert gateway.snapshot_assemblies == 0
    assert gateway.quarantined_batches == (("batch-001",) if planned_inventory_present else ())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item_keys", "issuer_id", "item_count"),
    [
        (("case-002", "case-001"), "coordinator-001", 2),
        (("",), "coordinator-001", 1),
        (("case-001",), "untrusted-writer", 1),
        (("case-001",), "coordinator-001", 2),
    ],
)
async def test_planned_scope_quarantines_noncanonical_or_untrusted_inventory(
    item_keys: tuple[str, ...],
    issuer_id: str,
    item_count: int,
) -> None:
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.preexecution_proof import (
        IntegrityFailure,
        ProvePreexecutionClosure,
        ProvePreexecutionClosureCommand,
        canonical_materialized_run_set_digest,
    )
    from qarunner.domain import (
        BatchCancellationScope,
        BatchCancellationScopeKind,
        BatchPreexecutionTerminalKind,
        canonical_digest,
    )

    def digest(label: str):
        return canonical_digest(
            schema_version="qep.test-planned-proof-integrity.v1",
            payload={"label": label},
        )

    scope = BatchCancellationScope(
        kind=BatchCancellationScopeKind.FROZEN_PLAN,
        preplan_scope_digest=None,
        manifest_digest=digest("manifest"),
        shard_plan_version=2,
        shard_plan_digest=digest("plan"),
        canonical_run_set_digest=canonical_materialized_run_set_digest(
            batch_id="batch-001",
            run_ids=(),
        ),
    )
    gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        planned_manifest_id="manifest-001",
        planned_manifest_digest=scope.manifest_digest,
        planned_item_keys=item_keys,
        planned_shard_plan_id="plan-001",
        planned_shard_plan_version=2,
        planned_shard_plan_digest=scope.shard_plan_digest,
        planned_issuer_id=issuer_id,
        planned_item_count_override=item_count,
    )

    with pytest.raises(IntegrityFailure):
        await ProvePreexecutionClosure(gateway=gateway).execute(
            ProvePreexecutionClosureCommand(
                batch_id="batch-001",
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                source_batch_version=3,
                scope=scope,
                terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
                command_digest=digest("rejection"),
            )
        )

    assert gateway.quarantined_batches == ("batch-001",)
    assert gateway.snapshot_assemblies == 0
