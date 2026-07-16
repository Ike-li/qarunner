"""Queue a policy-authorized Run retry and publish it atomically."""

from dataclasses import dataclass

from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.run_retry import (
    RetryBudgetReservation,
    RunRetryGateway,
    RunRetryProjection,
    RunRetryPublication,
    RunRetrySideEffect,
    UnknownRetryPublication,
)
from qarunner.domain import (
    DuplicateRiskAcceptanceRequest,
    RetryIntent,
    RunRetryDecision,
    UnknownAdjudicationDecision,
    UnknownAdjudicationRetryAuthority,
)
from qarunner.domain.errors import DomainValidationError, IdempotencyConflict, VersionConflict
from qarunner.domain.run_retry_policy import RetryDecision


@dataclass(frozen=True, slots=True)
class QueuePolicyRetryCommand:
    identity_scope: tuple[str, str]
    candidate_intent: RetryIntent | None
    decision: RunRetryDecision
    expected_run_version: int
    expected_attempt_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.decision, RunRetryDecision):
            _invalid("decision")
        retrying = self.decision.decision is RetryDecision.RETRY
        if retrying != isinstance(self.candidate_intent, RetryIntent):
            _invalid("candidate_intent")
        intent_digest = self.candidate_intent.digest if self.candidate_intent is not None else None
        if self.decision.retry_intent_digest != intent_digest:
            _invalid("decision_binding")
        _version("expected_run_version", self.expected_run_version)
        _version("expected_attempt_version", self.expected_attempt_version)
        if (
            not isinstance(self.identity_scope, tuple)
            or len(self.identity_scope) != 2
            or any(not isinstance(value, str) or not value for value in self.identity_scope)
        ):
            _invalid("identity_scope")


@dataclass(frozen=True, slots=True)
class QueuePolicyRetryResult:
    projection: RunRetryProjection
    replayed: bool


class QueuePolicyRetry:
    def __init__(self, *, gateway: RunRetryGateway) -> None:
        self._gateway = gateway

    async def execute(self, command: QueuePolicyRetryCommand) -> QueuePolicyRetryResult:
        intent, decision = command.candidate_intent, command.decision
        run_id = decision.source.run_id
        authority = await self._gateway.require_retry_authority(run_id=run_id)
        if (
            authority.policy_digest != decision.policy_digest
            or authority.authority_schema != decision.authority_schema
            or authority.authority_id != decision.authority_id
            or authority.authority_version != decision.authority_version
            or authority.authority_digest != decision.authority_digest
        ):
            raise AuthorityStateConflict(reason="retry_authority_superseded")
        stored = await self._gateway.lookup_stored(identity_scope=command.identity_scope)
        if stored is not None:
            if (
                stored.retry_intent_digest != decision.retry_intent_digest
                or stored.decision_digest != decision.decision_digest
            ):
                raise IdempotencyConflict(
                    scope=command.identity_scope[0],
                    key=command.identity_scope[1],
                    stored_digest=stored.decision_digest,
                    received_digest=decision.decision_digest,
                )
            return QueuePolicyRetryResult(stored, True)
        snapshot = await self._gateway.get_mutation_snapshot_for_update(run_id=run_id)
        if snapshot.attempt_version != command.expected_attempt_version:
            raise VersionConflict(
                entity_type="attempt",
                entity_id=decision.source.attempt_id,
                current_version=snapshot.attempt_version,
                expected_version=command.expected_attempt_version,
            )
        if snapshot.run.version != command.expected_run_version:
            raise VersionConflict(
                entity_type="run",
                entity_id=run_id,
                current_version=snapshot.run.version,
                expected_version=command.expected_run_version,
            )
        queued = snapshot.run
        if intent is not None:
            queued = queued.queue_policy_retry(
                retry_intent=intent,
                retry_decision=decision,
                expected_version=command.expected_run_version,
            )
        projection = RunRetryProjection(
            queued, decision.decision_digest, decision.retry_intent_digest
        )
        publication = RunRetryPublication(
            command.identity_scope,
            authority,
            snapshot,
            intent,
            decision,
            projection,
            RetryBudgetReservation.from_decision(decision) if intent is not None else None,
            RunRetrySideEffect(run_id, decision.decision_digest, decision.retry_intent_digest),
        )
        result = await self._gateway.publish_retry(publication=publication)
        return QueuePolicyRetryResult(result.value, result.replayed)


