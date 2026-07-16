"""Copy-on-write Fake for entering Batch finalization."""

from qarunner.application.ports.batch_finalization_readiness import (
    BatchFinalizationReadinessAuthority,
    BatchFinalizationReadinessIdentityScope,
    BatchFinalizationReadinessMutationSnapshot,
    BatchFinalizationReadinessProjection,
    BatchFinalizationReadinessPublication,
    BatchFinalizationReadinessSideEffect,
)
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityStateConflict,
)
from qarunner.application.ports.common import ReplayResult
from qarunner.domain.errors import IdempotencyConflict, VersionConflict


class InMemoryBatchFinalizationReadinessGateway:
    def __init__(
        self,
        authority: BatchFinalizationReadinessAuthority,
        mutation_snapshot: BatchFinalizationReadinessMutationSnapshot,
        *,
        fault_at: str | None = None,
    ) -> None:
        self.authority = authority
        self.mutation_snapshot = mutation_snapshot
        self.fault_at = fault_at
        self.authority_current = True
        self.mutation_snapshot_reads = 0
        self.publication_attempts = 0
        self.publication_commits = 0
        self.projections: dict[
            BatchFinalizationReadinessIdentityScope, BatchFinalizationReadinessProjection
        ] = {}
        self.audit_records: tuple[BatchFinalizationReadinessSideEffect, ...] = ()
        self.semantic_outbox: tuple[BatchFinalizationReadinessSideEffect, ...] = ()

    async def require_readiness_authority(
        self, *, batch_id: str
    ) -> BatchFinalizationReadinessAuthority:
        if batch_id != self.authority.snapshot.batch_id:
            raise AuthorityPermissionDenied
        if not self.authority_current:
            raise AuthorityStateConflict(reason="batch_readiness_authority_superseded")
        return self.authority

    async def lookup_stored(
        self, *, identity_scope: BatchFinalizationReadinessIdentityScope
    ) -> BatchFinalizationReadinessProjection | None:
        return self.projections.get(identity_scope)

    async def get_mutation_snapshot_for_update(
        self, *, batch_id: str
    ) -> BatchFinalizationReadinessMutationSnapshot:
        self.mutation_snapshot_reads += 1
        if batch_id != self.mutation_snapshot.batch_id:
            raise KeyError(batch_id)
        return self.mutation_snapshot

    async def publish_readiness(
        self, *, publication: BatchFinalizationReadinessPublication
    ) -> ReplayResult[BatchFinalizationReadinessProjection]:
        self.publication_attempts += 1
        scope = publication.identity_scope
        stored = self.projections.get(scope)
        if stored is not None:
            if stored.readiness_digest != publication.projection.readiness_digest:
                raise IdempotencyConflict(
                    scope=f"batch:{publication.projection.batch_id}:begin-finalization",
                    key=str(publication.projection.source_batch_version),
                    stored_digest=stored.readiness_digest,
                    received_digest=publication.projection.readiness_digest,
                )
            return ReplayResult(stored, True)
        if publication.expected_snapshot != self.mutation_snapshot:
            raise VersionConflict(
                entity_type="batch",
                entity_id=self.mutation_snapshot.batch_id,
                current_version=self.mutation_snapshot.batch_version,
                expected_version=publication.expected_snapshot.batch_version,
            )
        if publication.authority != self.authority or not self.authority_current:
            raise AuthorityStateConflict(reason="publication_authority_superseded")
        projections = dict(self.projections)
        audit = self.audit_records
        outbox = self.semantic_outbox
        self._raise_at("projection")
        projections[scope] = publication.projection
        self._raise_at("audit")
        audit += (publication.side_effect,)
        self._raise_at("outbox")
        outbox += (publication.side_effect,)
        self._raise_at("commit")
        self.projections = projections
        self.audit_records = audit
        self.semantic_outbox = outbox
        self.publication_commits += 1
        return ReplayResult(publication.projection, False)

    def _raise_at(self, point: str) -> None:
        if self.fault_at == point:
            self.fault_at = None
            raise RuntimeError(f"batch_finalization_readiness_fault:{point}")

    @property
    def projection_count(self) -> int:
        return len(self.projections)

    @property
    def audit_count(self) -> int:
        return len(self.audit_records)

    @property
    def outbox_count(self) -> int:
        return len(self.semantic_outbox)
