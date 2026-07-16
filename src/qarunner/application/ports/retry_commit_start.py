"""Atomic port contracts for retry commit-start publication."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.run_retry import (
    RetryQueueReceipt,
    RunRetryWriteAuthority,
    UnknownRetryWriteAuthority,
)
from qarunner.domain import (
    Digest,
    Run,
)
from qarunner.domain.run import CommitStartResult

type RetryCommitAuthority = RunRetryWriteAuthority | UnknownRetryWriteAuthority
type RetryCommitIdentityScope = tuple[str, str]


@dataclass(frozen=True, slots=True)
class RetryCommitMutationSnapshot:
    run: Run
    source_attempt_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.run, Run):
            _invalid("retry_commit_mutation_snapshot", "run")
        if (
            isinstance(self.source_attempt_version, bool)
            or not isinstance(self.source_attempt_version, int)
            or self.source_attempt_version < 0
        ):
            _invalid("retry_commit_mutation_snapshot", "source_attempt_version")


@dataclass(frozen=True, slots=True)
class RetryCommitSideEffect:
    run_id: str
    retry_intent_digest: Digest
    attempt_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            _invalid("retry_commit_side_effect", "run_id")
        if not isinstance(self.retry_intent_digest, Digest):
            _invalid("retry_commit_side_effect", "retry_intent_digest")
        if not isinstance(self.attempt_id, str) or not self.attempt_id.strip():
            _invalid("retry_commit_side_effect", "attempt_id")


@dataclass(frozen=True, slots=True)
class StoredRetryCommit:
    commit: CommitStartResult
    receipt_digest: Digest

    def __post_init__(self) -> None:
        if not isinstance(self.commit, CommitStartResult):
            _invalid("stored_retry_commit", "commit")
        if not isinstance(self.receipt_digest, Digest):
            _invalid("stored_retry_commit", "receipt_digest")


@dataclass(frozen=True, slots=True)
class RetryCommitPublication:
    identity_scope: RetryCommitIdentityScope
    authority: RetryCommitAuthority
    expected_snapshot: RetryCommitMutationSnapshot
    receipt: RetryQueueReceipt
    commit: CommitStartResult
    side_effect: RetryCommitSideEffect

    def __post_init__(self) -> None:
        if (
            not isinstance(self.identity_scope, tuple)
            or len(self.identity_scope) != 2
            or any(not isinstance(value, str) or not value for value in self.identity_scope)
        ):
            _invalid("retry_commit_publication", "identity_scope")
        if not isinstance(self.authority, (RunRetryWriteAuthority, UnknownRetryWriteAuthority)):
            _invalid("retry_commit_publication", "authority")
        if not isinstance(self.expected_snapshot, RetryCommitMutationSnapshot):
            _invalid("retry_commit_publication", "expected_snapshot")
        if not isinstance(self.receipt, RetryQueueReceipt):
            _invalid("retry_commit_publication", "receipt")
        if not isinstance(self.commit, CommitStartResult):
            _invalid("retry_commit_publication", "commit")
        if not isinstance(self.side_effect, RetryCommitSideEffect):
            _invalid("retry_commit_publication", "side_effect")
        if (
            self.expected_snapshot.run.id != self.receipt.intent.run_id
            or self.commit.run.id != self.receipt.intent.run_id
            or self.commit.attempt.retry_provenance is None
            or self.commit.attempt.retry_provenance.retry_intent_digest
            != self.receipt.intent.digest
            or self.side_effect
            != RetryCommitSideEffect(
                self.receipt.intent.run_id,
                self.receipt.intent.digest,
                self.commit.attempt.id,
            )
        ):
            _invalid("retry_commit_publication", "binding")


@runtime_checkable
class RetryCommitStartGateway(Protocol):
    async def require_current_authority(
        self, *, receipt: RetryQueueReceipt
    ) -> RetryCommitAuthority: ...
    async def lookup_stored(
        self, *, identity_scope: RetryCommitIdentityScope
    ) -> StoredRetryCommit | None: ...
    async def require_receipt_for_update(
        self, *, retry_intent_digest: Digest
    ) -> RetryQueueReceipt: ...
    async def get_snapshot_for_update(self, *, run_id: str) -> RetryCommitMutationSnapshot: ...
    async def publish(
        self, *, publication: RetryCommitPublication
    ) -> ReplayResult[CommitStartResult]: ...


def _invalid(resource: str, field: str) -> None:
    raise PortContractError(resource=resource, field=field, reason="invalid")
