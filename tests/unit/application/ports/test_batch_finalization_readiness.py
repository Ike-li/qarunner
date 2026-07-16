"""Value and binding tests for Batch finalization readiness ports."""

from dataclasses import replace

import pytest


def _values():
    from tests.unit.application.test_begin_batch_finalization import _values

    from qarunner.application.ports.batch_finalization_readiness import (
        BatchFinalizationReadinessProjection,
        BatchFinalizationReadinessPublication,
        BatchFinalizationReadinessSideEffect,
    )
    from qarunner.domain import BatchState

    snapshot, authority, mutation = _values()
    projection = BatchFinalizationReadinessProjection(
        snapshot.batch_id,
        snapshot.source_batch_version,
        snapshot.source_batch_version + 1,
        BatchState.FINALIZING,
        snapshot.readiness_digest,
    )
    effect = BatchFinalizationReadinessSideEffect(snapshot.batch_id, snapshot.readiness_digest)
    publication = BatchFinalizationReadinessPublication(authority, mutation, projection, effect)
    return snapshot, authority, mutation, projection, effect, publication


@pytest.mark.parametrize(
    ("field", "value"),
    [("run_id", ""), ("kind", object()), ("authority_digest", object())],
)
def test_attempt_opportunity_rejects_invalid_values(field, value) -> None:
    opportunity = _values()[0].attempt_creation_opportunities
    from tests.unit.application.test_begin_batch_finalization import _digest

    from qarunner.application.ports.batch_finalization_readiness import (
        AttemptCreationOpportunity,
        AttemptCreationOpportunityKind,
    )

    valid = AttemptCreationOpportunity(
        "run-001", AttemptCreationOpportunityKind.INITIAL_COMMIT, _digest("authority")
    )
    assert opportunity == ()
    with pytest.raises(ValueError, match=field):
        replace(valid, **{field: value})


@pytest.mark.parametrize("field", ["handoff", "basis", "resolution_set", "state"])
def test_run_closure_rejects_untyped_participants(field: str) -> None:
    closure = _values()[0].run_closures[0]
    with pytest.raises(ValueError, match=field):
        replace(closure, **{field: object()})


def test_run_closure_rejects_closed_state_binding_drift() -> None:
    from tests.unit.application.test_begin_batch_finalization import _digest

    closure = _values()[0].run_closures[0]
    with pytest.raises(ValueError, match="binding"):
        replace(
            closure,
            state=replace(closure.state, finalization_basis_digest=_digest("other-basis")),
        )


def test_snapshot_rejects_run_resolution_items_detached_from_bound_shard() -> None:
    from tests.unit.application.test_begin_batch_finalization import _digest
    from tests.unit.application.test_finalize_run import bound_basis

    from qarunner.application import build_run_closed_handoff
    from qarunner.application.ports.batch_finalization_readiness import BatchRunClosure
    from qarunner.domain import RunItemKey, RunItemResolutionSet

    snapshot = _values()[0]
    closure = snapshot.run_closures[0]
    entry = replace(closure.resolution_set.entries[0], item_key=RunItemKey("manifest-1", 1))
    resolution = RunItemResolutionSet.build(
        expected_item_keys=(entry.item_key,),
        entries=(entry,),
        batch_id=closure.basis.batch_id,
        run_id=closure.basis.run_id,
        source_run_version=closure.basis.source_run_version,
        manifest_digest=closure.basis.manifest_digest,
        shard_plan_digest=closure.basis.shard_plan_digest,
        run_item_set_digest=closure.basis.run_item_set_digest,
        attempt_chain_digest=closure.basis.attempt_chain_digest,
        retry_chain_digest=closure.basis.retry_chain_digest,
        adjudication_chain_digest=closure.basis.adjudication_chain_digest,
    )
    basis = bound_basis(resolution)
    handoff = build_run_closed_handoff(
        basis=basis,
        resolution_set=resolution,
        authority_digest=_digest("authority"),
        write_epoch=1,
    )
    detached = BatchRunClosure(
        handoff,
        basis,
        resolution,
        replace(closure.state, finalization_basis_digest=basis.basis_digest),
    )

    with pytest.raises(ValueError, match="shard_item_ownership_mismatch"):
        replace(snapshot, run_closures=(detached,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_id", ""),
        ("source_batch_version", True),
        ("item_count", 0),
        ("manifest_digest", object()),
        ("frozen_plan", object()),
        ("success_policy", object()),
        ("batch_cancellation_intent_digest", object()),
        ("canonical_run_ids", object()),
        ("canonical_run_ids", ()),
        ("canonical_run_ids", ("z", "a")),
        ("run_closures", object()),
        ("run_closures", (object(),)),
        ("pending_retry_intents", (object(),)),
        ("attempt_creation_opportunities", object()),
        ("attempt_creation_opportunities", (object(),)),
    ],
)
def test_readiness_snapshot_rejects_invalid_contract(field, value) -> None:
    snapshot = _values()[0]
    with pytest.raises(ValueError, match=field):
        replace(snapshot, **{field: value})


