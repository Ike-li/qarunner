"""Unknown review requests are fail-closed and never imply retry or finalization."""

from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    Digest,
    DomainValidationError,
    UnknownObservation,
    UnknownReason,
    UnknownReviewActor,
    UnknownReviewAuditKind,
    UnknownReviewNotificationKind,
    UnknownReviewRole,
    UnknownReviewStatus,
    UnknownSource,
    evaluate_unknown_review,
)

UTC = UTC
RECORDED = datetime(2026, 7, 15, 12, tzinfo=UTC)


def digest(label: str) -> Digest:
    from qarunner.domain import canonical_digest

    return canonical_digest(schema_version="test.v1", payload={"label": label})


def observation() -> UnknownObservation:
    return UnknownObservation(
        id="unknown-1",
        reason=UnknownReason.EXECUTION_STOP_UNPROVEN,
        source=UnknownSource.RECONCILER,
        review_basis_digest=digest("basis"),
        recorded_at=RECORDED,
    )


def owner() -> UnknownReviewActor:
    return UnknownReviewActor("suite-owner-1", UnknownReviewRole.SUITE_OWNER)


def on_call() -> UnknownReviewActor:
    return UnknownReviewActor("platform-oncall-1", UnknownReviewRole.PLATFORM_ON_CALL)


def reviewer() -> UnknownReviewActor:
    return UnknownReviewActor("reviewer-1", UnknownReviewRole.REVIEWER)


def evaluate(*, now=RECORDED, assigned_reviewer=None):
    return evaluate_unknown_review(
        run_id="run-1",
        attempt_id="attempt-1",
        observation=observation(),
        triggering_actor_id="coordinator-1",
        suite_owner=owner(),
        platform_on_call=on_call(),
        assigned_reviewer=assigned_reviewer,
        review_started_at=now if assigned_reviewer is not None else None,
        evaluated_at=now,
    )


def test_attempt_unknown_creates_versioned_review_request_with_exact_24h_sla() -> None:
    result = evaluate()

    assert result.status is UnknownReviewStatus.PENDING
    assert result.request.observation_digest == observation().digest
    assert result.request.triggering_actor_id == "coordinator-1"
    assert result.request.suite_owner_actor_id == "suite-owner-1"
    assert result.request.platform_on_call_actor_id == "platform-oncall-1"
    assert result.request.review_start_deadline == RECORDED + timedelta(hours=24)
    assert result.request.schema_version == "qep.unknown-review-request.v1"
    assert result.request.digest == evaluate().request.digest
    assert result.notifications == ()
    assert result.audit_facts == ()
    assert not hasattr(result, "retry_intent")
    assert not hasattr(result, "outcome")
    assert not hasattr(result, "disposition")


def test_independent_reviewer_starts_review_at_the_deadline_without_escalation() -> None:
    result = evaluate(now=RECORDED + timedelta(hours=24), assigned_reviewer=reviewer())

    assert result.status is UnknownReviewStatus.STARTED
    assert result.reviewer == reviewer()
    assert result.notifications == ()
    assert result.audit_facts == ()


@pytest.mark.parametrize(
    ("started_at", "expected"),
    [
        (RECORDED + timedelta(hours=23), UnknownReviewStatus.STARTED),
        (RECORDED + timedelta(hours=24, microseconds=1), UnknownReviewStatus.SLA_BREACHED),
    ],
)
def test_explicit_start_time_controls_sla_even_when_evaluated_later(started_at, expected) -> None:
    result = evaluate_unknown_review(
        run_id="run-1",
        attempt_id="attempt-1",
        observation=observation(),
        triggering_actor_id="coordinator-1",
        suite_owner=owner(),
        platform_on_call=on_call(),
        assigned_reviewer=reviewer(),
        review_started_at=started_at,
        evaluated_at=RECORDED + timedelta(hours=25),
    )
    assert result.status is expected


@pytest.mark.parametrize("actor_id", ["coordinator-1", "suite-owner-1"])
def test_reviewer_must_be_independent_of_trigger_and_owner(actor_id: str) -> None:
    with pytest.raises(DomainValidationError) as caught:
        evaluate(assigned_reviewer=UnknownReviewActor(actor_id, UnknownReviewRole.REVIEWER))

    assert caught.value.entity_type == "unknown_review_evaluation"
    assert caught.value.field == "assigned_reviewer"
    assert caught.value.reason == "not_independent"


