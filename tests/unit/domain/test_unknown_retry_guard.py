"""T-M0-UNKNOWN-001A: unknown is explicit and never auto-retried."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest


def _committed_run():
    from qarunner.domain import (
        Run,
        RunState,
        WorkerAuthority,
        WorkerGeneration,
        WorkerRef,
        WorkerState,
        canonical_digest,
    )

    registered_at = datetime(2026, 7, 12, 12, tzinfo=UTC)
    worker = WorkerGeneration.register(
        ref=WorkerRef(worker_id="worker-001", generation=3),
        host_id="host-001",
        pool_id="pool-default",
        cert_serial="cert-001",
        agent_version="1.0.0",
        capabilities_digest=canonical_digest(
            schema_version="qep.worker-capabilities.v1",
            payload={"executor": "docker"},
        ),
        registered_at=registered_at,
    )
    worker_authority = WorkerAuthority(current_ref=worker.ref)
    ready = worker.transition(
        WorkerState.READY,
        authority=worker_authority,
        expected_version=0,
        occurred_at=registered_at + timedelta(seconds=1),
    )
    spec_digest = canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-001", "profile_id": "profile-001"},
    )
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    offered = queued.offer_assignment(
        assignment_id="assignment-001",
        worker=ready,
        worker_authority=worker_authority,
        spec_digest=spec_digest,
        expected_version=1,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-001",
        worker=ready.ref,
        expected_version=2,
    )
    return claimed.commit_start(
        assignment_id="assignment-001",
        worker=ready.ref,
        start_commit_key="commit-001",
        spec_digest=spec_digest,
        new_attempt_id="attempt-001",
        expected_version=3,
    )


def _observation(*, observation_id: str = "unknown-001", label: str = "baseline"):
    from qarunner.domain import (
        UnknownObservation,
        UnknownReason,
        UnknownSource,
        canonical_digest,
    )

    return UnknownObservation(
        id=observation_id,
        reason=UnknownReason.EXECUTION_STOP_UNPROVEN,
        source=UnknownSource.RECONCILER,
        review_basis_digest=canonical_digest(
            schema_version="qep.unknown-review-basis.v1",
            payload={"label": label},
        ),
        recorded_at=datetime(2026, 7, 12, 13, tzinfo=UTC),
    )


def _attempt_in_state(state):
    from qarunner.domain import AttemptState

    attempt = _committed_run().attempt
    if state is AttemptState.START_COMMITTED:
        return attempt
    provisioning = attempt.transition(AttemptState.PROVISIONING, expected_version=0)
    if state is AttemptState.PROVISIONING:
        return provisioning
    running = provisioning.transition(AttemptState.RUNNING, expected_version=1)
    if state is AttemptState.RUNNING:
        return running
    return running.transition(AttemptState.UPLOADING, expected_version=2)


@pytest.mark.parametrize(
    "state_name",
    ["START_COMMITTED", "PROVISIONING", "RUNNING", "UPLOADING"],
)
def test_every_committed_nonterminal_phase_can_be_marked_unknown(state_name: str) -> None:
    from qarunner.domain import AttemptAuthority, AttemptState

    attempt = _attempt_in_state(AttemptState[state_name])
    observation = _observation()
    authority = AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=attempt.worker,
    )

    unknown = attempt.mark_unknown(
        observation=observation,
        authority=authority,
        expected_version=attempt.version,
    )

    assert unknown.state is AttemptState.ATTEMPT_UNKNOWN
    assert unknown.unknown_observation == observation
    assert unknown.version == attempt.version + 1
    assert attempt.unknown_observation is None
    assert attempt.state is AttemptState[state_name]


def test_generic_transition_cannot_bypass_required_unknown_observation() -> None:
    from qarunner.domain import AttemptState, InvalidTransition

    attempt = _attempt_in_state(AttemptState.RUNNING)

    with pytest.raises(InvalidTransition):
        attempt.transition(AttemptState.ATTEMPT_UNKNOWN, expected_version=attempt.version)

    assert attempt.state is AttemptState.RUNNING
    assert attempt.unknown_observation is None


@pytest.mark.parametrize(
    "terminal_state_name",
    ["PASSED", "TEST_FAILED", "INFRA_FAILED", "CANCELLED"],
)
def test_completed_terminal_attempt_cannot_be_reclassified_unknown(
    terminal_state_name: str,
) -> None:
    from qarunner.domain import AttemptAuthority, AttemptState, InvalidTransition

    running = _attempt_in_state(AttemptState.RUNNING)
    terminal = replace(running, state=AttemptState[terminal_state_name])
    authority = AttemptAuthority(
        current_fence=terminal.fence,
        current_worker=terminal.worker,
    )

    with pytest.raises(InvalidTransition):
        terminal.mark_unknown(
            observation=_observation(),
            authority=authority,
            expected_version=terminal.version,
        )

    assert terminal.state is AttemptState[terminal_state_name]
    assert terminal.unknown_observation is None


def test_unknown_observation_exact_replay_wins_before_cas() -> None:
    from qarunner.domain import AttemptAuthority, AttemptState

    attempt = _attempt_in_state(AttemptState.UPLOADING)
    observation = _observation()
    authority = AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=attempt.worker,
    )
    first = attempt.mark_unknown(
        observation=observation,
        authority=authority,
        expected_version=attempt.version,
    )

    replay = first.mark_unknown(
        observation=observation,
        authority=authority,
        expected_version=attempt.version,
    )

    assert replay is first
    assert replay.version == first.version


def test_unknown_observation_id_reuse_with_changed_basis_conflicts() -> None:
    from qarunner.domain import (
        AttemptAuthority,
        AttemptState,
        UnknownObservationConflict,
    )

    attempt = _attempt_in_state(AttemptState.RUNNING)
    authority = AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=attempt.worker,
    )
    first = attempt.mark_unknown(
        observation=_observation(label="first"),
        authority=authority,
        expected_version=attempt.version,
    )

    with pytest.raises(UnknownObservationConflict) as caught:
        first.mark_unknown(
            observation=_observation(label="changed"),
            authority=authority,
            expected_version=first.version,
        )

    assert caught.value.code == "unknown_observation_conflict"
    assert caught.value.attempt_id == attempt.id
    assert first.unknown_observation == _observation(label="first")


def test_unknown_attempt_rejects_a_second_observation_identity() -> None:
    from qarunner.domain import AttemptAuthority, AttemptState, InvalidTransition

    attempt = _attempt_in_state(AttemptState.RUNNING)
    authority = AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=attempt.worker,
    )
    first = attempt.mark_unknown(
        observation=_observation(observation_id="unknown-001"),
        authority=authority,
        expected_version=attempt.version,
    )

    with pytest.raises(InvalidTransition):
        first.mark_unknown(
            observation=_observation(observation_id="unknown-002"),
            authority=authority,
            expected_version=first.version,
        )

    assert first.unknown_observation == _observation(observation_id="unknown-001")


def test_attempt_rejects_a_non_observation_value_stably() -> None:
    from qarunner.domain import AttemptAuthority, AttemptState, DomainValidationError

    attempt = _attempt_in_state(AttemptState.RUNNING)
    authority = AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=attempt.worker,
    )

    with pytest.raises(DomainValidationError) as caught:
        attempt.mark_unknown(
            observation="bad",  # type: ignore[arg-type]
            authority=authority,
            expected_version=attempt.version,
        )

    assert caught.value.entity_type == "attempt"
    assert caught.value.field == "unknown_observation"
    assert caught.value.reason == "invalid_type"


def test_attempt_rehydration_requires_unknown_state_and_observation_to_match() -> None:
    from qarunner.domain import AttemptAuthority, AttemptState, DomainValidationError

    running = _attempt_in_state(AttemptState.RUNNING)
    authority = AttemptAuthority(
        current_fence=running.fence,
        current_worker=running.worker,
    )
    unknown = running.mark_unknown(
        observation=_observation(),
        authority=authority,
        expected_version=running.version,
    )

    with pytest.raises(DomainValidationError) as missing_observation:
        replace(running, state=AttemptState.ATTEMPT_UNKNOWN)
    with pytest.raises(DomainValidationError) as nonunknown_observation:
        replace(unknown, state=AttemptState.RUNNING)

    assert missing_observation.value.field == "unknown_observation"
    assert missing_observation.value.reason == "required_for_unknown"
    assert nonunknown_observation.value.field == "unknown_observation"
    assert nonunknown_observation.value.reason == "only_allowed_for_unknown"


def test_attempt_rehydration_rejects_raw_state_or_fake_unknown_observation() -> None:
    from qarunner.domain import AttemptState, DomainValidationError

    running = _attempt_in_state(AttemptState.RUNNING)

    with pytest.raises(DomainValidationError) as raw_state:
        replace(running, state="attempt_unknown")
    with pytest.raises(DomainValidationError) as fake_observation:
        replace(
            running,
            state=AttemptState.ATTEMPT_UNKNOWN,
            unknown_observation="bad",
        )

    assert raw_state.value.field == "state"
    assert raw_state.value.reason == "unknown_state"
    assert fake_observation.value.field == "unknown_observation"
    assert fake_observation.value.reason == "invalid_type"


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"id": 123}, "id", "not_string", id="id-type"),
        pytest.param({"id": ""}, "id", "empty", id="id-empty"),
        pytest.param({"reason": "bad"}, "reason", "unknown", id="reason"),
        pytest.param({"source": "bad"}, "source", "unknown", id="source"),
        pytest.param(
            {"review_basis_digest": "bad"},
            "review_basis_digest",
            "not_digest",
            id="digest",
        ),
        pytest.param(
            {"recorded_at": "bad"},
            "recorded_at",
            "not_datetime",
            id="time-type",
        ),
        pytest.param(
            {"recorded_at": datetime(2026, 7, 12, 13)},
            "recorded_at",
            "not_utc",
            id="time-naive",
        ),
        pytest.param(
            {"recorded_at": datetime(2026, 7, 12, 13, tzinfo=timezone(timedelta(hours=1)))},
            "recorded_at",
            "not_utc",
            id="time-offset",
        ),
    ],
)
def test_unknown_observation_rejects_invalid_values_stably(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_observation(), **changes)

    assert caught.value.entity_type == "unknown_observation"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_run_records_unknown_on_its_latest_attempt_without_changing_authority() -> None:
    from qarunner.domain import AttemptState

    committed = _committed_run()
    run = committed.run
    observation = _observation()

    unknown_run = run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=observation,
        expected_version=run.version,
        expected_attempt_version=committed.attempt.version,
    )

    assert unknown_run.attempts[-1].state is AttemptState.ATTEMPT_UNKNOWN
    assert unknown_run.attempts[-1].unknown_observation == observation
    assert unknown_run.current_fence == run.current_fence == 1
    assert unknown_run.assignment == run.assignment
    assert unknown_run.version == run.version + 1
    assert run.attempts[-1].state is AttemptState.START_COMMITTED


def test_run_unknown_record_replays_without_advancing_versions() -> None:
    committed = _committed_run()
    observation = _observation()
    first = committed.run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=observation,
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )

    replay = first.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=observation,
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )

    assert replay is first


@pytest.mark.parametrize("run_kind", ["empty", "wrong-attempt"])
def test_run_rejects_unknown_for_a_noncurrent_attempt(run_kind: str) -> None:
    from qarunner.domain import AttemptUnknownReviewRequired, Run

    committed = _committed_run()
    run = Run.create(run_id="empty-run") if run_kind == "empty" else committed.run

    with pytest.raises(AttemptUnknownReviewRequired) as caught:
        run.mark_current_attempt_unknown(
            attempt_id="missing-attempt",
            observation=_observation(),
            expected_version=run.version,
            expected_attempt_version=0,
        )

    assert caught.value.reason == "source_attempt_not_current"
    assert run.current_fence == (0 if run_kind == "empty" else 1)


def test_automatic_retry_of_unknown_is_stably_rejected_without_mutation() -> None:
    from qarunner.domain import AttemptUnknownReviewRequired

    committed = _committed_run()
    unknown_run = committed.run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=_observation(),
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )
    before = unknown_run

    with pytest.raises(AttemptUnknownReviewRequired) as caught:
        unknown_run.ensure_automatic_retry_source_is_not_unknown(
            attempt_id=committed.attempt.id,
            expected_version=unknown_run.version,
        )

    assert caught.value.code == "attempt_unknown_review_required"
    assert caught.value.run_id == unknown_run.id
    assert caught.value.attempt_id == committed.attempt.id
    assert caught.value.reason == "manual_adjudication_required"
    assert unknown_run == before
    assert unknown_run.current_fence == 1
    assert len(unknown_run.attempts) == 1


def test_unknown_review_guard_wins_before_a_stale_retry_cas() -> None:
    from qarunner.domain import AttemptUnknownReviewRequired

    committed = _committed_run()
    unknown_run = committed.run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=_observation(),
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )

    with pytest.raises(AttemptUnknownReviewRequired) as caught:
        unknown_run.ensure_automatic_retry_source_is_not_unknown(
            attempt_id=committed.attempt.id,
            expected_version=committed.run.version,
        )

    assert caught.value.reason == "manual_adjudication_required"
    assert unknown_run.current_fence == 1
    assert len(unknown_run.attempts) == 1


def test_automatic_retry_guard_allows_a_nonunknown_source_for_later_policy_checks() -> None:
    committed = _committed_run()

    result = committed.run.ensure_automatic_retry_source_is_not_unknown(
        attempt_id=committed.attempt.id,
        expected_version=committed.run.version,
    )

    assert result is None
    assert committed.run.current_fence == 1
    assert len(committed.run.attempts) == 1


@pytest.mark.parametrize("run_kind", ["empty", "wrong-attempt"])
def test_automatic_retry_rejects_a_noncurrent_attempt(run_kind: str) -> None:
    from qarunner.domain import AttemptUnknownReviewRequired, Run

    committed = _committed_run()
    run = Run.create(run_id="empty-run") if run_kind == "empty" else committed.run

    with pytest.raises(AttemptUnknownReviewRequired) as caught:
        run.ensure_automatic_retry_source_is_not_unknown(
            attempt_id="missing-attempt",
            expected_version=run.version,
        )

    assert caught.value.reason == "source_attempt_not_current"
    assert run.current_fence == (0 if run_kind == "empty" else 1)
