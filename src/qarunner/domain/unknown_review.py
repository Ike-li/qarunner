"""Fail-closed review-start policy for an existing unknown observation."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.unknown import UnknownObservation

_REVIEW_START_SLA = timedelta(hours=24)


class UnknownReviewRole(enum.StrEnum):
    SUITE_OWNER = "suite_owner"
    PLATFORM_ON_CALL = "platform_on_call"
    REVIEWER = "reviewer"


class UnknownReviewStatus(enum.StrEnum):
    PENDING = "pending"
    STARTED = "started"
    SLA_BREACHED = "sla_breached"


class UnknownReviewNotificationKind(enum.StrEnum):
    SUITE_OWNER = "suite_owner"
    PLATFORM_ON_CALL = "platform_on_call"


class UnknownReviewAuditKind(enum.StrEnum):
    SECURITY = "security"
    OPERATIONS = "operations"


@dataclass(frozen=True, slots=True)
class UnknownReviewActor:
    actor_id: str
    role: UnknownReviewRole

    def __post_init__(self) -> None:
        _require_string("unknown_review_actor", "actor_id", self.actor_id)
        if not isinstance(self.role, UnknownReviewRole):
            _invalid("unknown_review_actor", "role", "unknown")


@dataclass(frozen=True, slots=True)
class UnknownReviewRequest:
    run_id: str
    attempt_id: str
    observation_digest: Digest
    triggering_actor_id: str
    suite_owner_actor_id: str
    platform_on_call_actor_id: str
    requested_at: datetime
    review_start_deadline: datetime

    def __post_init__(self) -> None:
        _require_string("unknown_review_request", "run_id", self.run_id)
        _require_string("unknown_review_request", "attempt_id", self.attempt_id)
        _require_digest("unknown_review_request", "observation_digest", self.observation_digest)
        for field in (
            "triggering_actor_id",
            "suite_owner_actor_id",
            "platform_on_call_actor_id",
        ):
            _require_string("unknown_review_request", field, getattr(self, field))
        if self.suite_owner_actor_id == self.platform_on_call_actor_id:
            _invalid(
                "unknown_review_request",
                "escalation_recipients",
                "recipients_not_distinct",
            )
        _require_utc("unknown_review_request", "requested_at", self.requested_at)
        _require_utc("unknown_review_request", "review_start_deadline", self.review_start_deadline)
        if self.review_start_deadline != self.requested_at + _REVIEW_START_SLA:
            _invalid("unknown_review_request", "review_start_deadline", "not_exact_24h")

    @property
    def schema_version(self) -> str:
        return "qep.unknown-review-request.v1"

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version=self.schema_version,
            payload=self.canonical_payload(),
        )

    def canonical_payload(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "observation_digest": self.observation_digest.value,
            "triggering_actor_id": self.triggering_actor_id,
            "suite_owner_actor_id": self.suite_owner_actor_id,
            "platform_on_call_actor_id": self.platform_on_call_actor_id,
            "requested_at": _timestamp(self.requested_at),
            "review_start_deadline": _timestamp(self.review_start_deadline),
        }


@dataclass(frozen=True, slots=True)
class UnknownReviewNotification:
    kind: UnknownReviewNotificationKind
    recipient_actor_id: str
    request_digest: Digest
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.kind, UnknownReviewNotificationKind):
            _invalid("unknown_review_notification", "kind", "unknown")
        _require_string(
            "unknown_review_notification", "recipient_actor_id", self.recipient_actor_id
        )
        _require_digest("unknown_review_notification", "request_digest", self.request_digest)
        _require_utc("unknown_review_notification", "occurred_at", self.occurred_at)

    def canonical_payload(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "recipient_actor_id": self.recipient_actor_id,
            "request_digest": self.request_digest.value,
            "occurred_at": _timestamp(self.occurred_at),
        }


@dataclass(frozen=True, slots=True)
class UnknownReviewAuditFact:
    kind: UnknownReviewAuditKind
    request_digest: Digest
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.kind, UnknownReviewAuditKind):
            _invalid("unknown_review_audit_fact", "kind", "unknown")
        _require_digest("unknown_review_audit_fact", "request_digest", self.request_digest)
        _require_utc("unknown_review_audit_fact", "occurred_at", self.occurred_at)

    def canonical_payload(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "request_digest": self.request_digest.value,
            "occurred_at": _timestamp(self.occurred_at),
        }


@dataclass(frozen=True, slots=True)
class UnknownReviewEvaluation:
    request: UnknownReviewRequest
    status: UnknownReviewStatus
    reviewer: UnknownReviewActor | None
    notifications: tuple[UnknownReviewNotification, ...]
    audit_facts: tuple[UnknownReviewAuditFact, ...]
    evaluated_at: datetime
    review_started_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.request, UnknownReviewRequest):
            _invalid("unknown_review_evaluation", "request", "not_request")
        if not isinstance(self.status, UnknownReviewStatus):
            _invalid("unknown_review_evaluation", "status", "unknown")
        _require_utc("unknown_review_evaluation", "evaluated_at", self.evaluated_at)
        if not isinstance(self.notifications, tuple) or not all(
            isinstance(item, UnknownReviewNotification) for item in self.notifications
        ):
            _invalid("unknown_review_evaluation", "notifications", "invalid_tuple")
        if not isinstance(self.audit_facts, tuple) or not all(
            isinstance(item, UnknownReviewAuditFact) for item in self.audit_facts
        ):
            _invalid("unknown_review_evaluation", "audit_facts", "invalid_tuple")
        _validate_reviewer_start(
            self.request, self.reviewer, self.review_started_at, self.evaluated_at
        )
        if self.reviewer is not None and self.reviewer.actor_id in {
            self.request.triggering_actor_id,
            self.request.suite_owner_actor_id,
        }:
            _invalid("unknown_review_evaluation", "reviewer", "not_independent")
        if self.status is UnknownReviewStatus.PENDING:
            if self.reviewer is not None or self.notifications or self.audit_facts:
                _invalid("unknown_review_evaluation", "status", "invalid_pending_matrix")
        elif self.status is UnknownReviewStatus.STARTED:
            if self.reviewer is None or self.notifications or self.audit_facts:
                _invalid("unknown_review_evaluation", "status", "invalid_started_matrix")
            if self.review_started_at > self.request.review_start_deadline:
                _invalid("unknown_review_evaluation", "status", "invalid_started_matrix")
        else:
            expected_notifications = (
                UnknownReviewNotificationKind.SUITE_OWNER,
                UnknownReviewNotificationKind.PLATFORM_ON_CALL,
            )
            expected_audits = (UnknownReviewAuditKind.SECURITY, UnknownReviewAuditKind.OPERATIONS)
            if (
                tuple(item.kind for item in self.notifications) != expected_notifications
                or tuple(item.recipient_actor_id for item in self.notifications)
                != (
                    self.request.suite_owner_actor_id,
                    self.request.platform_on_call_actor_id,
                )
                or tuple(item.kind for item in self.audit_facts) != expected_audits
                or any(item.request_digest != self.request.digest for item in self.notifications)
                or any(item.request_digest != self.request.digest for item in self.audit_facts)
                or any(item.occurred_at != self.evaluated_at for item in self.notifications)
                or any(item.occurred_at != self.evaluated_at for item in self.audit_facts)
                or (
                    self.review_started_at is not None
                    and self.review_started_at <= self.request.review_start_deadline
                )
            ):
                _invalid("unknown_review_evaluation", "status", "invalid_breached_matrix")

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.unknown-review-evaluation.v1",
            payload={
                "request_digest": self.request.digest.value,
                "status": self.status.value,
                "reviewer": (
                    {"actor_id": self.reviewer.actor_id, "role": self.reviewer.role.value}
                    if self.reviewer is not None
                    else None
                ),
                "notifications": [item.canonical_payload() for item in self.notifications],
                "audit_facts": [item.canonical_payload() for item in self.audit_facts],
                "evaluated_at": _timestamp(self.evaluated_at),
                "review_started_at": (
                    _timestamp(self.review_started_at)
                    if self.review_started_at is not None
                    else None
                ),
            },
        )


def evaluate_unknown_review(
    *,
    run_id: str,
    attempt_id: str,
    observation: UnknownObservation,
    triggering_actor_id: str,
    suite_owner: UnknownReviewActor,
    platform_on_call: UnknownReviewActor,
    assigned_reviewer: UnknownReviewActor | None,
    review_started_at: datetime | None,
    evaluated_at: datetime,
) -> UnknownReviewEvaluation:
    """Derive request/start/escalation facts without changing unknown handling."""
    _require_string("unknown_review_evaluation", "run_id", run_id)
    _require_string("unknown_review_evaluation", "attempt_id", attempt_id)
    if not isinstance(observation, UnknownObservation):
        _invalid("unknown_review_evaluation", "observation", "not_unknown_observation")
    _require_string("unknown_review_evaluation", "triggering_actor_id", triggering_actor_id)
    _require_actor("suite_owner", suite_owner, UnknownReviewRole.SUITE_OWNER)
    _require_actor("platform_on_call", platform_on_call, UnknownReviewRole.PLATFORM_ON_CALL)
    if suite_owner.actor_id == platform_on_call.actor_id:
        _invalid("unknown_review_evaluation", "escalation_recipients", "recipients_not_distinct")
    _require_utc("unknown_review_evaluation", "evaluated_at", evaluated_at)
    if evaluated_at < observation.recorded_at:
        _invalid("unknown_review_evaluation", "evaluated_at", "before_request")

    if assigned_reviewer is not None:
        _require_actor("assigned_reviewer", assigned_reviewer, UnknownReviewRole.REVIEWER)
        if assigned_reviewer.actor_id in {triggering_actor_id, suite_owner.actor_id}:
            _invalid("unknown_review_evaluation", "assigned_reviewer", "not_independent")

    request = UnknownReviewRequest(
        run_id=run_id,
        attempt_id=attempt_id,
        observation_digest=observation.digest,
        triggering_actor_id=triggering_actor_id,
        suite_owner_actor_id=suite_owner.actor_id,
        platform_on_call_actor_id=platform_on_call.actor_id,
        requested_at=observation.recorded_at,
        review_start_deadline=observation.recorded_at + _REVIEW_START_SLA,
    )
    _validate_reviewer_start(request, assigned_reviewer, review_started_at, evaluated_at)
    breached = (
        review_started_at > request.review_start_deadline
        if review_started_at is not None
        else evaluated_at > request.review_start_deadline
    )
    if breached:
        notifications = (
            UnknownReviewNotification(
                UnknownReviewNotificationKind.SUITE_OWNER,
                suite_owner.actor_id,
                request.digest,
                evaluated_at,
            ),
            UnknownReviewNotification(
                UnknownReviewNotificationKind.PLATFORM_ON_CALL,
                platform_on_call.actor_id,
                request.digest,
                evaluated_at,
            ),
        )
        audits = tuple(
            UnknownReviewAuditFact(kind, request.digest, evaluated_at)
            for kind in (UnknownReviewAuditKind.SECURITY, UnknownReviewAuditKind.OPERATIONS)
        )
        status = UnknownReviewStatus.SLA_BREACHED
    else:
        notifications = ()
        audits = ()
        status = (
            UnknownReviewStatus.STARTED
            if assigned_reviewer is not None
            else UnknownReviewStatus.PENDING
        )
    return UnknownReviewEvaluation(
        request,
        status,
        assigned_reviewer,
        notifications,
        audits,
        evaluated_at,
        review_started_at,
    )


def _validate_reviewer_start(
    request: UnknownReviewRequest,
    reviewer: UnknownReviewActor | None,
    started_at: datetime | None,
    evaluated_at: datetime,
) -> None:
    if reviewer is None:
        if started_at is not None:
            _invalid(
                "unknown_review_evaluation", "review_started_at", "forbidden_without_reviewer"
            )
        return
    if (
        not isinstance(reviewer, UnknownReviewActor)
        or reviewer.role is not UnknownReviewRole.REVIEWER
    ):
        _invalid("unknown_review_evaluation", "reviewer", "invalid_reviewer")
    if started_at is None:
        _invalid("unknown_review_evaluation", "review_started_at", "required_with_reviewer")
    _require_utc("unknown_review_evaluation", "review_started_at", started_at)
    if started_at < request.requested_at:
        _invalid("unknown_review_evaluation", "review_started_at", "before_request")
    if started_at > evaluated_at:
        _invalid("unknown_review_evaluation", "review_started_at", "after_evaluation")


def _require_actor(field: str, value: object, role: UnknownReviewRole) -> None:
    if not isinstance(value, UnknownReviewActor):
        _invalid("unknown_review_evaluation", field, "not_actor")
    if value.role is not role:
        _invalid("unknown_review_evaluation", field, "role_mismatch")


def _require_string(entity: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity, field, "not_string")
    if not value.strip():
        _invalid(entity, field, "empty")


def _require_digest(entity: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity, field, "not_digest")


def _require_utc(entity: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid(entity, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid(entity, field, "not_utc")


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _invalid(entity: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity, field=field, reason=reason)