def test_only_expiry_after_24h_notifies_owner_and_oncall_and_records_sec_ops() -> None:
    result = evaluate(now=RECORDED + timedelta(hours=24, microseconds=1))

    assert result.status is UnknownReviewStatus.SLA_BREACHED
    assert [item.kind for item in result.notifications] == [
        UnknownReviewNotificationKind.SUITE_OWNER,
        UnknownReviewNotificationKind.PLATFORM_ON_CALL,
    ]
    assert [item.recipient_actor_id for item in result.notifications] == [
        "suite-owner-1",
        "platform-oncall-1",
    ]
    assert [item.kind for item in result.audit_facts] == [
        UnknownReviewAuditKind.SECURITY,
        UnknownReviewAuditKind.OPERATIONS,
    ]
    assert all(item.request_digest == result.request.digest for item in result.notifications)
    assert all(item.request_digest == result.request.digest for item in result.audit_facts)
    assert result.digest == evaluate(now=RECORDED + timedelta(hours=24, microseconds=1)).digest


@pytest.mark.parametrize(
    ("change", "field", "reason"),
    [
        ({"run_id": ""}, "run_id", "empty"),
        ({"attempt_id": 1}, "attempt_id", "not_string"),
        ({"observation": "bad"}, "observation", "not_unknown_observation"),
        ({"triggering_actor_id": " "}, "triggering_actor_id", "empty"),
        ({"suite_owner": None}, "suite_owner", "not_actor"),
        ({"platform_on_call": None}, "platform_on_call", "not_actor"),
        ({"evaluated_at": RECORDED.replace(tzinfo=None)}, "evaluated_at", "not_utc"),
        ({"evaluated_at": "bad"}, "evaluated_at", "not_datetime"),
        ({"evaluated_at": RECORDED - timedelta(microseconds=1)}, "evaluated_at", "before_request"),
    ],
)
def test_missing_or_invalid_evaluation_input_fails_closed(change, field, reason) -> None:
    values = {
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "observation": observation(),
        "triggering_actor_id": "coordinator-1",
        "suite_owner": owner(),
        "platform_on_call": on_call(),
        "assigned_reviewer": None,
        "review_started_at": None,
        "evaluated_at": RECORDED,
    }
    values.update(change)
    with pytest.raises(DomainValidationError) as caught:
        evaluate_unknown_review(**values)
    assert (caught.value.field, caught.value.reason) == (field, reason)


@pytest.mark.parametrize(
    ("value", "field", "reason"),
    [
        (lambda: UnknownReviewActor("", UnknownReviewRole.REVIEWER), "actor_id", "empty"),
        (lambda: UnknownReviewActor("a", "reviewer"), "role", "unknown"),
    ],
)
def test_actor_values_are_strongly_typed(value, field, reason) -> None:
    with pytest.raises(DomainValidationError) as caught:
        value()
    assert (caught.value.field, caught.value.reason) == (field, reason)


def test_role_mismatches_and_colliding_escalation_recipients_fail_closed() -> None:
    with pytest.raises(DomainValidationError, match="role_mismatch"):
        evaluate_unknown_review(
            run_id="run-1",
            attempt_id="attempt-1",
            observation=observation(),
            triggering_actor_id="trigger",
            suite_owner=reviewer(),
            platform_on_call=on_call(),
            assigned_reviewer=None,
            review_started_at=None,
            evaluated_at=RECORDED,
        )