def test_readiness_snapshot_supports_typed_cancel_and_rejects_unknown_attempt_run() -> None:
    from tests.unit.application.test_begin_batch_finalization import _digest

    from qarunner.application.ports.batch_finalization_readiness import (
        AttemptCreationOpportunity,
        AttemptCreationOpportunityKind,
    )

    snapshot = _values()[0]
    cancelled = replace(snapshot, batch_cancellation_intent_digest=_digest("cancel"))
    assert cancelled.readiness_digest != snapshot.readiness_digest
    unknown = AttemptCreationOpportunity(
        "run-unknown", AttemptCreationOpportunityKind.INITIAL_COMMIT, _digest("authority")
    )
    with pytest.raises(ValueError, match="attempt_creation_opportunities"):
        replace(snapshot, attempt_creation_opportunities=(unknown,))


def test_readiness_snapshot_rejects_manifest_count_or_bound_plan_drift() -> None:
    snapshot = _values()[0]
    with pytest.raises(ValueError, match="frozen_plan"):
        replace(snapshot, item_count=snapshot.item_count + 1)
    with pytest.raises(ValueError, match="frozen_plan"):
        replace(snapshot, shard_plan_id="plan-other")


def test_frozen_plan_rejects_detached_version_plan_or_digest() -> None:
    from qarunner.application.ports.batch_finalization_readiness import FrozenBoundShardPlan

    frozen = _values()[0].frozen_plan
    with pytest.raises(ValueError, match="plan_version"):
        replace(frozen, plan_version=True)
    with pytest.raises(ValueError, match="bound_plan"):
        replace(frozen, bound_plan=object())
    with pytest.raises(ValueError, match="binding_digest"):
        replace(frozen, binding_digest=object())
    with pytest.raises(ValueError, match="binding_digest"):
        replace(frozen, plan_version=frozen.plan_version + 1)
    with pytest.raises(ValueError, match="bound_plan"):
        FrozenBoundShardPlan.freeze(plan_version=1, bound_plan=object())


def test_readiness_snapshot_rejects_duplicate_attempt_opportunity() -> None:
    from tests.unit.application.test_begin_batch_finalization import _digest

    from qarunner.application.ports.batch_finalization_readiness import (
        AttemptCreationOpportunity,
        AttemptCreationOpportunityKind,
    )

    snapshot = _values()[0]
    opportunity = AttemptCreationOpportunity(
        snapshot.canonical_run_ids[0],
        AttemptCreationOpportunityKind.INITIAL_COMMIT,
        _digest("authority"),
    )
    with pytest.raises(ValueError, match="attempt_creation_opportunities"):
        replace(snapshot, attempt_creation_opportunities=(opportunity, opportunity))


