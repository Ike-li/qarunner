"""Versioned, fail-closed Run retry policy evaluation values."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime

from qarunner.domain.digest import Digest, JsonValue, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.run_finalization import AttemptExecutionFact


class RetryPolicyFamily(enum.StrEnum):
    SUITE = "suite"
    PLATFORM = "platform"


class RetryDecisionSourceKind(enum.StrEnum):
    SUITE_POLICY = "suite_policy"
    PLATFORM_POLICY = "platform_policy"


class RetryDecision(enum.StrEnum):
    RETRY = "retry"
    CLOSED_NO_RETRY = "closed_no_retry"


class RetryScope(enum.StrEnum):
    FULL_RUN = "full_run"


@dataclass(frozen=True, slots=True, order=True)
class RetryReasonSelector:
    reason_class: str
    reason_code: str

    def __post_init__(self) -> None:
        _string("retry_reason_selector", "reason_class", self.reason_class)
        _string("retry_reason_selector", "reason_code", self.reason_code)


@dataclass(frozen=True, slots=True)
class RetryBudget:
    max_retries: int
    max_execution_seconds: int
    max_resource_unit_seconds: int

    def __post_init__(self) -> None:
        _nonnegative("retry_budget", "max_retries", self.max_retries)
        _nonnegative("retry_budget", "max_execution_seconds", self.max_execution_seconds)
        _nonnegative("retry_budget", "max_resource_unit_seconds", self.max_resource_unit_seconds)

    def canonical_payload(self) -> dict[str, int]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class RetryBudgetUsage:
    retry_count: int
    execution_seconds: int
    resource_unit_seconds: int

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            _nonnegative("retry_budget_usage", field, getattr(self, field))

    def canonical_payload(self) -> dict[str, int]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class RetryRequestedBudget:
    execution_seconds: int
    resource_unit_seconds: int

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            _positive("retry_requested_budget", field, getattr(self, field))

    def canonical_payload(self) -> dict[str, int]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class SuiteRetryPolicy:
    policy_id: str
    version: int
    project_id: str
    suite_revision_id: str
    retryable_reasons: tuple[RetryReasonSelector, ...]
    required_approver_role: str
    max_retries: int
    retry_scope: RetryScope
    budget: RetryBudget
    effective_from: datetime
    effective_until: datetime
    policy_digest: Digest

    def __post_init__(self) -> None:
        _policy_common(self, max_allowed=1, entity="suite_retry_policy")
        _string("suite_retry_policy", "suite_revision_id", self.suite_revision_id)


@dataclass(frozen=True, slots=True)
class PlatformRetryPolicy:
    policy_id: str
    version: int
    project_id: str
    runner_id: str
    runner_version: int
    retryable_reasons: tuple[RetryReasonSelector, ...]
    required_approver_role: str
    max_retries: int
    retry_scope: RetryScope
    budget: RetryBudget
    require_prior_execution_stop: bool
    effective_from: datetime
    effective_until: datetime
    policy_digest: Digest

    def __post_init__(self) -> None:
        _policy_common(self, max_allowed=2, entity="platform_retry_policy")
        _string("platform_retry_policy", "runner_id", self.runner_id)
        _positive("platform_retry_policy", "runner_version", self.runner_version)
        if self.require_prior_execution_stop is not True:
            _invalid("platform_retry_policy", "require_prior_execution_stop", "must_be_true")


@dataclass(frozen=True, slots=True)
class RetrySourceFact:
    run_id: str
    run_version: int
    attempt_id: str
    attempt_no: int
    attempt_fence: int
    outcome: AttemptExecutionFact
    reason_class: str
    reason_code: str
    item_resolution_set_digest: Digest
    source_item_set_digest: Digest
    target_item_set_digest: Digest
    prior_execution_stop_digest: Digest | None

    def __post_init__(self) -> None:
        entity = "retry_source_fact"
        for field in ("run_id", "attempt_id", "reason_class", "reason_code"):
            _string(entity, field, getattr(self, field))
        _nonnegative(entity, "run_version", self.run_version)
        _positive(entity, "attempt_no", self.attempt_no)
        _positive(entity, "attempt_fence", self.attempt_fence)
        if not isinstance(self.outcome, AttemptExecutionFact):
            _invalid(entity, "outcome", "unknown")
        for field in (
            "item_resolution_set_digest",
            "source_item_set_digest",
            "target_item_set_digest",
        ):
            _digest(entity, field, getattr(self, field))
        if self.source_item_set_digest != self.target_item_set_digest:
            _invalid(entity, "target_item_set_digest", "full_run_scope_mismatch")
        if self.prior_execution_stop_digest is not None:
            _digest(entity, "prior_execution_stop_digest", self.prior_execution_stop_digest)


@dataclass(frozen=True, slots=True)
class RetryPolicyAuthority:
    schema: str
    authority_id: str
    version: int
    family: RetryPolicyFamily
    project_id: str
    suite_revision_id: str | None
    runner_id: str | None
    runner_version: int | None
    approver_role: str
    approval_record_digest: Digest
    valid_from: datetime
    valid_until: datetime
    authority_digest: Digest

    def __post_init__(self) -> None:
        entity = "retry_policy_authority"
        for field in ("schema", "authority_id", "project_id", "approver_role"):
            _string(entity, field, getattr(self, field))
        _positive(entity, "version", self.version)
        if not isinstance(self.family, RetryPolicyFamily):
            _invalid(entity, "family", "unknown")
        _digest(entity, "approval_record_digest", self.approval_record_digest)
        _digest(entity, "authority_digest", self.authority_digest)
        _window(entity, self.valid_from, self.valid_until)
        if self.family is RetryPolicyFamily.SUITE:
            _string(entity, "suite_revision_id", self.suite_revision_id)
            if self.runner_id is not None or self.runner_version is not None:
                _invalid(entity, "runner", "forbidden")
        else:
            _string(entity, "runner_id", self.runner_id)
            _positive(entity, "runner_version", self.runner_version)
            if self.suite_revision_id is not None:
                _invalid(entity, "suite_revision_id", "forbidden")


@dataclass(frozen=True, slots=True)
class RunRetryDecision:
    source: RetrySourceFact
    retry_scope: RetryScope
    decision: RetryDecision
    decision_source_kind: RetryDecisionSourceKind
    reason_class: str
    reason_code: str
    retry_intent_digest: Digest | None
    authority_schema: str | None
    authority_id: str | None
    authority_version: int | None
    authority_digest: Digest | None
    policy_digest: Digest | None
    prior_execution_stop_digest: Digest | None
    budget: RetryBudget
    usage: RetryBudgetUsage
    requested: RetryRequestedBudget
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.decision is RetryDecision.RETRY and self.retry_intent_digest is None:
            _invalid("run_retry_decision", "retry_intent_digest", "required")
        if self.decision is RetryDecision.CLOSED_NO_RETRY and self.retry_intent_digest is not None:
            _invalid("run_retry_decision", "retry_intent_digest", "forbidden")

    def canonical_payload(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.source.run_id,
            "source_run_version": self.source.run_version,
            "source_attempt_id": self.source.attempt_id,
            "source_attempt_no": self.source.attempt_no,
            "source_attempt_fence": self.source.attempt_fence,
            "source_outcome": self.source.outcome.value,
            "source_item_resolution_set_digest": self.source.item_resolution_set_digest.value,
            "source_item_set_digest": self.source.source_item_set_digest.value,
            "target_item_set_digest": self.source.target_item_set_digest.value,
            "retry_scope": self.retry_scope.value,
            "decision": self.decision.value,
            "decision_source_kind": self.decision_source_kind.value,
            "reason_class": self.reason_class,
            "reason_code": self.reason_code,
            "retry_intent_digest": _digest_value(self.retry_intent_digest),
            "authority_schema": self.authority_schema,
            "authority_id": self.authority_id,
            "authority_version": self.authority_version,
            "authority_digest": _digest_value(self.authority_digest),
            "policy_digest": _digest_value(self.policy_digest),
            "adjudication_digest": None,
            "prior_execution_stop_digest": _digest_value(self.prior_execution_stop_digest),
            "duplicate_risk_acceptance_digest": None,
            "attempt_budget_snapshot": {
                "configured": self.budget.canonical_payload(),
                "usage": self.usage.canonical_payload(),
                "requested": self.requested.canonical_payload(),
            },
            "decided_at": _utc_text(self.decided_at),
        }

    @property
    def decision_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-retry-decision.v1", payload=self.canonical_payload()
        )


def evaluate_run_retry(
    *,
    policy: SuiteRetryPolicy | PlatformRetryPolicy | None,
    source: RetrySourceFact,
    authority: RetryPolicyAuthority | None,
    usage: RetryBudgetUsage,
    requested: RetryRequestedBudget,
    retry_intent_digest: Digest,
    decided_at: datetime,
) -> RunRetryDecision:
    """Evaluate exact policy inputs without defaults; every mismatch fails closed."""
    _utc("run_retry_evaluation", "decided_at", decided_at)
    _digest("run_retry_evaluation", "retry_intent_digest", retry_intent_digest)
    family = (
        RetryPolicyFamily.PLATFORM
        if isinstance(policy, PlatformRetryPolicy)
        else RetryPolicyFamily.SUITE
    )
    kind = (
        RetryDecisionSourceKind.PLATFORM_POLICY
        if family is RetryPolicyFamily.PLATFORM
        else RetryDecisionSourceKind.SUITE_POLICY
    )
    allowed = policy is not None and authority is not None
    if allowed:
        expected_outcome = (
            AttemptExecutionFact.INFRA_FAILED
            if family is RetryPolicyFamily.PLATFORM
            else AttemptExecutionFact.TEST_FAILED
        )
        allowed = (
            source.outcome is expected_outcome
            and authority.family is family
            and authority.project_id == policy.project_id
            and authority.approver_role == policy.required_approver_role
            and policy.effective_from <= decided_at < policy.effective_until
            and authority.valid_from <= decided_at < authority.valid_until
            and RetryReasonSelector(source.reason_class, source.reason_code)
            in policy.retryable_reasons
            and usage.retry_count < policy.max_retries
            and usage.execution_seconds + requested.execution_seconds
            <= policy.budget.max_execution_seconds
            and usage.resource_unit_seconds + requested.resource_unit_seconds
            <= policy.budget.max_resource_unit_seconds
        )
        if isinstance(policy, SuiteRetryPolicy):
            allowed = allowed and authority.suite_revision_id == policy.suite_revision_id
        else:
            allowed = (
                allowed
                and authority.runner_id == policy.runner_id
                and authority.runner_version == policy.runner_version
                and source.prior_execution_stop_digest is not None
            )
    fallback_budget = policy.budget if policy is not None else RetryBudget(0, 0, 0)
    return RunRetryDecision(
        source=source,
        retry_scope=RetryScope.FULL_RUN,
        decision=RetryDecision.RETRY if allowed else RetryDecision.CLOSED_NO_RETRY,
        decision_source_kind=kind,
        reason_class=source.reason_class,
        reason_code=source.reason_code,
        retry_intent_digest=retry_intent_digest if allowed else None,
        authority_schema=authority.schema if authority else None,
        authority_id=authority.authority_id if authority else None,
        authority_version=authority.version if authority else None,
        authority_digest=authority.authority_digest if authority else None,
        policy_digest=policy.policy_digest if policy else None,
        prior_execution_stop_digest=source.prior_execution_stop_digest,
        budget=fallback_budget,
        usage=usage,
        requested=requested,
        decided_at=decided_at,
    )


def _policy_common(policy: object, *, max_allowed: int, entity: str) -> None:
    for field in ("policy_id", "project_id"):
        _string(entity, field, getattr(policy, field))
    _positive(entity, "version", policy.version)
    maximum = policy.max_retries
    _nonnegative(entity, "max_retries", maximum)
    if maximum > max_allowed or policy.budget.max_retries != maximum:
        _invalid(entity, "max_retries", "outside_signed_limit")
    if policy.retry_scope is not RetryScope.FULL_RUN:
        _invalid(entity, "retry_scope", "unsupported")
    _string(entity, "required_approver_role", policy.required_approver_role)
    values = policy.retryable_reasons
    if (
        not isinstance(values, tuple)
        or not values
        or any(not isinstance(value, RetryReasonSelector) for value in values)
    ):
        _invalid(entity, "retryable_reasons", "invalid_selector")
    if tuple(sorted(set(values))) != values:
        _invalid(entity, "retryable_reasons", "not_sorted_unique")
    _window(entity, policy.effective_from, policy.effective_until)
    _digest(entity, "policy_digest", policy.policy_digest)


def _window(entity: str, start: datetime, end: datetime) -> None:
    _utc(entity, "effective_from", start)
    _utc(entity, "effective_until", end)
    if start >= end:
        _invalid(entity, "effective_until", "invalid_window")


def _utc(entity: str, field: str, value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _invalid(entity, field, "not_utc")


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _digest_value(value: Digest | None) -> str | None:
    return value.value if value else None


def _digest(entity: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity, field, "invalid")


def _string(entity: str, field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        _invalid(entity, field, "empty")


def _positive(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _invalid(entity, field, "not_positive_integer")


def _nonnegative(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(entity, field, "not_nonnegative_integer")


def _invalid(entity: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity, field=field, reason=reason)
