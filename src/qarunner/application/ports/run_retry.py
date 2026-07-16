"""Atomic application port for policy-authorized Run retry publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import Digest, RetryIntent, Run, RunRetryDecision
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
class RunRetryMutationSnapshot:
    run: Run
    attempt_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.run, Run):
            _invalid("run_retry_mutation_snapshot", "run", "invalid")
        _nonnegative("run_retry_mutation_snapshot", "attempt_version", self.attempt_version)


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


@runtime_checkable
class RunRetryGateway(Protocol):
    async def require_retry_authority(self, *, run_id: str) -> RunRetryWriteAuthority: ...
    async def lookup_stored(
        self, *, identity_scope: RunRetryIdentityScope
    ) -> RunRetryProjection | None: ...
    async def get_mutation_snapshot_for_update(
        self, *, run_id: str
    ) -> RunRetryMutationSnapshot: ...
    async def publish_retry(
        self, *, publication: RunRetryPublication
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