@pytest.mark.parametrize(
    ("reviewer_value", "started_at", "field", "reason"),
    [
        (reviewer(), None, "review_started_at", "required_with_reviewer"),
        (None, RECORDED, "review_started_at", "forbidden_without_reviewer"),
        (reviewer(), "bad", "review_started_at", "not_datetime"),
        (reviewer(), RECORDED.replace(tzinfo=None), "review_started_at", "not_utc"),
        (reviewer(), RECORDED - timedelta(microseconds=1), "review_started_at", "before_request"),
        (
            reviewer(),
            RECORDED + timedelta(microseconds=1),
            "review_started_at",
            "after_evaluation",
        ),
    ],
)
def test_review_start_identity_and_time_are_explicit_and_fail_closed(
    reviewer_value, started_at, field, reason
) -> None:
    with pytest.raises(DomainValidationError) as caught:
        evaluate_unknown_review(
            run_id="run-1",
            attempt_id="attempt-1",
            observation=observation(),
            triggering_actor_id="trigger",
            suite_owner=owner(),
            platform_on_call=on_call(),
            assigned_reviewer=reviewer_value,
            review_started_at=started_at,
            evaluated_at=RECORDED,
        )
    assert (caught.value.field, caught.value.reason) == (field, reason)


def test_exported_values_reject_invalid_direct_construction_and_state_matrix() -> None:
    from dataclasses import replace

    from qarunner.domain import UnknownReviewEvaluation

    request = evaluate().request
    with pytest.raises(DomainValidationError):
        replace(request, observation_digest="bad")
    with pytest.raises(DomainValidationError):
        replace(
            request, review_start_deadline=request.review_start_deadline + timedelta(seconds=1)
        )
    assert replace(request, suite_owner_actor_id="suite-owner-2").digest != request.digest
    with pytest.raises(DomainValidationError, match="recipients_not_distinct"):
        replace(request, platform_on_call_actor_id=request.suite_owner_actor_id)

    breached = evaluate(now=RECORDED + timedelta(hours=25))
    with pytest.raises(DomainValidationError):
        replace(breached.notifications[0], kind="suite_owner")
    with pytest.raises(DomainValidationError):
        replace(breached.audit_facts[0], request_digest="bad")
    with pytest.raises(DomainValidationError):
        replace(breached.audit_facts[0], kind="security")
    pending = evaluate()
    with pytest.raises(DomainValidationError):
        replace(pending, reviewer=reviewer())
    with pytest.raises(DomainValidationError, match="invalid_pending_matrix"):
        replace(pending, notifications=(breached.notifications[0],))
    started = evaluate(assigned_reviewer=reviewer())
    with pytest.raises(DomainValidationError, match="invalid_started_matrix"):
        replace(started, notifications=(breached.notifications[0],))
    with pytest.raises(DomainValidationError, match="invalid_breached_matrix"):
        replace(breached, audit_facts=breached.audit_facts[:1])
    with pytest.raises(DomainValidationError):
        UnknownReviewEvaluation(request, "pending", None, (), (), RECORDED, None)
    with pytest.raises(DomainValidationError):
        replace(pending, request="bad")
    with pytest.raises(DomainValidationError):
        replace(pending, notifications=[])
    with pytest.raises(DomainValidationError):
        replace(pending, audit_facts=[])
    with pytest.raises(DomainValidationError):
        replace(started, reviewer=owner())
    with pytest.raises(DomainValidationError, match="not_independent"):
        replace(
            started,
            reviewer=UnknownReviewActor(request.triggering_actor_id, UnknownReviewRole.REVIEWER),
        )
    with pytest.raises(DomainValidationError, match="invalid_started_matrix"):
        replace(
            started,
            review_started_at=RECORDED + timedelta(hours=25),
            evaluated_at=RECORDED + timedelta(hours=25),
        )
    wrong_recipient = replace(breached.notifications[0], recipient_actor_id="unrelated-actor")
    with pytest.raises(DomainValidationError, match="invalid_breached_matrix"):
        replace(
            breached,
            notifications=(wrong_recipient, breached.notifications[1]),
        )
    with pytest.raises(DomainValidationError, match="recipients_not_distinct"):
        evaluate_unknown_review(
            run_id="run-1",
            attempt_id="attempt-1",
            observation=observation(),
            triggering_actor_id="trigger",
            suite_owner=owner(),
            platform_on_call=UnknownReviewActor(
                "suite-owner-1", UnknownReviewRole.PLATFORM_ON_CALL
            ),
            assigned_reviewer=None,
            review_started_at=None,
            evaluated_at=RECORDED,
        )
