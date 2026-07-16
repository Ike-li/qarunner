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
    UnknownRetryMutationSnapshot,
    UnknownRetryPublication,
    UnknownRetryWriteAuthority,
)
from qarunner.domain import (
    Digest,
    DuplicateRiskAcceptanceConsumption,
    IdempotencyConflict,
    VersionConflict,
    consume_duplicate_risk_acceptance,
)


class InMemoryRunRetryGateway:
    def __init__(
        self,
        *,
        authority: RunRetryWriteAuthority | None = None,
        mutation_snapshot: RunRetryMutationSnapshot | None = None,
        unknown_authority: UnknownRetryWriteAuthority | None = None,
        unknown_mutation_snapshot: UnknownRetryMutationSnapshot | None = None,
        authority_current: bool = True,
        fault_at: str | None = None,
    ) -> None:
        self.authority = authority
        self.mutation_snapshot = mutation_snapshot
        self.unknown_authority = unknown_authority
        self.unknown_mutation_snapshot = unknown_mutation_snapshot
        self.authority_current = authority_current
        self.fault_at = fault_at
        self.authority_checks = self.stored_lookups = self.snapshot_reads = 0
        self.unknown_authority_checks = self.unknown_snapshot_reads = 0
        self.publication_attempts = self.publication_commits = 0
        self.intent_digests: dict[RunRetryIdentityScope, Digest] = {}
        self.projections: dict[RunRetryIdentityScope, RunRetryProjection] = {}
        self.decisions: dict[RunRetryIdentityScope, object] = {}
        self.reservations: dict[RunRetryIdentityScope, object] = {}
        self.audit_records: tuple[RunRetrySideEffect, ...] = ()
        self.semantic_outbox: tuple[RunRetrySideEffect, ...] = ()
        self.acceptance_consumptions: dict[
            tuple[str, str], DuplicateRiskAcceptanceConsumption
        ] = {}
        self.cancel_at_publish = False

    async def require_retry_authority(self, *, run_id: str) -> RunRetryWriteAuthority:
        self.authority_checks += 1
        if not self.authority_current or run_id != self.authority.run_id:
            raise AuthorityStateConflict(reason="retry_authority_superseded")
        return self.authority

    async def require_unknown_retry_authority(self, *, run_id: str) -> UnknownRetryWriteAuthority:
        self.unknown_authority_checks += 1
        if (
            not self.authority_current
            or self.unknown_authority is None
            or run_id != self.unknown_authority.run_id
        ):
            raise AuthorityStateConflict(reason="unknown_retry_authority_superseded")
        return self.unknown_authority

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

    async def get_unknown_mutation_snapshot_for_update(
        self, *, run_id: str
    ) -> UnknownRetryMutationSnapshot:
        self.unknown_snapshot_reads += 1
        if (
            self.unknown_mutation_snapshot is None
            or run_id != self.unknown_mutation_snapshot.run.id
        ):
            raise KeyError(run_id)
        return self.unknown_mutation_snapshot

    async def publish_retry(
        self, *, publication: RunRetryPublication | UnknownRetryPublication
    ) -> ReplayResult[RunRetryProjection]:
        self.publication_attempts += 1
        scope = publication.identity_scope
        decision_digest = (
            publication.authority.adjudication_digest
            if isinstance(publication, UnknownRetryPublication)
            else publication.decision.decision_digest
        )
        intent_digest = publication.intent.digest if publication.intent is not None else None
        stored = self.projections.get(scope)
        if stored is not None:
            if (
                stored.retry_intent_digest != intent_digest
                or stored.decision_digest != decision_digest
            ):
                raise IdempotencyConflict(
                    scope=scope[0],
                    key=scope[1],
                    stored_digest=stored.decision_digest,
                    received_digest=decision_digest,
                )
            return ReplayResult(stored, True)
        expected_authority = (
            self.unknown_authority
            if isinstance(publication, UnknownRetryPublication)
            else self.authority
        )
        expected_snapshot = (
            self.unknown_mutation_snapshot
            if isinstance(publication, UnknownRetryPublication)
            else self.mutation_snapshot
        )
        if not self.authority_current or publication.authority != expected_authority:
            raise AuthorityStateConflict(reason="retry_publication_authority_superseded")
        if self.cancel_at_publish or publication.expected_snapshot != expected_snapshot:
            raise VersionConflict(
                entity_type="run_retry_snapshot",
                entity_id=expected_snapshot.run.id,
                current_version=expected_snapshot.run.version,
                expected_version=publication.expected_snapshot.run.version,
            )
        intents, projections = dict(self.intent_digests), dict(self.projections)
        decisions, reservations = dict(self.decisions), dict(self.reservations)
        audit, outbox = self.audit_records, self.semantic_outbox
        acceptances = dict(self.acceptance_consumptions)
        self._fault("intent")
        if publication.intent is not None:
            intents[scope] = publication.intent.digest
        if isinstance(publication, UnknownRetryPublication) and publication.acceptance_request:
            self._fault("acceptance")
            request = publication.acceptance_request
            prior = acceptances.get((request.scope, request.key))
            consumed = consume_duplicate_risk_acceptance(request=request, prior=prior)
            if isinstance(consumed, DuplicateRiskAcceptanceConsumption):
                acceptances[(request.scope, request.key)] = consumed
        self._fault("decision")
        decisions[scope] = (
            publication.intent.authority
            if isinstance(publication, UnknownRetryPublication)
            else publication.decision
        )
        self._fault("run")
        projections[scope] = publication.projection
        self._fault("reservation")
        if (
            not isinstance(publication, UnknownRetryPublication)
            and publication.reservation is not None
        ):
            reservations[scope] = publication.reservation
        self._fault("audit")
        audit += (publication.side_effect,)
        self._fault("outbox")
        outbox += (publication.side_effect,)
        self._fault("commit")
        self.intent_digests, self.projections = intents, projections
        self.decisions, self.reservations = decisions, reservations
        self.audit_records, self.semantic_outbox = audit, outbox
        self.acceptance_consumptions = acceptances
        self.publication_commits += 1
        return ReplayResult(publication.projection, False)

    def _fault(self, point: str) -> None:
        if self.fault_at == point:
            self.fault_at = None
            raise RuntimeError(f"run_retry_fault:{point}")