@dataclass(frozen=True, slots=True)
class QueueAdjudicatedRetryCommand:
    identity_scope: tuple[str, str]
    candidate_intent: RetryIntent
    expected_run_version: int
    expected_attempt_version: int
    acceptance_request: DuplicateRiskAcceptanceRequest | None = None

    def __post_init__(self) -> None:
        authority = getattr(self.candidate_intent, "authority", None)
        if (
            not isinstance(self.candidate_intent, RetryIntent)
            or not isinstance(authority, UnknownAdjudicationRetryAuthority)
            or not authority.decision.permits_retry
        ):
            _invalid_unknown("candidate_intent")
        duplicate = (
            authority.decision is UnknownAdjudicationDecision.ACCEPT_DUPLICATE_RISK_THEN_RETRY
        )
        if duplicate != isinstance(self.acceptance_request, DuplicateRiskAcceptanceRequest):
            _invalid_unknown("acceptance_request")
        _version("expected_run_version", self.expected_run_version)
        _version("expected_attempt_version", self.expected_attempt_version)
        if (
            not isinstance(self.identity_scope, tuple)
            or len(self.identity_scope) != 2
            or any(not isinstance(value, str) or not value for value in self.identity_scope)
        ):
            _invalid_unknown("identity_scope")


@dataclass(frozen=True, slots=True)
class QueueAdjudicatedRetryResult:
    projection: RunRetryProjection
    replayed: bool


class QueueAdjudicatedRetry:
    def __init__(self, *, gateway: RunRetryGateway) -> None:
        self._gateway = gateway

    async def execute(self, command: QueueAdjudicatedRetryCommand) -> QueueAdjudicatedRetryResult:
        intent = command.candidate_intent
        authority = await self._gateway.require_unknown_retry_authority(run_id=intent.run_id)
        if (
            authority.attempt_id != intent.source_attempt_id
            or authority.attempt_fence != intent.source_fence
            or authority.attempt_version != command.expected_attempt_version
            or authority.adjudication_id != intent.adjudication_id
            or authority.adjudication_digest != intent.adjudication_digest
            or authority.decision is not intent.decision
        ):
            raise AuthorityStateConflict(reason="unknown_retry_authority_superseded")
        stored = await self._gateway.lookup_stored(identity_scope=command.identity_scope)
        if stored is not None:
            if (
                stored.retry_intent_digest != intent.digest
                or stored.decision_digest != authority.adjudication_digest
            ):
                raise IdempotencyConflict(
                    scope=command.identity_scope[0],
                    key=command.identity_scope[1],
                    stored_digest=stored.decision_digest,
                    received_digest=authority.adjudication_digest,
                )
            return QueueAdjudicatedRetryResult(stored, True)
        snapshot = await self._gateway.get_unknown_mutation_snapshot_for_update(
            run_id=intent.run_id
        )
        if snapshot.attempt_version != command.expected_attempt_version:
            raise VersionConflict(
                entity_type="attempt",
                entity_id=intent.source_attempt_id,
                current_version=snapshot.attempt_version,
                expected_version=command.expected_attempt_version,
            )
        if snapshot.run.version != command.expected_run_version:
            raise VersionConflict(
                entity_type="run",
                entity_id=intent.run_id,
                current_version=snapshot.run.version,
                expected_version=command.expected_run_version,
            )
        queued = snapshot.run.queue_adjudicated_retry(
            retry_intent=intent, expected_version=command.expected_run_version
        )
        projection = RunRetryProjection(queued, authority.adjudication_digest, intent.digest)
        publication = UnknownRetryPublication(
            command.identity_scope,
            authority,
            snapshot,
            intent,
            projection,
            command.acceptance_request,
            RunRetrySideEffect(intent.run_id, authority.adjudication_digest, intent.digest),
        )
        result = await self._gateway.publish_retry(publication=publication)
        return QueueAdjudicatedRetryResult(result.value, result.replayed)


def _version(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(field)


def _invalid(field: str) -> None:
    raise DomainValidationError(
        entity_type="queue_policy_retry_command", field=field, reason="invalid"
    )


def _invalid_unknown(field: str) -> None:
    raise DomainValidationError(
        entity_type="queue_adjudicated_retry_command", field=field, reason="invalid"
    )
