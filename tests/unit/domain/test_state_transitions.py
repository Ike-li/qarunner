"""T-M0-STATE-001: state transitions reject illegal commands without mutation."""

import pytest


def test_batch_rejects_skipping_from_draft_to_running_without_mutation() -> None:
    """A rejected transition keeps the immutable Batch at its prior version."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    batch = Batch.create(batch_id="batch-001")

    with pytest.raises(InvalidTransition) as caught:
        batch.transition(BatchState.RUNNING, expected_version=0)

    assert caught.value.code == "invalid_transition"
    assert caught.value.entity_type == "batch"
    assert caught.value.entity_id == "batch-001"
    assert caught.value.current_state == BatchState.DRAFT
    assert caught.value.requested_state == BatchState.RUNNING
    assert batch.state == BatchState.DRAFT
    assert batch.version == 0


def test_batch_legal_transition_returns_a_new_version() -> None:
    """A legal transition advances state/version without mutating its source."""
    from qarunner.domain import Batch, BatchState

    draft = Batch.create(batch_id="batch-001")

    validating = draft.transition(BatchState.VALIDATING, expected_version=0)

    assert validating.state == BatchState.VALIDATING
    assert validating.version == 1
    assert draft.state == BatchState.DRAFT
    assert draft.version == 0


def test_batch_rejects_a_stale_expected_version_without_mutation() -> None:
    """State commands use CAS semantics before applying an otherwise legal edge."""
    from qarunner.domain import Batch, BatchState, VersionConflict

    batch = Batch.create(batch_id="batch-001")

    with pytest.raises(VersionConflict) as caught:
        batch.transition(BatchState.VALIDATING, expected_version=7)

    assert caught.value.code == "version_conflict"
    assert caught.value.entity_type == "batch"
    assert caught.value.entity_id == "batch-001"
    assert caught.value.current_version == 0
    assert caught.value.expected_version == 7
    assert batch.state == BatchState.DRAFT
    assert batch.version == 0


def test_run_rejects_skipping_from_planned_to_assigned_without_mutation() -> None:
    """A Run must enter its queue before an Assignment can reserve it."""
    from qarunner.domain import InvalidTransition, Run, RunState

    run = Run.create(run_id="run-001")

    with pytest.raises(InvalidTransition) as caught:
        run.transition(RunState.ASSIGNED, expected_version=0)

    assert caught.value.code == "invalid_transition"
    assert caught.value.entity_type == "run"
    assert caught.value.entity_id == "run-001"
    assert caught.value.current_state == RunState.PLANNED
    assert caught.value.requested_state == RunState.ASSIGNED
    assert run.state == RunState.PLANNED
    assert run.version == 0


def test_run_legal_transition_returns_a_new_version() -> None:
    """Queueing a planned Run advances an immutable aggregate by one version."""
    from qarunner.domain import Run, RunState

    planned = Run.create(run_id="run-001")

    queued = planned.transition(RunState.QUEUED, expected_version=0)

    assert queued.state == RunState.QUEUED
    assert queued.version == 1
    assert planned.state == RunState.PLANNED
    assert planned.version == 0


def test_attempt_rejects_skipping_provisioning_without_mutation() -> None:
    """A committed Attempt cannot claim that test execution already started."""
    from qarunner.domain import Attempt, AttemptState, InvalidTransition

    attempt = Attempt.create(attempt_id="attempt-001")

    with pytest.raises(InvalidTransition) as caught:
        attempt.transition(AttemptState.RUNNING, expected_version=0)

    assert caught.value.code == "invalid_transition"
    assert caught.value.entity_type == "attempt"
    assert caught.value.entity_id == "attempt-001"
    assert caught.value.current_state == AttemptState.START_COMMITTED
    assert caught.value.requested_state == AttemptState.RUNNING
    assert attempt.state == AttemptState.START_COMMITTED
    assert attempt.version == 0


def test_attempt_legal_transition_returns_a_new_version() -> None:
    """Provisioning starts from a durable commit and advances one version."""
    from qarunner.domain import Attempt, AttemptState

    committed = Attempt.create(attempt_id="attempt-001")

    provisioning = committed.transition(AttemptState.PROVISIONING, expected_version=0)

    assert provisioning.state == AttemptState.PROVISIONING
    assert provisioning.version == 1
    assert committed.state == AttemptState.START_COMMITTED
    assert committed.version == 0
