"""T-M0-STATE-001H H6: fact-aware RUNNING -> FINALIZING application UoW."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-begin-batch-finalization.v1", payload={"label": label}
    )


def _values(*, blocked: str | None = None):
    from tests.unit.application.test_finalize_run import bound_basis
    from tests.unit.domain.test_batch_finalization import _policy
    from tests.unit.domain.test_manifest_shard_plan import _item, _manifest, _plan, _shard
    from tests.unit.domain.test_run_finalization_basis import resolution_set as base_resolution_set

    from qarunner.application import build_run_closed_handoff
    from qarunner.application.ports.batch_finalization_readiness import (
        AttemptCreationOpportunity,
        AttemptCreationOpportunityKind,
        BatchFinalizationReadinessAuthority,
        BatchFinalizationReadinessMutationSnapshot,
        BatchFinalizationReadinessSnapshot,
        BatchRunClosure,
        FrozenBoundShardPlan,
    )
    from qarunner.domain import (
        BatchState,
        BoundShardPlan,
        RunBinding,
        RunDisposition,
        RunFinalizationState,
        RunItemResolutionSet,
        RunPhase,
        canonical_materialized_run_set_digest,
    )

    manifest = _manifest(_item(0, "case-1"), manifest_id="manifest-1", batch_id="batch-1")
    plan = _plan(
        manifest,
        _shard(0, (0,), estimated_duration_ms=100),
        plan_id="plan-1",
        batch_id="batch-1",
    )
    bound_plan = BoundShardPlan.create(
        plan=plan, bindings=(RunBinding(run_id="run-1", shard_index=0),)
    )
    base = base_resolution_set()
    resolved = RunItemResolutionSet.build(
        expected_item_keys=tuple(value.item_key for value in base.entries),
        entries=base.entries,
        batch_id=base.batch_id,
        run_id=base.run_id,
        source_run_version=base.source_run_version,
        manifest_digest=manifest.digest,
        shard_plan_digest=plan.digest,
        run_item_set_digest=base.run_item_set_digest,
        attempt_chain_digest=base.attempt_chain_digest,
        retry_chain_digest=base.retry_chain_digest,
        adjudication_chain_digest=base.adjudication_chain_digest,
    )
    basis = bound_basis(resolved)
    handoff = build_run_closed_handoff(
        basis=basis,
        resolution_set=resolved,
        authority_digest=_digest("run-authority"),
        write_epoch=1,
    )
    state = RunFinalizationState(
        RunPhase.CLOSED,
        RunDisposition.CLOSED_NO_RETRY,
        basis.outcome,
        basis.basis_digest,
        None,
        None,
        None,
    )
    closure = BatchRunClosure(handoff, basis, resolved, state)
    pending_retry_intents = ()
    opportunities = ()
    if blocked == "retry":
        from tests.unit.domain.test_unknown_adjudicated_retry import _retry_intent

        pending_retry_intents = (replace(_retry_intent(), run_id=basis.run_id),)
    if blocked == "attempt":
        opportunities = (
            AttemptCreationOpportunity(
                run_id=basis.run_id,
                kind=AttemptCreationOpportunityKind.RETRY_COMMIT,
                authority_digest=_digest("retry-commit-authority"),
            ),
        )
    snapshot = BatchFinalizationReadinessSnapshot(
        batch_id=basis.batch_id,
        source_batch_version=9,
        manifest_id=manifest.id,
        manifest_digest=basis.manifest_digest,
        item_count=basis.item_count,
        shard_plan_id=plan.id,
        frozen_plan=FrozenBoundShardPlan.freeze(plan_version=3, bound_plan=bound_plan),
        canonical_run_ids=(basis.run_id,),
        canonical_run_set_digest=canonical_materialized_run_set_digest(
            batch_id=basis.batch_id, run_ids=(basis.run_id,)
        ),
        success_policy=_policy(),
        batch_cancellation_intent_digest=None,
        run_closures=(closure,),
        pending_retry_intents=pending_retry_intents,
        attempt_creation_opportunities=opportunities,
    )
    authority = BatchFinalizationReadinessAuthority(snapshot, _digest("authority"), 1)
    mutation = BatchFinalizationReadinessMutationSnapshot(
        basis.batch_id, 9, BatchState.RUNNING, snapshot
    )
    return snapshot, authority, mutation


@pytest.mark.asyncio
async def test_ready_snapshot_atomically_transitions_with_audit_and_outbox() -> None:
    from tests.fakes.greenfield.batch_finalization_readiness import (
        InMemoryBatchFinalizationReadinessGateway,
    )

    from qarunner.application.begin_batch_finalization import (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
    )
    from qarunner.domain import BatchState

    snapshot, authority, mutation = _values()
    fake = InMemoryBatchFinalizationReadinessGateway(authority, mutation)

    result = await BeginBatchFinalization(gateway=fake).execute(
        BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
    )

    assert not result.replayed
    assert result.projection is not None
    assert result.projection.state is BatchState.FINALIZING
    assert result.projection.batch_version == snapshot.source_batch_version + 1
    assert result.projection.readiness_digest == snapshot.readiness_digest
    assert (fake.publication_commits, fake.audit_count, fake.outbox_count) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["retry", "attempt"])
async def test_live_blockers_return_not_ready_without_lock_or_write(blocked: str) -> None:
    from tests.fakes.greenfield.batch_finalization_readiness import (
        InMemoryBatchFinalizationReadinessGateway,
    )

    from qarunner.application.begin_batch_finalization import (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
    )

    snapshot, authority, mutation = _values(blocked=blocked)
    fake = InMemoryBatchFinalizationReadinessGateway(authority, mutation)

    result = await BeginBatchFinalization(gateway=fake).execute(
        BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
    )

    assert result.projection is None
    assert result.reason.value == f"pending_{blocked}"
    assert (fake.mutation_snapshot_reads, fake.publication_attempts) == (0, 0)


def test_snapshot_rejects_run_basis_detached_from_manifest_plan_or_run_set() -> None:
    snapshot, _, _ = _values()
    closure = snapshot.run_closures[0]

    with pytest.raises(ValueError, match="frozen_plan"):
        replace(snapshot, manifest_digest=_digest("other-manifest"))
    with pytest.raises(ValueError, match="binding_digest"):
        replace(snapshot.frozen_plan, plan_version=snapshot.shard_plan_version + 1)
    with pytest.raises(ValueError, match="frozen_plan"):
        replace(snapshot, canonical_run_ids=("run-other",))
    with pytest.raises(ValueError, match="canonical_run_ids"):
        replace(snapshot, canonical_run_set_digest=_digest("other-run-set"))
    with pytest.raises(ValueError, match="run_finalization_state.phase"):
        replace(
            snapshot,
            run_closures=(replace(closure, state=replace(closure.state, phase="closed")),),
        )


@pytest.mark.asyncio
async def test_exact_replay_precedes_stale_cas_but_requires_current_authority() -> None:
    from tests.fakes.greenfield.batch_finalization_readiness import (
        InMemoryBatchFinalizationReadinessGateway,
    )

    from qarunner.application.begin_batch_finalization import (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
    )

    snapshot, authority, mutation = _values()
    fake = InMemoryBatchFinalizationReadinessGateway(authority, mutation)
    command = BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
    first = await BeginBatchFinalization(gateway=fake).execute(command)
    stale_sources = replace(snapshot, source_batch_version=99)
    fake.mutation_snapshot = replace(mutation, batch_version=99, readiness_snapshot=stale_sources)

    replay = await BeginBatchFinalization(gateway=fake).execute(command)

    assert replay.replayed and replay.projection == first.projection
    assert fake.mutation_snapshot_reads == 1


@pytest.mark.asyncio
async def test_locked_snapshot_drift_rejects_before_publication() -> None:
    from tests.fakes.greenfield.batch_finalization_readiness import (
        InMemoryBatchFinalizationReadinessGateway,
    )

    from qarunner.application.begin_batch_finalization import (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
    )

    snapshot, authority, mutation = _values()
    drifted = replace(snapshot, success_policy=replace(snapshot.success_policy, policy_version=99))
    fake = InMemoryBatchFinalizationReadinessGateway(
        authority, replace(mutation, readiness_snapshot=drifted)
    )

    with pytest.raises(ValueError, match="locked_snapshot_drift"):
        await BeginBatchFinalization(gateway=fake).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
        )
    assert fake.publication_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["projection", "audit", "outbox", "commit"])
async def test_publication_fault_rolls_back_all_participants(point: str) -> None:
    from tests.fakes.greenfield.batch_finalization_readiness import (
        InMemoryBatchFinalizationReadinessGateway,
    )

    from qarunner.application.begin_batch_finalization import (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
    )

    snapshot, authority, mutation = _values()
    fake = InMemoryBatchFinalizationReadinessGateway(authority, mutation, fault_at=point)

    with pytest.raises(RuntimeError, match=point):
        await BeginBatchFinalization(gateway=fake).execute(
            BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
        )
    assert (fake.projection_count, fake.audit_count, fake.outbox_count) == (0, 0, 0)
