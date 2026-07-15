"""T-M0-STATE-001G G1: four-layer Run finalization value contract."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-run-finalization-state.v1",
        payload={"label": label},
    )


def _state(**changes: object):
    from qarunner.domain import RunFinalizationState, RunPhase

    values: dict[str, object] = {
        "phase": RunPhase.RUNNING,
        "disposition": None,
        "outcome": None,
        "finalization_basis_digest": None,
        "latest_attempt_fact": None,
        "current_assignment_id": "assignment-001",
        "pending_retry_intent_id": None,
    }
    values.update(changes)
    return RunFinalizationState(**values)


def test_v1_vocabularies_are_exact_and_do_not_reuse_cancelled_as_a_phase() -> None:
    from qarunner.domain import RunDisposition, RunOutcome, RunPhase

    assert tuple(item.value for item in RunPhase) == (
        "planned",
        "queued",
        "assigned",
        "running",
        "retry_queued",
        "closed",
    )
    assert tuple(item.value for item in RunDisposition) == (
        "review_required",
        "retry_queued",
        "closed_no_retry",
    )
    assert tuple(item.value for item in RunOutcome) == (
        "passed",
        "test_failed",
        "infra_failed",
        "cancelled",
    )
    assert "cancelled" not in {item.value for item in RunPhase}


@pytest.mark.parametrize("phase_name", ["PLANNED", "QUEUED", "ASSIGNED", "RUNNING"])
def test_active_phase_may_have_no_disposition_outcome_or_basis(phase_name: str) -> None:
    from qarunner.domain import RunPhase

    state = _state(
        phase=RunPhase[phase_name],
        current_assignment_id=(
            "assignment-001" if phase_name in {"ASSIGNED", "RUNNING"} else None
        ),
    )

    assert state.disposition is None
    assert state.outcome is None
    assert state.finalization_basis_digest is None


def test_review_required_is_a_running_unknown_without_outcome() -> None:
    from qarunner.domain import AttemptExecutionFact, RunDisposition, RunPhase

    state = _state(
        phase=RunPhase.RUNNING,
        disposition=RunDisposition.REVIEW_REQUIRED,
        latest_attempt_fact=AttemptExecutionFact.ATTEMPT_UNKNOWN,
    )

    assert state.disposition is RunDisposition.REVIEW_REQUIRED
    assert state.latest_attempt_fact is AttemptExecutionFact.ATTEMPT_UNKNOWN


def test_retry_queued_requires_its_pending_intent_without_outcome() -> None:
    from qarunner.domain import RunDisposition, RunPhase

    state = _state(
        phase=RunPhase.RETRY_QUEUED,
        disposition=RunDisposition.RETRY_QUEUED,
        current_assignment_id=None,
        pending_retry_intent_id="retry-001",
    )

    assert state.pending_retry_intent_id == "retry-001"
    assert state.outcome is None


@pytest.mark.parametrize("outcome_name", ["PASSED", "TEST_FAILED", "INFRA_FAILED", "CANCELLED"])
def test_closed_no_retry_requires_outcome_basis_and_cleared_active_pointers(
    outcome_name: str,
) -> None:
    from qarunner.domain import RunDisposition, RunOutcome, RunPhase

    state = _state(
        phase=RunPhase.CLOSED,
        disposition=RunDisposition.CLOSED_NO_RETRY,
        outcome=RunOutcome[outcome_name],
        finalization_basis_digest=_digest(f"basis-{outcome_name}"),
        current_assignment_id=None,
    )

    assert state.phase is RunPhase.CLOSED
    assert state.outcome is RunOutcome[outcome_name]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"phase": "running"}, "phase"),
        ({"disposition": "review_required"}, "disposition"),
        ({"outcome": "passed"}, "outcome"),
        ({"latest_attempt_fact": "attempt_unknown"}, "latest_attempt_fact"),
        ({"finalization_basis_digest": "sha256:not-typed"}, "finalization_basis_digest"),
        ({"current_assignment_id": " "}, "current_assignment_id"),
        ({"pending_retry_intent_id": 7}, "pending_retry_intent_id"),
    ],
)
def test_rejects_raw_or_malformed_values(changes: dict[str, object], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _state(**changes)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"phase": "CLOSED"}, "closed_requires_disposition"),
        (
            {"disposition": "CLOSED_NO_RETRY"},
            "closed_disposition_requires_closed_phase",
        ),
        (
            {"phase": "CLOSED", "disposition": "CLOSED_NO_RETRY"},
            "closed_requires_outcome",
        ),
        (
            {
                "phase": "CLOSED",
                "disposition": "CLOSED_NO_RETRY",
                "outcome": "PASSED",
            },
            "closed_requires_basis",
        ),
        (
            {
                "phase": "CLOSED",
                "disposition": "CLOSED_NO_RETRY",
                "outcome": "PASSED",
                "finalization_basis_digest": "basis",
                "current_assignment_id": "assignment-001",
            },
            "closed_forbids_active_pointer",
        ),
        ({"outcome": "PASSED"}, "outcome_requires_closed"),
        ({"finalization_basis_digest": "basis"}, "basis_requires_closed"),
        ({"disposition": "REVIEW_REQUIRED"}, "review_requires_unknown"),
        (
            {
                "phase": "QUEUED",
                "disposition": "REVIEW_REQUIRED",
                "latest_attempt_fact": "ATTEMPT_UNKNOWN",
                "current_assignment_id": None,
            },
            "review_requires_running",
        ),
        ({"phase": "RETRY_QUEUED", "current_assignment_id": None}, "retry_requires_disposition"),
        (
            {
                "phase": "RETRY_QUEUED",
                "disposition": "RETRY_QUEUED",
                "current_assignment_id": None,
            },
            "retry_requires_pending_intent",
        ),
        (
            {
                "phase": "RUNNING",
                "disposition": "RETRY_QUEUED",
                "pending_retry_intent_id": "retry-001",
            },
            "retry_disposition_requires_retry_phase",
        ),
        (
            {
                "phase": "RETRY_QUEUED",
                "disposition": "RETRY_QUEUED",
                "pending_retry_intent_id": "retry-001",
                "current_assignment_id": "assignment-001",
            },
            "retry_forbids_current_assignment",
        ),
        (
            {"phase": "RUNNING", "pending_retry_intent_id": "retry-001"},
            "pending_intent_requires_retry_phase",
        ),
    ],
)
def test_cross_field_invalid_combinations_fail_closed(
    changes: dict[str, object],
    reason: str,
) -> None:
    from qarunner.domain import AttemptExecutionFact, RunDisposition, RunOutcome, RunPhase

    typed = dict(changes)
    for field, enum_type in (
        ("phase", RunPhase),
        ("disposition", RunDisposition),
        ("outcome", RunOutcome),
        ("latest_attempt_fact", AttemptExecutionFact),
    ):
        value = typed.get(field)
        if isinstance(value, str):
            typed[field] = enum_type[value]
    if typed.get("finalization_basis_digest") == "basis":
        typed["finalization_basis_digest"] = _digest("basis")

    with pytest.raises(ValueError, match=reason):
        _state(**typed)


def test_value_is_frozen_and_replace_preserves_valid_state() -> None:
    from qarunner.domain import RunPhase

    original = _state(phase=RunPhase.QUEUED, current_assignment_id=None)

    assert replace(original) == original
    with pytest.raises((AttributeError, TypeError)):
        original.phase = RunPhase.RUNNING  # type: ignore[misc]
