"""Atomic application port for policy-authorized Run retry publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import (
    Digest,
    DuplicateRiskAcceptanceRequest,
    RetryIntent,
    Run,
    RunRetryDecision,
    UnknownAdjudicationDecision,
    UnknownAdjudicationRetryAuthority,
)
from qarunner.domain.run_retry_policy import (
    RetryBudget,
    RetryBudgetUsage,
    RetryDecision,
    RetryRequestedBudget,
)

type RunRetryIdentityScope = tuple[str, str]


@dataclass(frozen=True, slots=True)
class RunRetryWriteAuthority:
    run_id: str
    writer_digest: Digest
    policy_digest: Digest
    authority_schema: str
    authority_id: str
    authority_version: int
    authority_digest: Digest
    write_epoch: int

    def __post_init__(self) -> None:
        _string("run_retry_write_authority", "run_id", self.run_id)
        _digest("run_retry_write_authority", "writer_digest", self.writer_digest)
        _digest("run_retry_write_authority", "policy_digest", self.policy_digest)
        _string("run_retry_write_authority", "authority_schema", self.authority_schema)
        _string("run_retry_write_authority", "authority_id", self.authority_id)
        _positive("run_retry_write_authority", "authority_version", self.authority_version)
        _digest("run_retry_write_authority", "authority_digest", self.authority_digest)
        _positive("run_retry_write_authority", "write_epoch", self.write_epoch)


@dataclass(frozen=True, slots=True)
class UnknownRetryWriteAuthority:
    run_id: str
    attempt_id: str
    attempt_version: int
    attempt_fence: int
    adjudication_id: str
    adjudication_digest: Digest
    decision: UnknownAdjudicationDecision
    proof_digest: Digest | None
    risk_acceptance_basis_digest: Digest | None
    writer_digest: Digest
    write_epoch: int

    def __post_init__(self) -> None:
        for field in ("run_id", "attempt_id", "adjudication_id"):
            _string("unknown_retry_write_authority", field, getattr(self, field))
        for field in ("adjudication_digest", "writer_digest"):
            _digest("unknown_retry_write_authority", field, getattr(self, field))
        if not isinstance(self.decision, UnknownAdjudicationDecision):
            _invalid("unknown_retry_write_authority", "decision", "invalid")
        _nonnegative("unknown_retry_write_authority", "attempt_version", self.attempt_version)
        _positive("unknown_retry_write_authority", "attempt_fence", self.attempt_fence)
        if self.decision is UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY:
            _digest("unknown_retry_write_authority", "proof_digest", self.proof_digest)
            if self.risk_acceptance_basis_digest is not None:
                _invalid(
                    "unknown_retry_write_authority",
                    "risk_acceptance_basis_digest",
                    "forbidden",
                )
        elif self.decision is UnknownAdjudicationDecision.ACCEPT_DUPLICATE_RISK_THEN_RETRY:
            _digest(
                "unknown_retry_write_authority",
                "risk_acceptance_basis_digest",
                self.risk_acceptance_basis_digest,
            )
            if self.proof_digest is not None:
                _invalid("unknown_retry_write_authority", "proof_digest", "forbidden")
        else:
            _invalid("unknown_retry_write_authority", "decision", "nonretry")
        _positive("unknown_retry_write_authority", "write_epoch", self.write_epoch)


@dataclass(frozen=True, slots=True)
class RunRetryMutationSnapshot:
    run: Run
    attempt_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.run, Run):
            _invalid("run_retry_mutation_snapshot", "run", "invalid")
        _nonnegative("run_retry_mutation_snapshot", "attempt_version", self.attempt_version)


@dataclass(frozen=True, slots=True)
class UnknownRetryMutationBinding:
    run_item_set_digest: Digest
    sut_identity: str
    sut_digest: Digest
    target_grant_identity: str
    target_grant_version: int
    target_grant_digest: Digest

    def __post_init__(self) -> None:
        for field in ("run_item_set_digest", "sut_digest", "target_grant_digest"):
            _digest("unknown_retry_mutation_binding", field, getattr(self, field))
        for field in ("sut_identity", "target_grant_identity"):
            _string("unknown_retry_mutation_binding", field, getattr(self, field))
        _positive(
            "unknown_retry_mutation_binding", "target_grant_version", self.target_grant_version
        )


@dataclass(frozen=True, slots=True)
class UnknownRetryMutationSnapshot:
    run: Run
    attempt_version: int
    binding: UnknownRetryMutationBinding

    def __post_init__(self) -> None:
        if not isinstance(self.run, Run):
            _invalid("unknown_retry_mutation_snapshot", "run", "invalid")
        _nonnegative("unknown_retry_mutation_snapshot", "attempt_version", self.attempt_version)
        if not isinstance(self.binding, UnknownRetryMutationBinding):
            _invalid("unknown_retry_mutation_snapshot", "binding", "invalid")


@dataclass(frozen=True, slots=True)
class RetryBudgetReservation:
    retry_intent_digest: Digest
    decision_digest: Digest
    configured: RetryBudget
    usage: RetryBudgetUsage
    requested: RetryRequestedBudget

    def __post_init__(self) -> None:
        _digest("retry_budget_reservation", "retry_intent_digest", self.retry_intent_digest)
        _digest("retry_budget_reservation", "decision_digest", self.decision_digest)
        for field, kind in (
            ("configured", RetryBudget),
            ("usage", RetryBudgetUsage),
            ("requested", RetryRequestedBudget),
        ):
            if not isinstance(getattr(self, field), kind):
                _invalid("retry_budget_reservation", field, "invalid")

    @classmethod
    def from_decision(cls, decision: RunRetryDecision) -> RetryBudgetReservation:
        if decision.retry_intent_digest is None:
            _invalid("retry_budget_reservation", "decision", "closed_no_retry")
        return cls(
            decision.retry_intent_digest,
            decision.decision_digest,
            decision.budget,
            decision.usage,
            decision.requested,
        )


@dataclass(frozen=True, slots=True)
class RunRetryProjection:
    run: Run
    decision_digest: Digest
    retry_intent_digest: Digest | None

    def __post_init__(self) -> None:
        if not isinstance(self.run, Run):
            _invalid("run_retry_projection", "run", "invalid")
        _digest("run_retry_projection", "decision_digest", self.decision_digest)
        if self.retry_intent_digest is not None:
            _digest("run_retry_projection", "retry_intent_digest", self.retry_intent_digest)


@dataclass(frozen=True, slots=True)
class RunRetrySideEffect:
    run_id: str
    decision_digest: Digest
    retry_intent_digest: Digest | None

    def __post_init__(self) -> None:
        _string("run_retry_side_effect", "run_id", self.run_id)
        _digest("run_retry_side_effect", "decision_digest", self.decision_digest)
        if self.retry_intent_digest is not None:
            _digest("run_retry_side_effect", "retry_intent_digest", self.retry_intent_digest)


@dataclass(frozen=True, slots=True)
class RunRetryPublication:
    identity_scope: RunRetryIdentityScope
    authority: RunRetryWriteAuthority
    expected_snapshot: RunRetryMutationSnapshot
    intent: RetryIntent | None
    decision: RunRetryDecision
    projection: RunRetryProjection
    reservation: RetryBudgetReservation | None
    side_effect: RunRetrySideEffect

    def __post_init__(self) -> None:
        entity = "run_retry_publication"
        if (
            not isinstance(self.identity_scope, tuple)
            or len(self.identity_scope) != 2
            or any(not isinstance(value, str) or not value for value in self.identity_scope)
        ):
            _invalid(entity, "identity_scope", "invalid")
        for field, kind in (
            ("authority", RunRetryWriteAuthority),
            ("expected_snapshot", RunRetryMutationSnapshot),
            ("decision", RunRetryDecision),
            ("projection", RunRetryProjection),
            ("side_effect", RunRetrySideEffect),
        ):
            if not isinstance(getattr(self, field), kind):
                _invalid(entity, field, "invalid")
        retrying = self.decision.decision is RetryDecision.RETRY
        if retrying != (self.intent is not None) or retrying != (self.reservation is not None):
            _invalid(entity, "participants", "decision_mismatch")
        intent_digest = self.intent.digest if self.intent is not None else None
        if (
            self.authority.run_id != self.decision.source.run_id
            or self.authority.policy_digest != self.decision.policy_digest
            or self.authority.authority_schema != self.decision.authority_schema
            or self.authority.authority_id != self.decision.authority_id
            or self.authority.authority_version != self.decision.authority_version
            or self.authority.authority_digest != self.decision.authority_digest
            or self.expected_snapshot.run.id != self.decision.source.run_id
            or intent_digest != self.decision.retry_intent_digest
            or self.projection.run.id != self.decision.source.run_id
            or self.projection.decision_digest != self.decision.decision_digest
            or self.projection.retry_intent_digest != intent_digest
            or (
                self.reservation is not None
                and self.reservation.retry_intent_digest != intent_digest
            )
            or (
                self.reservation is not None
                and self.reservation.decision_digest != self.decision.decision_digest
            )
            or (
                self.reservation is not None
                and self.reservation.configured != self.decision.budget
            )
            or (self.reservation is not None and self.reservation.usage != self.decision.usage)
            or (
                self.reservation is not None
                and self.reservation.requested != self.decision.requested
            )
            or self.side_effect.run_id != self.decision.source.run_id
            or self.side_effect.decision_digest != self.decision.decision_digest
            or self.side_effect.retry_intent_digest != intent_digest
        ):
            _invalid(entity, "binding", "mismatch")


@dataclass(frozen=True, slots=True)
class UnknownRetryPublication:
    identity_scope: RunRetryIdentityScope
    authority: UnknownRetryWriteAuthority
    expected_snapshot: UnknownRetryMutationSnapshot
    intent: RetryIntent
    projection: RunRetryProjection
    acceptance_request: DuplicateRiskAcceptanceRequest | None
    side_effect: RunRetrySideEffect

    def __post_init__(self) -> None:
        entity = "unknown_retry_publication"
        if (
            not isinstance(self.identity_scope, tuple)
            or len(self.identity_scope) != 2
            or any(not isinstance(value, str) or not value for value in self.identity_scope)
        ):
            _invalid(entity, "identity_scope", "invalid")
        for field, kind in (
            ("authority", UnknownRetryWriteAuthority),
            ("expected_snapshot", UnknownRetryMutationSnapshot),
            ("intent", RetryIntent),
            ("projection", RunRetryProjection),
            ("side_effect", RunRetrySideEffect),
        ):
            if not isinstance(getattr(self, field), kind):
                _invalid(entity, field, "invalid")
        if not isinstance(self.intent.authority, UnknownAdjudicationRetryAuthority):
            _invalid(entity, "intent", "not_unknown")
        duplicate = (
            self.authority.decision is UnknownAdjudicationDecision.ACCEPT_DUPLICATE_RISK_THEN_RETRY
        )
        if duplicate != isinstance(self.acceptance_request, DuplicateRiskAcceptanceRequest):
            _invalid(entity, "acceptance_request", "decision_mismatch")
        if duplicate:
            request = self.acceptance_request
            if request is None:  # pragma: no cover - narrowed by the typed matrix above
                _invalid(entity, "acceptance_request", "missing")
            acceptance, basis = request.acceptance, request.acceptance.basis
            binding = self.expected_snapshot.binding
            if (
                self.authority.risk_acceptance_basis_digest != basis.digest
                or acceptance.retry_intent_digest != self.intent.digest
                or basis.run_id != self.intent.run_id
                or basis.source_attempt_id != self.intent.source_attempt_id
                or basis.source_attempt_no != self.intent.source_attempt_no
                or basis.source_fence != self.intent.source_fence
                or basis.run_item_set_digest != binding.run_item_set_digest
                or basis.sut_identity != binding.sut_identity
                or basis.sut_digest != binding.sut_digest
                or basis.target_grant_identity != binding.target_grant_identity
                or basis.target_grant_version != binding.target_grant_version
                or basis.target_grant_digest != binding.target_grant_digest
            ):
                _invalid(entity, "acceptance_binding", "mismatch")
        if (
            self.authority.run_id != self.intent.run_id
            or self.authority.adjudication_id != self.intent.adjudication_id
            or self.authority.adjudication_digest != self.intent.adjudication_digest
            or self.authority.decision is not self.intent.decision
            or self.authority.attempt_id != self.intent.source_attempt_id
            or self.authority.attempt_version != self.expected_snapshot.attempt_version
            or self.authority.attempt_fence != self.intent.source_fence
            or self.expected_snapshot.run.id != self.intent.run_id
            or self.projection.run.id != self.intent.run_id
            or self.projection.retry_intent_digest != self.intent.digest
            or self.projection.decision_digest != self.authority.adjudication_digest
            or self.side_effect
            != RunRetrySideEffect(
                self.intent.run_id, self.authority.adjudication_digest, self.intent.digest
            )
        ):
            _invalid(entity, "binding", "mismatch")


type RetryPublication = RunRetryPublication | UnknownRetryPublication


@runtime_checkable
class RunRetryGateway(Protocol):
    async def require_retry_authority(self, *, run_id: str) -> RunRetryWriteAuthority: ...
    async def require_unknown_retry_authority(
        self, *, run_id: str
    ) -> UnknownRetryWriteAuthority: ...
    async def lookup_stored(
        self, *, identity_scope: RunRetryIdentityScope
    ) -> RunRetryProjection | None: ...
    async def get_mutation_snapshot_for_update(
        self, *, run_id: str
    ) -> RunRetryMutationSnapshot: ...
    async def get_unknown_mutation_snapshot_for_update(
        self, *, run_id: str
    ) -> UnknownRetryMutationSnapshot: ...
    async def publish_retry(
        self, *, publication: RetryPublication
    ) -> ReplayResult[RunRetryProjection]: ...


def _string(entity: str, field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        _invalid(entity, field, "invalid")


def _digest(entity: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity, field, "not_digest")


def _positive(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _invalid(entity, field, "invalid")


def _nonnegative(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(entity, field, "invalid")


def _invalid(entity: str, field: str, reason: str) -> None:
    raise PortContractError(resource=entity, field=field, reason=reason)
