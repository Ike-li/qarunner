"""T-M0-STATE-001: state transitions reject illegal commands without mutation.

T-M0-STATE-001A narrows the Batch value, CAS, and absorbing-state contract.
T-M0-STATE-001C narrows runtime expected-version values at the shared CAS helper.
"""

import pytest


@pytest.mark.parametrize("batch_id", ["", "   ", 123])
def test_batch_rejects_invalid_identity(batch_id: object) -> None:
    """A Batch cannot enter the domain without a usable opaque identity."""
    from qarunner.domain import Batch, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        Batch.create(batch_id=batch_id)  # type: ignore[arg-type]

    assert caught.value.code == "domain_validation_error"
    assert caught.value.entity_type == "batch"
    assert caught.value.field == "id"
    assert caught.value.reason == "invalid"


def test_batch_rehydration_rejects_arbitrary_string_state() -> None:
    """Persisted state must resolve to the declared BatchState vocabulary."""
    from qarunner.domain import Batch, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        Batch(id="batch-001", state="running", version=0)  # type: ignore[arg-type]

    assert caught.value.code == "domain_validation_error"
    assert caught.value.entity_type == "batch"
    assert caught.value.field == "state"
    assert caught.value.reason == "unknown"


@pytest.mark.parametrize("version", [True, 1.5, -1])
def test_batch_rehydration_rejects_invalid_version(version: object) -> None:
    """Persisted Batch versions are non-negative integers, excluding booleans."""
    from qarunner.domain import Batch, BatchState, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        Batch(id="batch-001", state=BatchState.DRAFT, version=version)  # type: ignore[arg-type]

    assert caught.value.code == "domain_validation_error"
    assert caught.value.entity_type == "batch"
    assert caught.value.field == "version"
    assert caught.value.reason == "invalid"


def test_batch_transition_rejects_arbitrary_string_target_without_mutation() -> None:
    """A command target must use BatchState rather than an arbitrary string."""
    from qarunner.domain import Batch, DomainValidationError

    batch = Batch.create(batch_id="batch-001")

    with pytest.raises(DomainValidationError) as caught:
        batch.transition("not-a-batch-state", expected_version=0)  # type: ignore[arg-type]

    assert caught.value.code == "domain_validation_error"
    assert caught.value.entity_type == "batch"
    assert caught.value.field == "state"
    assert caught.value.reason == "unknown"
    assert batch.version == 0


def test_batch_stale_cas_precedes_target_state_validation() -> None:
    """A stale command cannot probe later target-state validation."""
    from qarunner.domain import Batch, VersionConflict

    batch = Batch.create(batch_id="batch-001")

    with pytest.raises(VersionConflict) as caught:
        batch.transition("not-a-batch-state", expected_version=7)  # type: ignore[arg-type]

    assert caught.value.code == "version_conflict"
    assert caught.value.current_version == 0
    assert caught.value.expected_version == 7
    assert batch.version == 0


def _committed_attempt():
    from qarunner.domain import Attempt, WorkerRef, canonical_digest

    return Attempt.create(
        attempt_id="attempt-001",
        run_id="run-001",
        attempt_no=1,
        fence=1,
        assignment_id="assignment-001",
        worker=WorkerRef(worker_id="worker-001", generation=1),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"run_id": "run-001"},
        ),
        start_commit_key="commit-001",
    )


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


@pytest.mark.parametrize(
    ("source_name", "target_name"),
    [("QUEUED", "RUNNING"), ("RUNNING", "FINALIZING")],
)
def test_batch_shared_nonterminal_edges_return_a_new_version(
    source_name: str, target_name: str
) -> None:
    """The PRD and detailed design agree on these execution-phase edges."""
    from qarunner.domain import Batch, BatchState

    source_state = BatchState[source_name]
    source = Batch(id="batch-001", state=source_state, version=3)

    advanced = source.transition(BatchState[target_name], expected_version=3)

    assert advanced.state is BatchState[target_name]
    assert advanced.version == 4
    assert source.state is source_state
    assert source.version == 3


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


@pytest.mark.parametrize(
    ("current_version", "expected_version", "reason"),
    [
        pytest.param(0, False, "not_integer", id="false-equals-zero"),
        pytest.param(1, True, "not_integer", id="true-equals-one"),
        pytest.param(0, 0.0, "not_integer", id="float-zero-equals-zero"),
        pytest.param(1, 1.0, "not_integer", id="float-one-equals-one"),
        pytest.param(0, "0", "not_integer", id="string"),
        pytest.param(0, None, "not_integer", id="none"),
        pytest.param(0, -1, "negative", id="negative"),
    ],
)
def test_batch_rejects_invalid_expected_version_runtime_without_mutation(
    current_version: int,
    expected_version: object,
    reason: str,
) -> None:
    """CAS inputs must be non-negative integers before equality is considered."""
    from qarunner.domain import Batch, BatchState, DomainValidationError

    batch = Batch(id="batch-001", state=BatchState.DRAFT, version=current_version)

    with pytest.raises(DomainValidationError) as caught:
        batch.transition(
            BatchState.VALIDATING,
            expected_version=expected_version,  # type: ignore[arg-type]
        )

    assert caught.value.code == "domain_validation_error"
    assert caught.value.entity_type == "batch"
    assert caught.value.field == "expected_version"
    assert caught.value.reason == reason
    assert batch.state is BatchState.DRAFT
    assert batch.version == current_version


