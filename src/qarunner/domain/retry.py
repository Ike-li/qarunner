"""Immutable retry authorization and Attempt provenance facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.run_finalization import AttemptExecutionFact
from qarunner.domain.run_retry_policy import RetryDecisionSourceKind, RetryPolicyFamily
from qarunner.domain.unknown import UnknownAdjudicationDecision


@dataclass(frozen=True, slots=True)
class UnknownAdjudicationRetryAuthority:
    adjudication_id: str
    adjudication_digest: Digest
    decision: UnknownAdjudicationDecision

    def __post_init__(self) -> None:
        _require_nonempty_string(
            "unknown_retry_authority", "adjudication_id", self.adjudication_id
        )
        _require_digest("unknown_retry_authority", "adjudication_digest", self.adjudication_digest)
        if not isinstance(self.decision, UnknownAdjudicationDecision):
            _invalid("unknown_retry_authority", "decision", "unknown")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": "unknown_adjudication",
            "adjudication_id": self.adjudication_id,
            "adjudication_digest": self.adjudication_digest.value,
            "decision": self.decision.value,
        }


@dataclass(frozen=True, slots=True)
class PolicyRetryAuthority:
    family: RetryPolicyFamily
    decision_source_kind: RetryDecisionSourceKind
    policy_digest: Digest
    authority_schema: str
    authority_id: str
    authority_version: int
    authority_digest: Digest
    source_outcome: AttemptExecutionFact
    item_resolution_set_digest: Digest
    source_item_set_digest: Digest
    target_item_set_digest: Digest

    def __post_init__(self) -> None:
        if not isinstance(self.family, RetryPolicyFamily):
            _invalid("policy_retry_authority", "family", "unknown")
        expected = (
            RetryDecisionSourceKind.SUITE_POLICY
            if self.family is RetryPolicyFamily.SUITE
            else RetryDecisionSourceKind.PLATFORM_POLICY
        )
        if self.decision_source_kind is not expected:
            _invalid("policy_retry_authority", "decision_source_kind", "family_mismatch")
        expected_outcome = (
            AttemptExecutionFact.TEST_FAILED
            if self.family is RetryPolicyFamily.SUITE
            else AttemptExecutionFact.INFRA_FAILED
        )
        if self.source_outcome is not expected_outcome:
            _invalid("policy_retry_authority", "source_outcome", "family_mismatch")
        for field in ("authority_schema", "authority_id"):
            _require_nonempty_string("policy_retry_authority", field, getattr(self, field))
        _require_positive_integer(
            "policy_retry_authority", "authority_version", self.authority_version
        )
        for field in (
            "policy_digest",
            "authority_digest",
            "item_resolution_set_digest",
            "source_item_set_digest",
            "target_item_set_digest",
        ):
            _require_digest("policy_retry_authority", field, getattr(self, field))
        if self.source_item_set_digest != self.target_item_set_digest:
            _invalid("policy_retry_authority", "target_item_set_digest", "full_run_scope_mismatch")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": "policy",
            "family": self.family.value,
            "decision_source_kind": self.decision_source_kind.value,
            "policy_digest": self.policy_digest.value,
            "authority_schema": self.authority_schema,
            "authority_id": self.authority_id,
            "authority_version": self.authority_version,
            "authority_digest": self.authority_digest.value,
            "source_outcome": self.source_outcome.value,
            "item_resolution_set_digest": self.item_resolution_set_digest.value,
            "source_item_set_digest": self.source_item_set_digest.value,
            "target_item_set_digest": self.target_item_set_digest.value,
        }


RetryAuthority = UnknownAdjudicationRetryAuthority | PolicyRetryAuthority


@dataclass(frozen=True, slots=True)
class RetryIntent:
    id: str
    run_id: str
    source_attempt_id: str
    source_attempt_no: int
    source_fence: int
    authority: RetryAuthority
    execution_spec_digest: Digest
    created_at: datetime

    def __post_init__(self) -> None:
        for field in ("id", "run_id", "source_attempt_id"):
            _require_nonempty_string("retry_intent", field, getattr(self, field))
        _require_positive_integer("retry_intent", "source_attempt_no", self.source_attempt_no)
        _require_positive_integer("retry_intent", "source_fence", self.source_fence)
        if not isinstance(
            self.authority, (UnknownAdjudicationRetryAuthority, PolicyRetryAuthority)
        ):
            _invalid("retry_intent", "authority", "unknown")
        _require_digest("retry_intent", "execution_spec_digest", self.execution_spec_digest)
        _require_utc("retry_intent", "created_at", self.created_at)

    @classmethod
    def from_unknown_adjudication(
        cls,
        *,
        adjudication_id: str,
        adjudication_digest: Digest,
        decision: UnknownAdjudicationDecision,
        **values: object,
    ) -> RetryIntent:
        return cls(
            authority=UnknownAdjudicationRetryAuthority(
                adjudication_id, adjudication_digest, decision
            ),
            **values,
        )

    @property
    def adjudication_id(self) -> str:
        return (
            self.authority.adjudication_id
            if isinstance(self.authority, UnknownAdjudicationRetryAuthority)
            else ""
        )

    @property
    def adjudication_digest(self) -> Digest | None:
        return (
            self.authority.adjudication_digest
            if isinstance(self.authority, UnknownAdjudicationRetryAuthority)
            else None
        )

    @property
    def decision(self) -> UnknownAdjudicationDecision | None:
        return (
            self.authority.decision
            if isinstance(self.authority, UnknownAdjudicationRetryAuthority)
            else None
        )

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.retry-intent.v2",
            payload={
                "id": self.id,
                "run_id": self.run_id,
                "source_attempt_id": self.source_attempt_id,
                "source_attempt_no": self.source_attempt_no,
                "source_fence": self.source_fence,
                "authority": self.authority.canonical_payload(),
                "execution_spec_digest": self.execution_spec_digest.value,
                "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class RetryProvenance:
    retry_intent_id: str
    retry_intent_digest: Digest
    source_attempt_id: str
    source_attempt_no: int
    source_fence: int
    authority: RetryAuthority

    def __post_init__(self) -> None:
        for field in ("retry_intent_id", "source_attempt_id"):
            _require_nonempty_string("retry_provenance", field, getattr(self, field))
        _require_digest("retry_provenance", "retry_intent_digest", self.retry_intent_digest)
        _require_positive_integer("retry_provenance", "source_attempt_no", self.source_attempt_no)
        _require_positive_integer("retry_provenance", "source_fence", self.source_fence)
        if not isinstance(
            self.authority, (UnknownAdjudicationRetryAuthority, PolicyRetryAuthority)
        ):
            _invalid("retry_provenance", "authority", "unknown")

    @property
    def adjudication_id(self) -> str:
        return (
            self.authority.adjudication_id
            if isinstance(self.authority, UnknownAdjudicationRetryAuthority)
            else ""
        )

    @property
    def adjudication_digest(self) -> Digest | None:
        return (
            self.authority.adjudication_digest
            if isinstance(self.authority, UnknownAdjudicationRetryAuthority)
            else None
        )

    @property
    def decision(self) -> UnknownAdjudicationDecision | None:
        return (
            self.authority.decision
            if isinstance(self.authority, UnknownAdjudicationRetryAuthority)
            else None
        )

    @classmethod
    def from_intent(cls, intent: RetryIntent) -> RetryProvenance:
        return cls(
            intent.id,
            intent.digest,
            intent.source_attempt_id,
            intent.source_attempt_no,
            intent.source_fence,
            intent.authority,
        )

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.retry-provenance.v2",
            payload={
                "retry_intent_id": self.retry_intent_id,
                "retry_intent_digest": self.retry_intent_digest.value,
                "source_attempt_id": self.source_attempt_id,
                "source_attempt_no": self.source_attempt_no,
                "source_fence": self.source_fence,
                "authority": self.authority.canonical_payload(),
            },
        )


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty")


def _require_positive_integer(entity_type: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(entity_type, field, "not_integer")
    if value < 1:
        _invalid(entity_type, field, "not_positive")


def _require_digest(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity_type, field, "not_digest")


def _require_utc(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid(entity_type, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid(entity_type, field, "not_utc")


def _invalid(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity_type, field=field, reason=reason)
