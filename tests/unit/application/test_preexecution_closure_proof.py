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

    assert proof.batch_id == "batch-001"
    assert proof.ledger_version == 1
    assert proof.high_watermark == 0
    assert proof.task_count == 0
    assert proof.stop_fact_digests == ()
    assert gateway.child_scans == 1
    assert gateway.inventory_reads == 1
    assert gateway.stop_reads == 1


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
        VerifiedTaskCompleteness,
    )

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

    assert isinstance(result, VerifiedTaskCompleteness)
    assert result.task_count == 2
    assert len(result.stop_fact_digests) == 2
