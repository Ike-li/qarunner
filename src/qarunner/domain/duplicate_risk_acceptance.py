"""Single-use duplicate-risk acceptance for an unknown retry intent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError, IdempotencyConflict


@dataclass(frozen=True, slots=True)
class DuplicateRiskAcceptanceBasis:
    """Pre-intent two-person authority and complete retry environment binding."""

    id: str
    run_id: str
    source_attempt_id: str
    source_attempt_no: int
    source_fence: int
    run_item_set_digest: Digest
    sut_identity: str
    sut_digest: Digest
    target_grant_identity: str
    target_grant_version: int
    target_grant_digest: Digest
    suite_owner_id: str
    reviewer_id: str
    original_executor_id: str
    original_trigger_actor_id: str
    accepted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "id",
            "run_id",
            "source_attempt_id",
            "sut_identity",
            "target_grant_identity",
            "suite_owner_id",
            "reviewer_id",
            "original_executor_id",
            "original_trigger_actor_id",
        ):
            _require_string("duplicate_risk_acceptance", field, getattr(self, field))
        for field in ("source_attempt_no", "source_fence", "target_grant_version"):
            _require_positive_int("duplicate_risk_acceptance", field, getattr(self, field))
        for field in (
            "run_item_set_digest",
            "sut_digest",
            "target_grant_digest",
        ):
            _require_digest("duplicate_risk_acceptance", field, getattr(self, field))
        _require_utc("duplicate_risk_acceptance", "accepted_at", self.accepted_at)
        _require_utc("duplicate_risk_acceptance", "expires_at", self.expires_at)
        if self.expires_at - self.accepted_at != timedelta(hours=1):
            _invalid("duplicate_risk_acceptance", "expires_at", "ttl_not_one_hour")
        if self.reviewer_id in {
            self.suite_owner_id,
            self.original_executor_id,
            self.original_trigger_actor_id,
        }:
            _invalid("duplicate_risk_acceptance", "reviewer_id", "not_independent")

    @property
    def ttl(self) -> timedelta:
        return self.expires_at - self.accepted_at

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.duplicate-risk-acceptance-basis.v1",
            payload={
                "id": self.id,
                "run_id": self.run_id,
                "source_attempt_id": self.source_attempt_id,
                "source_attempt_no": self.source_attempt_no,
                "source_fence": self.source_fence,
                "run_item_set_digest": self.run_item_set_digest.value,
                "sut_identity": self.sut_identity,
                "sut_digest": self.sut_digest.value,
                "target_grant_identity": self.target_grant_identity,
                "target_grant_version": self.target_grant_version,
                "target_grant_digest": self.target_grant_digest.value,
                "suite_owner_id": self.suite_owner_id,
                "reviewer_id": self.reviewer_id,
                "original_executor_id": self.original_executor_id,
                "original_trigger_actor_id": self.original_trigger_actor_id,
                "accepted_at": _timestamp(self.accepted_at),
                "expires_at": _timestamp(self.expires_at),
            },
        )


@dataclass(frozen=True, slots=True)
class DuplicateRiskAcceptance:
    """Consumable acceptance binding a pre-intent basis to the final intent."""

    basis: DuplicateRiskAcceptanceBasis
    retry_intent_digest: Digest

    def __post_init__(self) -> None:
        if not isinstance(self.basis, DuplicateRiskAcceptanceBasis):
            _invalid("duplicate_risk_acceptance", "basis", "wrong_type")
        _require_digest(
            "duplicate_risk_acceptance", "retry_intent_digest", self.retry_intent_digest
        )

    @property
    def ttl(self) -> timedelta:
        return self.basis.ttl

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.duplicate-risk-acceptance.v2",
            payload={
                "basis_digest": self.basis.digest.value,
                "retry_intent_digest": self.retry_intent_digest.value,
            },
        )


@dataclass(frozen=True, slots=True)
class DuplicateRiskAcceptanceRequest:
    scope: str
    key: str
    acceptance: DuplicateRiskAcceptance
    requested_at: datetime

    def __post_init__(self) -> None:
        _require_string("duplicate_risk_acceptance_request", "scope", self.scope)
        _require_string("duplicate_risk_acceptance_request", "key", self.key)
        if not isinstance(self.acceptance, DuplicateRiskAcceptance):
            _invalid("duplicate_risk_acceptance_request", "acceptance", "wrong_type")
        _require_utc("duplicate_risk_acceptance_request", "requested_at", self.requested_at)

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.duplicate-risk-acceptance-request.v1",
            payload={
                "scope": self.scope,
                "key": self.key,
                "acceptance_digest": self.acceptance.digest.value,
                "requested_at": _timestamp(self.requested_at),
            },
        )


@dataclass(frozen=True, slots=True)
class DuplicateRiskAcceptanceConsumption:
    scope: str
    key: str
    acceptance_digest: Digest
    request_digest: Digest
    consumed_at: datetime

    def __post_init__(self) -> None:
        _require_string("duplicate_risk_acceptance_consumption", "scope", self.scope)
        _require_string("duplicate_risk_acceptance_consumption", "key", self.key)
        _require_digest(
            "duplicate_risk_acceptance_consumption", "acceptance_digest", self.acceptance_digest
        )
        _require_digest(
            "duplicate_risk_acceptance_consumption", "request_digest", self.request_digest
        )
        _require_utc("duplicate_risk_acceptance_consumption", "consumed_at", self.consumed_at)

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.duplicate-risk-acceptance-consumption.v1",
            payload={
                "scope": self.scope,
                "key": self.key,
                "acceptance_digest": self.acceptance_digest.value,
                "request_digest": self.request_digest.value,
                "consumed_at": _timestamp(self.consumed_at),
            },
        )


@dataclass(frozen=True, slots=True)
class DuplicateRiskReplay:
    """Exact historical result; it is not a new consumption fact."""

    consumption: DuplicateRiskAcceptanceConsumption

    def __post_init__(self) -> None:
        if not isinstance(self.consumption, DuplicateRiskAcceptanceConsumption):
            _invalid("duplicate_risk_replay", "consumption", "wrong_type")

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.duplicate-risk-replay.v1",
            payload={"historical_consumption_digest": self.consumption.digest.value},
        )


def consume_duplicate_risk_acceptance(
    *,
    request: DuplicateRiskAcceptanceRequest,
    prior: DuplicateRiskAcceptanceConsumption | None,
) -> DuplicateRiskAcceptanceConsumption | DuplicateRiskReplay:
    """Consume once, or resolve an exact replay without consuming again."""
    if not isinstance(request, DuplicateRiskAcceptanceRequest):
        _invalid("duplicate_risk_acceptance_consumption", "request", "wrong_type")
    if prior is not None and not isinstance(prior, DuplicateRiskAcceptanceConsumption):
        _invalid("duplicate_risk_acceptance_consumption", "prior", "wrong_type")
    if prior is not None:
        if (
            prior.scope == request.scope
            and prior.key == request.key
            and prior.acceptance_digest == request.acceptance.digest
            and prior.request_digest == request.digest
        ):
            return DuplicateRiskReplay(consumption=prior)
        raise IdempotencyConflict(
            scope=request.scope,
            key=request.key,
            stored_digest=prior.request_digest,
            received_digest=request.digest,
        )
    if request.requested_at >= request.acceptance.basis.expires_at:
        _invalid("duplicate_risk_acceptance_consumption", "requested_at", "acceptance_expired")
    if request.requested_at < request.acceptance.basis.accepted_at:
        _invalid("duplicate_risk_acceptance_consumption", "requested_at", "before_acceptance")
    return DuplicateRiskAcceptanceConsumption(
        scope=request.scope,
        key=request.key,
        acceptance_digest=request.acceptance.digest,
        request_digest=request.digest,
        consumed_at=request.requested_at,
    )


def _require_string(entity: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity, field, "not_string")
    if not value.strip():
        _invalid(entity, field, "empty")


def _require_positive_int(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(entity, field, "not_integer")
    if value < 1:
        _invalid(entity, field, "not_positive")


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
