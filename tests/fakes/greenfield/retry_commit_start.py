"""Copy-on-write Fake for retry commit-start."""

from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.common import ReplayResult
from qarunner.application.ports.retry_commit_start import (
    RetryCommitIdentityScope,
    RetryCommitMutationSnapshot,
    RetryCommitPublication,
    RetryCommitSideEffect,
    RetryQueueReceipt,
    StoredRetryCommit,
)
from qarunner.domain import IdempotencyConflict, VersionConflict
from qarunner.domain.run import CommitStartResult


class InMemoryRetryCommitStartGateway:
    def __init__(
        self,
        *,
        authority,
        mutation_snapshot: RetryCommitMutationSnapshot,
        authority_current: bool = True,
        fault_at: str | None = None,
        queue_receipts: dict | None = None,
    ) -> None:
        self.authority = authority
        self.mutation_snapshot = mutation_snapshot
        self.authority_current = authority_current
        self.fault_at = fault_at
        self.cancel_at_publish = False
        self.authority_checks = self.stored_lookups = self.snapshot_reads = 0
        self.projections: dict[RetryCommitIdentityScope, CommitStartResult] = {}
        self.receipt_digests = {}
        self.queue_receipts = queue_receipts or {}
        self.audit_records: tuple[RetryCommitSideEffect, ...] = ()
        self.semantic_outbox: tuple[RetryCommitSideEffect, ...] = ()

    async def require_current_authority(self, *, receipt: RetryQueueReceipt):
        self.authority_checks += 1
        if not self.authority_current or self.authority.run_id != receipt.intent.run_id:
            raise AuthorityStateConflict(reason="retry_commit_authority_superseded")
        return self.authority

    async def lookup_stored(self, *, identity_scope):
        self.stored_lookups += 1
        commit = self.projections.get(identity_scope)
        return (
            StoredRetryCommit(commit, self.receipt_digests[identity_scope])
            if commit is not None
            else None
        )

    async def require_receipt_for_update(self, *, retry_intent_digest):
        try:
            return self.queue_receipts[retry_intent_digest]
        except KeyError:
            raise AuthorityStateConflict(reason="retry_queue_receipt_missing") from None

    async def get_snapshot_for_update(self, *, run_id: str):
        self.snapshot_reads += 1
        if run_id != self.mutation_snapshot.run.id:
            raise KeyError(run_id)
        return self.mutation_snapshot

    async def publish(self, *, publication: RetryCommitPublication):
        scope = publication.identity_scope
        stored = self.projections.get(scope)
        if stored is not None:
            if (
                stored.attempt.retry_provenance.retry_intent_digest
                != publication.receipt.intent.digest
            ):
                raise IdempotencyConflict(
                    scope=scope[0],
                    key=scope[1],
                    stored_digest=stored.attempt.retry_provenance.retry_intent_digest,
                    received_digest=publication.receipt.intent.digest,
                )
            return ReplayResult(stored, True)
        if not self.authority_current or publication.authority != self.authority:
            raise AuthorityStateConflict(reason="retry_commit_authority_superseded")
        if self.cancel_at_publish or publication.expected_snapshot != self.mutation_snapshot:
            raise VersionConflict(
                entity_type="retry_commit_snapshot",
                entity_id=publication.receipt.intent.run_id,
                current_version=self.mutation_snapshot.run.version,
                expected_version=publication.expected_snapshot.run.version,
            )
        projections = dict(self.projections)
        receipts = dict(self.receipt_digests)
        audit, outbox = self.audit_records, self.semantic_outbox
        self._fault("attempt")
        self._fault("run")
        projections[scope] = publication.commit
        receipts[scope] = publication.receipt.digest
        self._fault("audit")
        audit += (publication.side_effect,)
        self._fault("outbox")
        outbox += (publication.side_effect,)
        self._fault("commit")
        self.projections, self.receipt_digests = projections, receipts
        self.audit_records, self.semantic_outbox = audit, outbox
        return ReplayResult(publication.commit, False)

    def _fault(self, point: str) -> None:
        if self.fault_at == point:
            self.fault_at = None
            raise RuntimeError(f"retry_commit_fault:{point}")
