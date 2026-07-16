"""Copy-on-write Fake for atomic policy retry publication."""

from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.common import ReplayResult
from qarunner.application.ports.run_retry import (
    RunRetryIdentityScope,
    RunRetryMutationSnapshot,
    RunRetryProjection,
    RunRetryPublication,
    RunRetrySideEffect,
    RunRetryWriteAuthority,
)
from qarunner.domain import Digest, IdempotencyConflict, VersionConflict


class InMemoryRunRetryGateway:
    def __init__(
        self,
        *,
        authority: RunRetryWriteAuthority,
        mutation_snapshot: RunRetryMutationSnapshot,
        authority_current: bool = True,
        fault_at: str | None = None,
    ) -> None:
        self.authority = authority
        self.mutation_snapshot = mutation_snapshot
        self.authority_current = authority_current
        self.fault_at = fault_at
        self.authority_checks = self.stored_lookups = self.snapshot_reads = 0
        self.publication_attempts = self.publication_commits = 0
        self.intent_digests: dict[RunRetryIdentityScope, Digest] = {}
        self.projections: dict[RunRetryIdentityScope, RunRetryProjection] = {}
        self.decisions: dict[RunRetryIdentityScope, object] = {}
        self.reservations: dict[RunRetryIdentityScope, object] = {}
        self.audit_records: tuple[RunRetrySideEffect, ...] = ()
        self.semantic_outbox: tuple[RunRetrySideEffect, ...] = ()

    async def require_retry_authority(self, *, run_id: str) -> RunRetryWriteAuthority:
        self.authority_checks += 1
        if not self.authority_current or run_id != self.authority.run_id:
            raise AuthorityStateConflict(reason="retry_authority_superseded")
        return self.authority

    async def lookup_stored(
        self, *, identity_scope: RunRetryIdentityScope
    ) -> RunRetryProjection | None:
        self.stored_lookups += 1
        return self.projections.get(identity_scope)

    async def get_mutation_snapshot_for_update(self, *, run_id: str) -> RunRetryMutationSnapshot:
        self.snapshot_reads += 1
        if run_id != self.mutation_snapshot.run.id:
            raise KeyError(run_id)
        return self.mutation_snapshot

    async def publish_retry(
        self, *, publication: RunRetryPublication
    ) -> ReplayResult[RunRetryProjection]:
        self.publication_attempts += 1
        scope = publication.identity_scope
        stored = self.projections.get(scope)
        if stored is not None:
            if (
                stored.retry_intent_digest != publication.decision.retry_intent_digest
                or stored.decision_digest != publication.decision.decision_digest
            ):
                raise IdempotencyConflict(
                    scope=scope[0],
                    key=scope[1],
                    stored_digest=stored.decision_digest,
                    received_digest=publication.decision.decision_digest,
                )
            return ReplayResult(stored, True)
        if not self.authority_current or publication.authority != self.authority:
            raise AuthorityStateConflict(reason="retry_publication_authority_superseded")
        if publication.expected_snapshot != self.mutation_snapshot:
            raise VersionConflict(
                entity_type="run_retry_snapshot",
                entity_id=self.mutation_snapshot.run.id,
                current_version=self.mutation_snapshot.run.version,
                expected_version=publication.expected_snapshot.run.version,
            )
        intents, projections = dict(self.intent_digests), dict(self.projections)
        decisions, reservations = dict(self.decisions), dict(self.reservations)
        audit, outbox = self.audit_records, self.semantic_outbox
        self._fault("intent")
        if publication.intent is not None:
            intents[scope] = publication.intent.digest
        self._fault("decision")
        decisions[scope] = publication.decision
        self._fault("run")
        projections[scope] = publication.projection
        self._fault("reservation")
        if publication.reservation is not None:
            reservations[scope] = publication.reservation
        self._fault("audit")
        audit += (publication.side_effect,)
        self._fault("outbox")
        outbox += (publication.side_effect,)
        self._fault("commit")
        self.intent_digests, self.projections = intents, projections
        self.decisions, self.reservations = decisions, reservations
        self.audit_records, self.semantic_outbox = audit, outbox
        self.publication_commits += 1
        return ReplayResult(publication.projection, False)

    def _fault(self, point: str) -> None:
        if self.fault_at == point:
            self.fault_at = None
            raise RuntimeError(f"run_retry_fault:{point}")
