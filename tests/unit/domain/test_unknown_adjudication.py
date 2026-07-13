"""T-M0-UNKNOWN-001B: adjudication appends without rewriting unknown facts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-unknown-fact.v1",
        payload={"label": label},
    )


def _observation():
    from qarunner.domain import UnknownObservation, UnknownReason, UnknownSource

    return UnknownObservation(
        id="unknown-001",
        reason=UnknownReason.EXECUTION_STOP_UNPROVEN,
        source=UnknownSource.RECONCILER,
        review_basis_digest=_digest("review-basis"),
        recorded_at=datetime(2026, 7, 12, 13, tzinfo=UTC),
    )


def _unknown_attempt():
    from qarunner.domain import Attempt, AttemptAuthority, WorkerRef

    attempt = Attempt.create(
        attempt_id="attempt-001",
        run_id="run-001",
        attempt_no=1,
        fence=1,
        assignment_id="assignment-001",
        worker=WorkerRef(worker_id="worker-001", generation=3),
        spec_digest=_digest("execution-spec"),
        start_commit_key="commit-001",
    )
    return attempt.mark_unknown(
        observation=_observation(),
        authority=AttemptAuthority(
            current_fence=attempt.fence,
            current_worker=attempt.worker,
        ),
        expected_version=0,
    )


def _adjudication(
    *,
    adjudication_id: str = "adjudication-001",
    attempt_id: str = "attempt-001",
    decision_name: str = "CONFIRM_STOPPED_THEN_RETRY",
    actor_id: str = "admin-001",
    reason: str = "worker host termination verified",
    occurred_at: datetime = datetime(2026, 7, 12, 14, tzinfo=UTC),
):
    from qarunner.domain import UnknownAdjudication, UnknownAdjudicationDecision

    decision = UnknownAdjudicationDecision[decision_name]
    proof_digest = None
    risk_approver_id = None
    risk_acceptance_digest = None
    evidence_root_digest = None
    if decision is UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY:
        proof_digest = _digest("stopped-proof")
    elif decision is UnknownAdjudicationDecision.ACCEPT_DUPLICATE_RISK_THEN_RETRY:
        risk_approver_id = "business-owner-001"
        risk_acceptance_digest = _digest("risk-acceptance")
    elif decision is UnknownAdjudicationDecision.MARK_COMPLETED_FROM_VERIFIED_EVIDENCE:
        evidence_root_digest = _digest("verified-evidence")
    return UnknownAdjudication(
        id=adjudication_id,
        attempt_id=attempt_id,
        unknown_observation_digest=_observation().digest,
        decision=decision,
        actor_id=actor_id,
        reason=reason,
        occurred_at=occurred_at,
        proof_digest=proof_digest,
        risk_approver_id=risk_approver_id,
        risk_acceptance_digest=risk_acceptance_digest,
        evidence_root_digest=evidence_root_digest,
    )


def _committed_run():
    from qarunner.domain import (
        Run,
        RunState,
        WorkerAuthority,
        WorkerGeneration,
        WorkerRef,
        WorkerState,
    )

    registered_at = datetime(2026, 7, 12, 12, tzinfo=UTC)
    worker = WorkerGeneration.register(
        ref=WorkerRef(worker_id="worker-001", generation=3),
        host_id="host-001",
        pool_id="pool-default",
        cert_serial="cert-001",
        agent_version="1.0.0",
        capabilities_digest=_digest("capabilities"),
        registered_at=registered_at,
    )
    authority = WorkerAuthority(current_ref=worker.ref)
    ready = worker.transition(
        WorkerState.READY,
        authority=authority,
        expected_version=0,
        occurred_at=registered_at + timedelta(seconds=1),
    )
    spec_digest = _digest("execution-spec")
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    offered = queued.offer_assignment(
        assignment_id="assignment-001",
        worker=ready,
        worker_authority=authority,
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


@pytest.mark.parametrize(
    ("decision_name", "permits_retry"),
    [
        pytest.param("CONFIRM_STOPPED_THEN_RETRY", True, id="stopped"),
        pytest.param("ACCEPT_DUPLICATE_RISK_THEN_RETRY", True, id="risk-accepted"),
        pytest.param("MARK_INFRA_FAILED_NO_RETRY", False, id="infra-no-retry"),
        pytest.param(
            "MARK_COMPLETED_FROM_VERIFIED_EVIDENCE",
            False,
            id="verified-completion",
        ),
    ],
)
def test_adjudication_decision_declares_retry_authority(
    decision_name: str, permits_retry: bool
) -> None:
    from qarunner.domain import UnknownAdjudicationDecision

    assert UnknownAdjudicationDecision[decision_name].permits_retry is permits_retry


@pytest.mark.parametrize(
    ("decision_name", "changes", "field", "reason"),
    [
        pytest.param(
            "CONFIRM_STOPPED_THEN_RETRY",
            {"proof_digest": None},
            "proof_digest",
            "required_for_decision",
            id="stopped-proof",
        ),
        pytest.param(
            "ACCEPT_DUPLICATE_RISK_THEN_RETRY",
            {"risk_approver_id": None},
            "risk_approver_id",
            "required_for_decision",
            id="risk-approver",
        ),
        pytest.param(
            "ACCEPT_DUPLICATE_RISK_THEN_RETRY",
            {"risk_acceptance_digest": None},
            "risk_acceptance_digest",
            "required_for_decision",
            id="risk-digest",
        ),
        pytest.param(
            "MARK_COMPLETED_FROM_VERIFIED_EVIDENCE",
            {"evidence_root_digest": None},
            "evidence_root_digest",
            "required_for_decision",
            id="verified-evidence",
        ),
    ],
)
def test_adjudication_decision_requires_its_authoritative_basis(
    decision_name: str,
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_adjudication(decision_name=decision_name), **changes)

    assert caught.value.entity_type == "unknown_adjudication"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("decision_name", "changes", "field"),
    [
        pytest.param(
            "CONFIRM_STOPPED_THEN_RETRY",
            {"risk_approver_id": "owner", "risk_acceptance_digest": _digest("risk")},
            "risk_approver_id",
            id="stopped-plus-risk",
        ),
        pytest.param(
            "CONFIRM_STOPPED_THEN_RETRY",
            {"evidence_root_digest": _digest("evidence")},
            "evidence_root_digest",
            id="stopped-plus-evidence",
        ),
        pytest.param(
            "ACCEPT_DUPLICATE_RISK_THEN_RETRY",
            {"proof_digest": _digest("proof")},
            "proof_digest",
            id="risk-plus-proof",
        ),
        pytest.param(
            "MARK_INFRA_FAILED_NO_RETRY",
            {"proof_digest": _digest("proof")},
            "proof_digest",
            id="infra-plus-proof",
        ),
        pytest.param(
            "MARK_COMPLETED_FROM_VERIFIED_EVIDENCE",
            {"proof_digest": _digest("proof")},
            "proof_digest",
            id="completed-plus-proof",
        ),
        pytest.param(
            "MARK_COMPLETED_FROM_VERIFIED_EVIDENCE",
            {"risk_approver_id": "owner", "risk_acceptance_digest": _digest("risk")},
            "risk_approver_id",
            id="completed-plus-risk",
        ),
    ],
)
def test_adjudication_decision_rejects_unrelated_authority_basis(
    decision_name: str, changes: dict[str, object], field: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_adjudication(decision_name=decision_name), **changes)

    assert caught.value.field == field
    assert caught.value.reason == "not_allowed_for_decision"


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"id": 123}, "id", "not_string", id="id-type"),
        pytest.param({"id": ""}, "id", "empty", id="id-empty"),
        pytest.param({"attempt_id": ""}, "attempt_id", "empty", id="attempt-empty"),
        pytest.param(
            {"unknown_observation_digest": "bad"},
            "unknown_observation_digest",
            "not_digest",
            id="observation-digest",
        ),
        pytest.param({"decision": "bad"}, "decision", "unknown", id="decision"),
        pytest.param({"actor_id": ""}, "actor_id", "empty", id="actor"),
        pytest.param({"reason": 123}, "reason", "not_string", id="reason-type"),
        pytest.param(
            {"occurred_at": datetime(2026, 7, 12, 14)},
            "occurred_at",
            "not_utc",
            id="time",
        ),
        pytest.param({"proof_digest": "bad"}, "proof_digest", "not_digest", id="proof"),
        pytest.param(
            {"risk_approver_id": ""},
            "risk_approver_id",
            "empty",
            id="risk-approver",
        ),
        pytest.param(
            {"risk_acceptance_digest": "bad"},
            "risk_acceptance_digest",
            "not_digest",
            id="risk-digest",
        ),
        pytest.param(
            {"evidence_root_digest": "bad"},
            "evidence_root_digest",
            "not_digest",
            id="evidence-digest",
        ),
    ],
)
def test_adjudication_rejects_invalid_values_stably(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_adjudication(), **changes)

    assert caught.value.entity_type == "unknown_adjudication"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_adjudication_appends_without_rewriting_unknown_facts() -> None:
    from qarunner.domain import AttemptState

    unknown = _unknown_attempt()
    adjudication = _adjudication()

    adjudicated = unknown.append_unknown_adjudication(
        adjudication=adjudication,
        expected_version=unknown.version,
    )

    assert adjudicated.state is AttemptState.ATTEMPT_UNKNOWN
    assert adjudicated.unknown_observation == unknown.unknown_observation
    assert adjudicated.events == unknown.events
    assert adjudicated.evidence == unknown.evidence
    assert adjudicated.adjudications == (adjudication,)
    assert adjudicated.version == unknown.version + 1
    assert unknown.adjudications == ()


def test_attempt_rejects_a_non_adjudication_value_stably() -> None:
    from qarunner.domain import DomainValidationError

    unknown = _unknown_attempt()

    with pytest.raises(DomainValidationError) as caught:
        unknown.append_unknown_adjudication(
            adjudication="bad",  # type: ignore[arg-type]
            expected_version=unknown.version,
        )

    assert caught.value.field == "adjudications"
    assert caught.value.reason == "invalid_type"


def test_adjudication_exact_replay_wins_before_cas() -> None:
    unknown = _unknown_attempt()
    adjudication = _adjudication()
    first = unknown.append_unknown_adjudication(
        adjudication=adjudication,
        expected_version=unknown.version,
    )

    replay = first.append_unknown_adjudication(
        adjudication=adjudication,
        expected_version=unknown.version,
    )

    assert replay is first
    assert replay.adjudications == (adjudication,)


def test_adjudication_id_reuse_with_changed_content_conflicts() -> None:
    from qarunner.domain import AdjudicationConflict

    unknown = _unknown_attempt()
    first = unknown.append_unknown_adjudication(
        adjudication=_adjudication(reason="first"),
        expected_version=unknown.version,
    )

    with pytest.raises(AdjudicationConflict) as caught:
        first.append_unknown_adjudication(
            adjudication=_adjudication(reason="changed"),
            expected_version=first.version,
        )

    assert caught.value.code == "adjudication_conflict"
    assert caught.value.attempt_id == unknown.id
    assert len(first.adjudications) == 1


def test_second_adjudication_is_appended_after_the_original_record() -> None:
    unknown = _unknown_attempt()
    first_record = _adjudication(adjudication_id="adjudication-001")
    second_record = replace(
        _adjudication(
            adjudication_id="adjudication-002",
            decision_name="MARK_INFRA_FAILED_NO_RETRY",
            reason="operator closed without retry",
        ),
        supersedes_adjudication_id="adjudication-001",
    )
    first = unknown.append_unknown_adjudication(
        adjudication=first_record,
        expected_version=unknown.version,
    )

    second = first.append_unknown_adjudication(
        adjudication=second_record,
        expected_version=first.version,
    )

    assert second.adjudications == (first_record, second_record)
    assert first.adjudications == (first_record,)


def test_second_adjudication_without_explicit_supersession_is_rejected() -> None:
    from qarunner.domain import DomainValidationError

    unknown = _unknown_attempt()
    first = unknown.append_unknown_adjudication(
        adjudication=_adjudication(adjudication_id="adjudication-001"),
        expected_version=unknown.version,
    )

    with pytest.raises(DomainValidationError) as caught:
        first.append_unknown_adjudication(
            adjudication=_adjudication(
                adjudication_id="adjudication-002",
                decision_name="MARK_INFRA_FAILED_NO_RETRY",
            ),
            expected_version=first.version,
        )

    assert caught.value.field == "adjudications"
    assert caught.value.reason == "supersession_mismatch"


def test_mark_completed_is_reserved_until_verified_late_evidence_is_supported() -> None:
    from qarunner.domain import DomainValidationError

    unknown = _unknown_attempt()

    with pytest.raises(DomainValidationError) as caught:
        unknown.append_unknown_adjudication(
            adjudication=_adjudication(decision_name="MARK_COMPLETED_FROM_VERIFIED_EVIDENCE"),
            expected_version=unknown.version,
        )

    assert caught.value.field == "adjudications"
    assert caught.value.reason == "verified_evidence_adjudication_not_supported"


@pytest.mark.parametrize(
    ("case", "field", "reason"),
    [
        pytest.param("mutable", "adjudications", "not_tuple", id="mutable"),
        pytest.param("wrong-attempt", "adjudications", "attempt_mismatch", id="attempt"),
        pytest.param(
            "wrong-observation", "adjudications", "observation_mismatch", id="observation"
        ),
        pytest.param("duplicate-id", "adjudications", "duplicate_id", id="duplicate"),
        pytest.param("nonunknown", "adjudications", "only_allowed_for_unknown", id="state"),
        pytest.param("invalid-record", "adjudications", "invalid_type", id="record-type"),
        pytest.param("before-observation", "adjudications", "before_unknown", id="time-order"),
        pytest.param(
            "time-regression",
            "adjudications",
            "occurred_at_not_monotonic",
            id="history-time",
        ),
    ],
)
def test_attempt_rehydration_rejects_invalid_adjudication_history(
    case: str, field: str, reason: str
) -> None:
    from qarunner.domain import AttemptState, DomainValidationError

    unknown = _unknown_attempt()
    record = _adjudication()
    if case == "mutable":
        changes = {"adjudications": [record]}
    elif case == "wrong-attempt":
        changes = {"adjudications": (replace(record, attempt_id="attempt-999"),)}
    elif case == "wrong-observation":
        changes = {
            "adjudications": (
                replace(record, unknown_observation_digest=_digest("wrong-observation")),
            )
        }
    elif case == "duplicate-id":
        changes = {"adjudications": (record, record)}
    elif case == "invalid-record":
        changes = {"adjudications": ("bad",)}
    elif case == "before-observation":
        changes = {
            "adjudications": (replace(record, occurred_at=datetime(2026, 7, 12, 12, tzinfo=UTC)),)
        }
    elif case == "time-regression":
        changes = {
            "adjudications": (
                record,
                replace(
                    record,
                    id="adjudication-002",
                    occurred_at=datetime(2026, 7, 12, 13, 30, tzinfo=UTC),
                    supersedes_adjudication_id=record.id,
                ),
            )
        }
    else:
        changes = {
            "state": AttemptState.RUNNING,
            "unknown_observation": None,
            "adjudications": (record,),
        }

    with pytest.raises(DomainValidationError) as caught:
        replace(unknown, **changes)

    assert caught.value.entity_type == "attempt"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_run_appends_adjudication_to_latest_unknown_without_retrying() -> None:
    from qarunner.domain import AttemptState

    committed = _committed_run()
    unknown_run = committed.run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=_observation(),
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )
    adjudication = _adjudication()

    adjudicated_run = unknown_run.append_current_unknown_adjudication(
        attempt_id=committed.attempt.id,
        adjudication=adjudication,
        expected_version=unknown_run.version,
        expected_attempt_version=unknown_run.attempts[-1].version,
    )

    assert adjudicated_run.attempts[-1].state is AttemptState.ATTEMPT_UNKNOWN
    assert adjudicated_run.attempts[-1].adjudications == (adjudication,)
    assert len(adjudicated_run.attempts) == 1
    assert adjudicated_run.current_fence == unknown_run.current_fence == 1
    assert adjudicated_run.assignment == unknown_run.assignment
    assert unknown_run.attempts[-1].adjudications == ()


def test_run_adjudication_exact_replay_does_not_advance_versions() -> None:
    committed = _committed_run()
    unknown_run = committed.run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=_observation(),
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )
    adjudication = _adjudication()
    first = unknown_run.append_current_unknown_adjudication(
        attempt_id=committed.attempt.id,
        adjudication=adjudication,
        expected_version=unknown_run.version,
        expected_attempt_version=unknown_run.attempts[-1].version,
    )

    replay = first.append_current_unknown_adjudication(
        attempt_id=committed.attempt.id,
        adjudication=adjudication,
        expected_version=unknown_run.version,
        expected_attempt_version=unknown_run.attempts[-1].version,
    )

    assert replay is first


def test_run_rejects_adjudication_for_latest_attempt_on_a_stale_fence() -> None:
    from qarunner.domain import StaleFence

    committed = _committed_run()
    unknown_run = committed.run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=_observation(),
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )
    superseded = replace(unknown_run, current_fence=2)

    with pytest.raises(StaleFence):
        superseded.append_current_unknown_adjudication(
            attempt_id=committed.attempt.id,
            adjudication=_adjudication(),
            expected_version=superseded.version,
            expected_attempt_version=superseded.attempts[-1].version,
        )

    assert superseded.attempts[-1].adjudications == ()


def test_run_rejects_adjudication_for_an_attempt_owned_by_another_run() -> None:
    from qarunner.domain import AttemptUnknownReviewRequired

    committed = _committed_run()
    unknown_run = committed.run.mark_current_attempt_unknown(
        attempt_id=committed.attempt.id,
        observation=_observation(),
        expected_version=committed.run.version,
        expected_attempt_version=committed.attempt.version,
    )
    wrong_owner = replace(unknown_run, id="run-999")

    with pytest.raises(AttemptUnknownReviewRequired) as caught:
        wrong_owner.append_current_unknown_adjudication(
            attempt_id=committed.attempt.id,
            adjudication=_adjudication(),
            expected_version=wrong_owner.version,
            expected_attempt_version=wrong_owner.attempts[-1].version,
        )

    assert caught.value.reason == "source_attempt_not_current"


@pytest.mark.parametrize("run_kind", ["empty", "wrong-attempt"])
def test_run_rejects_adjudication_for_a_noncurrent_attempt(run_kind: str) -> None:
    from qarunner.domain import AttemptUnknownReviewRequired, Run

    committed = _committed_run()
    run = Run.create(run_id="empty-run") if run_kind == "empty" else committed.run

    with pytest.raises(AttemptUnknownReviewRequired) as caught:
        run.append_current_unknown_adjudication(
            attempt_id="missing-attempt",
            adjudication=_adjudication(),
            expected_version=run.version,
            expected_attempt_version=0,
        )

    assert caught.value.reason == "source_attempt_not_current"