@pytest.mark.parametrize(
    "state_name",
    ["SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED", "REJECTED"],
)
def test_batch_terminal_states_absorb_every_generic_transition(state_name: str) -> None:
    """A rehydrated terminal Batch cannot regress or be reclassified."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    state = BatchState[state_name]
    terminal = Batch(id="batch-001", state=state, version=11)

    for target in BatchState:
        with pytest.raises(InvalidTransition) as caught:
            terminal.transition(target, expected_version=11)

        assert caught.value.current_state is state
        assert caught.value.requested_state is target
        assert caught.value.current_version == 11
        assert caught.value.expected_version == 11
        assert terminal.state is state
        assert terminal.version == 11


@pytest.mark.parametrize("target_name", ["SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"])
def test_batch_finalizing_requires_a_fact_aware_finalize_command(target_name: str) -> None:
    """Generic transitions cannot bypass future Run/Evidence reconciliation."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    finalizing = Batch(id="batch-001", state=BatchState.FINALIZING, version=8)
    target = BatchState[target_name]

    with pytest.raises(InvalidTransition) as caught:
        finalizing.transition(target, expected_version=8)

    assert caught.value.current_state is BatchState.FINALIZING
    assert caught.value.requested_state is target
    assert finalizing.state is BatchState.FINALIZING
    assert finalizing.version == 8


def test_run_uses_shared_expected_version_runtime_validation() -> None:
    """Run state commands reject bool before Python can equate it with zero."""
    from qarunner.domain import DomainValidationError, Run, RunState

    run = Run.create(run_id="run-001")

    with pytest.raises(DomainValidationError) as caught:
        run.transition(RunState.QUEUED, expected_version=False)  # type: ignore[arg-type]

    assert caught.value.entity_type == "run"
    assert caught.value.field == "expected_version"
    assert caught.value.reason == "not_integer"
    assert run.state is RunState.PLANNED
    assert run.version == 0


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


def test_run_owned_attempt_cas_precedes_run_cas_when_both_are_stale() -> None:
    """The owned Attempt rejects a dual-stale command before the outer Run CAS."""
    from qarunner.domain import AttemptState, VersionConflict
    from tests.unit.domain.test_assignment_precommit_closure import _committed_initial

    committed, _ = _committed_initial()
    provisioning = committed.run.transition_current_attempt(
        attempt_id=committed.attempt.id,
        target=AttemptState.PROVISIONING,
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )
    current = provisioning.attempts[-1]
    source_run_version = provisioning.version
    source_attempt_version = current.version

    assert committed.run.version >= 0
    assert committed.attempt.version >= 0
    assert committed.run.version != source_run_version
    assert committed.attempt.version != source_attempt_version

    with pytest.raises(VersionConflict) as caught:
        provisioning.transition_current_attempt(
            attempt_id=current.id,
            target=AttemptState.RUNNING,
            expected_version=committed.run.version,
            expected_attempt_version=committed.attempt.version,
        )

    assert caught.value.code == "version_conflict"
    assert caught.value.entity_type == "attempt"
    assert caught.value.entity_id == current.id
    assert caught.value.current_version == source_attempt_version
    assert caught.value.expected_version == committed.attempt.version
    assert provisioning.version == source_run_version
    assert provisioning.attempts[-1] is current
    assert current.state is AttemptState.PROVISIONING
    assert current.version == source_attempt_version


def test_attempt_uses_shared_expected_version_runtime_validation() -> None:
    """Attempt state commands reject floats before equality is considered."""
    from qarunner.domain import AttemptState, DomainValidationError

    attempt = _committed_attempt()

    with pytest.raises(DomainValidationError) as caught:
        attempt.transition(AttemptState.PROVISIONING, expected_version=0.0)  # type: ignore[arg-type]

    assert caught.value.entity_type == "attempt"
    assert caught.value.field == "expected_version"
    assert caught.value.reason == "not_integer"
    assert attempt.state is AttemptState.START_COMMITTED
    assert attempt.version == 0


def test_attempt_rejects_skipping_provisioning_without_mutation() -> None:
    """A committed Attempt cannot claim that test execution already started."""
    from qarunner.domain import AttemptState, InvalidTransition

    attempt = _committed_attempt()

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
    from qarunner.domain import AttemptState

    committed = _committed_attempt()

    provisioning = committed.transition(AttemptState.PROVISIONING, expected_version=0)

    assert provisioning.state == AttemptState.PROVISIONING
    assert provisioning.version == 1
    assert committed.state == AttemptState.START_COMMITTED
    assert committed.version == 0
