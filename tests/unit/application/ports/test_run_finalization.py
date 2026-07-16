"""G4 Run finalization application port contract."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(schema_version="qep.test-g4-port.v1", payload={"label": label})


def _authority(**changes):
    from qarunner.application.ports import FinalizeRunAuthority

    values = {
        "run_id": "run-1",
        "batch_id": "batch-1",
        "source_run_version": 4,
        "authority_digest": _digest("authority"),
        "write_epoch": 2,
    }
    values.update(changes)
    return FinalizeRunAuthority(**values)


def _projection(**changes):
    from qarunner.application.ports import RunFinalizationProjection
    from qarunner.domain import RunDisposition, RunFinalizationState, RunOutcome, RunPhase

    values = {
        "run_id": "run-1",
        "source_run_version": 4,
        "state": RunFinalizationState(
            phase=RunPhase.CLOSED,
            disposition=RunDisposition.CLOSED_NO_RETRY,
            outcome=RunOutcome.PASSED,
            finalization_basis_digest=_digest("basis"),
            latest_attempt_fact=None,
            current_assignment_id=None,
            pending_retry_intent_id=None,
        ),
    }
    values.update(changes)
    return RunFinalizationProjection(**values)


def _side_effect(**changes):
    from qarunner.application.ports import RunFinalizationSideEffect
    from qarunner.domain import RunOutcome

    values = {"run_id": "run-1", "basis_digest": _digest("basis"), "outcome": RunOutcome.PASSED}
    values.update(changes)
    return RunFinalizationSideEffect(**values)


def _snapshot(**changes):
    from qarunner.application.ports import RunFinalizationMutationSnapshot

    values = {"run_id": "run-1", "run_version": 4, "attempt_id": "attempt-1", "attempt_version": 0}
    values.update(changes)
    return RunFinalizationMutationSnapshot(**values)


def _basis_and_resolution():
    from dataclasses import replace

    from tests.unit.domain.test_run_finalization_basis import basis
    from tests.unit.domain.test_run_item_resolution_set import _set

    resolution = _set(source_run_version=4)
    value = replace(
        basis(),
        source_run_version=4,
        manifest_digest=resolution.manifest_digest,
        shard_plan_digest=resolution.shard_plan_digest,
        run_item_set_digest=resolution.run_item_set_digest,
        item_resolution_set_digest=resolution.resolution_set_digest,
        original_resolution_set_digest=resolution.original_resolution_set_digest,
        effective_resolution_set_digest=resolution.effective_resolution_set_digest,
        item_count=resolution.item_count,
    )
    return value, resolution


def _publication(**changes):
    from qarunner.application.ports import RunFinalizationPublication
    from qarunner.application.run_closed_handoff import build_run_closed_handoff

    basis, resolution = _basis_and_resolution()
    projection = _projection(
        state=replace(_projection().state, finalization_basis_digest=basis.basis_digest)
    )
    values = {
        "authority": _authority(),
        "expected_snapshot": _snapshot(),
        "basis": basis,
        "resolution_set": resolution,
        "projection": projection,
        "side_effect": _side_effect(basis_digest=basis.basis_digest),
        "handoff": build_run_closed_handoff(
            basis=basis,
            resolution_set=resolution,
            authority_digest=_authority().authority_digest,
            write_epoch=_authority().write_epoch,
        ),
    }
    values.update(changes)
    return RunFinalizationPublication(**values)


def test_authority_identity_scope_is_exact_basis_schema_run_and_source_version() -> None:
    value = _authority()
    assert value.identity_scope == ("qep.run-finalization-basis.v1", "run-1", 4)


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": ""},
        {"batch_id": 7},
        {"source_run_version": True},
        {"source_run_version": -1},
        {"authority_digest": "raw"},
        {"write_epoch": 0},
    ],
)
def test_authority_rejects_invalid_values(changes) -> None:
    with pytest.raises(ValueError):
        _authority(**changes)


def test_projection_requires_closed_state_and_matching_basis_identity() -> None:
    from qarunner.domain import RunDisposition, RunFinalizationState, RunPhase

    value = _projection()
    assert value.basis_digest == _digest("basis")
    active = RunFinalizationState(RunPhase.RUNNING, None, None, None, None, "assignment-1", None)
    with pytest.raises(ValueError, match="state"):
        replace(value, state=active)
    with pytest.raises(ValueError, match="state"):
        replace(value, state=replace(value.state, disposition=RunDisposition.REVIEW_REQUIRED))


def test_mutation_snapshot_binds_run_and_optional_attempt_versions() -> None:
    assert _snapshot(attempt_id=None, attempt_version=None).attempt_id is None
    for changes in (
        {"run_id": ""},
        {"run_version": True},
        {"attempt_id": None},
        {"attempt_version": None},
        {"attempt_version": -1},
    ):
        with pytest.raises(ValueError):
            _snapshot(**changes)


@pytest.mark.parametrize(
    "changes", [{"run_id": ""}, {"source_run_version": -1}, {"state": object()}]
)
def test_projection_rejects_invalid_values(changes) -> None:
    with pytest.raises(ValueError):
        _projection(**changes)


def test_side_effect_is_minimal_typed_semantic_identity() -> None:
    from qarunner.domain import RunOutcome

    value = _side_effect()
    assert value.outcome is RunOutcome.PASSED
    for changes in ({"run_id": ""}, {"basis_digest": "raw"}, {"outcome": "passed"}):
        with pytest.raises(ValueError):
            _side_effect(**changes)


def test_publication_binds_authority_projection_basis_and_side_effect() -> None:
    value = _publication()
    assert value.identity_scope == value.authority.identity_scope
    assert value.basis.basis_digest == value.projection.basis_digest
    for changes in (
        {"projection": replace(value.projection, run_id="run-2")},
        {"projection": replace(value.projection, source_run_version=5)},
        {"basis": replace(value.basis, run_id="run-2")},
        {"resolution_set": replace(value.resolution_set, run_id="run-2")},
        {"expected_snapshot": replace(value.expected_snapshot, run_id="run-2")},
        {"side_effect": replace(value.side_effect, run_id="run-2")},
        {
            "side_effect": replace(
                value.side_effect,
                outcome=__import__(
                    "qarunner.domain", fromlist=["RunOutcome"]
                ).RunOutcome.CANCELLED,
            )
        },
    ):
        with pytest.raises(ValueError, match="binding"):
            replace(value, **changes)


@pytest.mark.parametrize(
    "field",
    [
        "authority",
        "expected_snapshot",
        "basis",
        "resolution_set",
        "projection",
        "side_effect",
        "handoff",
    ],
)
def test_publication_rejects_untyped_members(field: str) -> None:
    valid = _publication()
    values = {
        name: getattr(valid, name)
        for name in (
            "authority",
            "expected_snapshot",
            "basis",
            "resolution_set",
            "projection",
            "side_effect",
        )
    }
    values[field] = object()
    with pytest.raises(ValueError, match=field):
        _publication(**values)


def test_gateway_protocol_is_runtime_checkable() -> None:
    from qarunner.application.ports import (
        FinalizeRunAuthority,
        ReplayResult,
        RunFinalizationGateway,
        RunFinalizationMutationSnapshot,
        RunFinalizationPublication,
    )

    class Stub:
        async def require_finalization_authority(self, *, run_id: str) -> FinalizeRunAuthority:
            return _authority(run_id=run_id)

        async def lookup_stored(self, *, identity_scope):
            return None

        async def get_mutation_snapshot_for_update(
            self, *, run_id: str
        ) -> RunFinalizationMutationSnapshot:
            return _snapshot(run_id=run_id)

        async def publish_finalization(self, *, publication: RunFinalizationPublication):
            return ReplayResult(value=publication.projection, replayed=False)

    assert isinstance(Stub(), RunFinalizationGateway)
