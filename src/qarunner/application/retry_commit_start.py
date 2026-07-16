"""Commit one queued retry only after current authority revalidation."""

from dataclasses import dataclass
from datetime import datetime

from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.retry_commit_start import (
    RetryCommitPublication,
    RetryCommitSideEffect,
    RetryCommitStartGateway,
    RetryQueueReceipt,
)
from qarunner.application.ports.run_retry import RunRetryWriteAuthority, UnknownRetryWriteAuthority
from qarunner.domain import (
    Digest,
    IdempotencyConflict,
    PolicyRetryAuthority,
    UnknownAdjudicationRetryAuthority,
    VersionConflict,
    WorkerRef,
    canonical_digest,
)
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.run import CommitStartResult


@dataclass(frozen=True, slots=True)
class CommitRetryStartCommand:
    identity_scope: tuple[str, str]
    receipt: RetryQueueReceipt
    assignment_id: str
    worker: WorkerRef
    start_commit_key: str
    spec_digest: Digest
    new_attempt_id: str
    observed_at: datetime
    expected_run_version: int
    expected_attempt_version: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.identity_scope, tuple)
            or len(self.identity_scope) != 2
            or any(not isinstance(value, str) or not value for value in self.identity_scope)
        ):
            _invalid_command("identity_scope")
        for field, kind in (
            ("receipt", RetryQueueReceipt),
            ("worker", WorkerRef),
            ("spec_digest", Digest),
        ):
            if not isinstance(getattr(self, field), kind):
                _invalid_command(field)
        for field in ("assignment_id", "start_commit_key", "new_attempt_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                _invalid_command(field)
        if not isinstance(self.observed_at, datetime):
            _invalid_command("observed_at")
        for field in ("expected_run_version", "expected_attempt_version"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                _invalid_command(field)


@dataclass(frozen=True, slots=True)
class CommitRetryStartResult:
    commit: CommitStartResult
    replayed: bool


class CommitRetryStart:
    def __init__(self, *, gateway: RetryCommitStartGateway) -> None:
        self._gateway = gateway

    async def execute(self, command: CommitRetryStartCommand) -> CommitRetryStartResult:
        receipt, intent = command.receipt, command.receipt.intent
        authority = await self._gateway.require_current_authority(receipt=receipt)
        if not _authority_matches(authority, receipt):
            raise AuthorityStateConflict(reason="retry_commit_authority_superseded")
        canonical = await self._gateway.require_receipt_for_update(
            retry_intent_digest=intent.digest
        )
        if canonical.digest != receipt.digest:
            raise AuthorityStateConflict(reason="retry_queue_receipt_mismatch")
        receipt = canonical
        stored = await self._gateway.lookup_stored(identity_scope=command.identity_scope)
        if stored is not None:
            provenance = stored.commit.attempt.retry_provenance
            if (
                stored.receipt_digest != receipt.digest
                or provenance is None
                or provenance.retry_intent_digest != intent.digest
                or stored.commit.attempt.id != command.new_attempt_id
                or stored.commit.attempt.assignment_id != command.assignment_id
                or stored.commit.attempt.worker != command.worker
                or stored.commit.attempt.start_commit_key != command.start_commit_key
                or stored.commit.attempt.spec_digest != command.spec_digest
            ):
                raise IdempotencyConflict(
                    scope=command.identity_scope[0],
                    key=command.identity_scope[1],
                    stored_digest=provenance.retry_intent_digest
                    if provenance
                    else receipt.queue_decision_digest,
                    received_digest=intent.digest,
                )
            return CommitRetryStartResult(stored.commit, True)
        snapshot = await self._gateway.get_snapshot_for_update(run_id=intent.run_id)
        if snapshot.source_attempt_version != command.expected_attempt_version:
            raise VersionConflict(
                entity_type="attempt",
                entity_id=intent.source_attempt_id,
                current_version=snapshot.source_attempt_version,
                expected_version=command.expected_attempt_version,
            )
        if snapshot.run.version != command.expected_run_version:
            raise VersionConflict(
                entity_type="run",
                entity_id=intent.run_id,
                current_version=snapshot.run.version,
                expected_version=command.expected_run_version,
            )
        commit = snapshot.run.commit_start(
            assignment_id=command.assignment_id,
            worker=command.worker,
            start_commit_key=command.start_commit_key,
            spec_digest=command.spec_digest,
            new_attempt_id=command.new_attempt_id,
            observed_at=command.observed_at,
            expected_version=command.expected_run_version,
        )
        result = await self._gateway.publish(
            publication=RetryCommitPublication(
                command.identity_scope,
                authority,
                snapshot,
                receipt,
                commit,
                RetryCommitSideEffect(intent.run_id, intent.digest, commit.attempt.id),
            )
        )
        return CommitRetryStartResult(result.value, result.replayed)


def _authority_matches(authority, receipt: RetryQueueReceipt) -> bool:
    pinned = receipt.intent.authority
    if isinstance(pinned, PolicyRetryAuthority):
        return isinstance(authority, RunRetryWriteAuthority) and (
            authority.run_id == receipt.intent.run_id
            and authority.policy_digest == pinned.policy_digest
            and authority.authority_schema == pinned.authority_schema
            and authority.authority_id == pinned.authority_id
            and authority.authority_version == pinned.authority_version
            and authority.authority_digest == pinned.authority_digest
        )
    matches = (
        isinstance(pinned, UnknownAdjudicationRetryAuthority)
        and isinstance(authority, UnknownRetryWriteAuthority)
        and (
            authority.run_id == receipt.intent.run_id
            and authority.attempt_id == receipt.intent.source_attempt_id
            and authority.attempt_fence == receipt.intent.source_fence
            and authority.adjudication_id == pinned.adjudication_id
            and authority.adjudication_digest == pinned.adjudication_digest
            and authority.decision is pinned.decision
        )
    )
    if not matches:
        return False
    consumption = receipt.duplicate_acceptance_consumption
    if consumption is None:
        return True
    expected = canonical_digest(
        schema_version="qep.duplicate-risk-acceptance.v2",
        payload={
            "basis_digest": authority.risk_acceptance_basis_digest.value,
            "retry_intent_digest": receipt.intent.digest.value,
        },
    )
    return consumption.acceptance_digest == expected


def _invalid_command(field: str) -> None:
    raise DomainValidationError(
        entity_type="commit_retry_start_command", field=field, reason="invalid"
    )