def test_readiness_snapshot_rejects_duplicate_or_foreign_pending_retry() -> None:
    from tests.unit.domain.test_unknown_adjudicated_retry import _retry_intent

    snapshot = _values()[0]
    intent = _retry_intent()
    with pytest.raises(ValueError, match="pending_retry_intents"):
        replace(snapshot, pending_retry_intents=(intent, intent))
    with pytest.raises(ValueError, match="pending_retry_intents"):
        replace(snapshot, pending_retry_intents=(replace(intent, run_id="run-unknown"),))


@pytest.mark.parametrize(
    ("index", "field", "value"),
    [
        (1, "snapshot", object()),
        (1, "authority_digest", object()),
        (1, "write_epoch", 0),
        (3, "batch_id", ""),
        (3, "source_batch_version", True),
        (3, "batch_version", 0),
        (3, "state", object()),
        (3, "readiness_digest", object()),
        (2, "batch_id", ""),
        (2, "batch_version", True),
        (2, "state", object()),
        (2, "readiness_snapshot", object()),
        (4, "batch_id", ""),
        (4, "readiness_digest", object()),
    ],
)
def test_port_values_reject_invalid_contract(index, field, value) -> None:
    with pytest.raises(ValueError, match=field):
        replace(_values()[index], **{field: value})


def test_mutation_rejects_identity_version_detached_from_snapshot() -> None:
    mutation = _values()[2]
    with pytest.raises(ValueError, match="binding"):
        replace(mutation, batch_version=mutation.batch_version + 1)


@pytest.mark.parametrize("field", ["authority", "expected_snapshot", "projection", "side_effect"])
def test_publication_rejects_untyped_participant(field: str) -> None:
    with pytest.raises(ValueError, match="binding"):
        replace(_values()[5], **{field: object()})


def test_publication_rejects_blocked_authority_and_projection_drift() -> None:
    from tests.unit.application.test_begin_batch_finalization import _values as readiness_values

    publication = _values()[5]
    _, blocked_authority, _ = readiness_values(blocked="retry")
    with pytest.raises(ValueError, match="binding"):
        replace(publication, authority=blocked_authority)
    with pytest.raises(ValueError, match="binding"):
        replace(
            publication,
            projection=replace(
                publication.projection,
                batch_version=publication.projection.batch_version + 1,
            ),
        )


@pytest.mark.parametrize("field", ["batch_id", "expected_batch_version"])
def test_command_rejects_invalid_values(field: str) -> None:
    from qarunner.application.begin_batch_finalization import BeginBatchFinalizationCommand

    values = {"batch_id": "batch-001", "expected_batch_version": 1}
    values[field] = "" if field == "batch_id" else True
    with pytest.raises(ValueError, match=field):
        BeginBatchFinalizationCommand(**values)


@pytest.mark.asyncio
async def test_handler_rejects_stored_digest_conflict_and_stale_cas() -> None:
    from tests.fakes.greenfield.batch_finalization_readiness import (
        InMemoryBatchFinalizationReadinessGateway,
    )
    from tests.unit.application.test_begin_batch_finalization import _digest

    from qarunner.application.begin_batch_finalization import (
        BeginBatchFinalization,
        BeginBatchFinalizationCommand,
    )

    snapshot, authority, mutation, projection, *_ = _values()
    fake = InMemoryBatchFinalizationReadinessGateway(authority, mutation)
    fake.projections[authority.identity_scope] = replace(
        projection, readiness_digest=_digest("different")
    )
    command = BeginBatchFinalizationCommand(snapshot.batch_id, snapshot.source_batch_version)
    with pytest.raises(ValueError, match="already bound"):
        await BeginBatchFinalization(gateway=fake).execute(command)
    fake.projections.clear()
    with pytest.raises(ValueError, match="expected 10"):
        await BeginBatchFinalization(gateway=fake).execute(
            replace(command, expected_batch_version=command.expected_batch_version + 1)
        )
