"""Deterministic replay Fake for the Batch-finalization gateway."""

from qarunner.application.ports.batch_finalization import (
    BatchFinalizationIdentityScope,
    BatchFinalizationMutationSnapshot,
    BatchFinalizationProjection,
    BatchFinalizationPublication,
    BatchFinalizationSideEffect,
    FinalizeBatchAuthority,
)
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
)
from qarunner.application.ports.common import ReplayResult
from qarunner.domain.batch_finalization import BatchFinalizationBasis
from qarunner.domain.digest import Digest
from qarunner.domain.errors import IdempotencyConflict, VersionConflict


class InMemoryBatchFinalizationGateway:
    def __init__(
        self,
        *,
        authority: FinalizeBatchAuthority,
        mutation_snapshot: BatchFinalizationMutationSnapshot,
        projection: BatchFinalizationProjection | None = None,
        basis: BatchFinalizationBasis | None = None,
        fault_at: str | None = None,
        authority_available: bool = True,
        authority_allowed: bool = True,
        authority_current: bool = True,
    ) -> None:
        self.authority = authority
        self.mutation_snapshot = mutation_snapshot
        self.fault_at = fault_at
        self.authority_available = authority_available
        self.authority_allowed = authority_allowed
        self.authority_current = authority_current
        self.authority_checks = 0
        self.stored_lookups = 0
        self.mutation_snapshot_reads = 0
        self.publication_attempts = 0
        self.publication_commits = 0
        self.last_publication: BatchFinalizationPublication | None = None
        self.basis_digests: dict[BatchFinalizationIdentityScope, Digest] = {}
        self.bases: dict[BatchFinalizationIdentityScope, BatchFinalizationBasis] = {}
        self.audit_records: tuple[BatchFinalizationSideEffect, ...] = ()
        self.semantic_outbox: tuple[BatchFinalizationSideEffect, ...] = ()
        self.projections: dict[BatchFinalizationIdentityScope, BatchFinalizationProjection] = {}
        if projection is not None:
            if (
                projection.batch_id != authority.batch_id
                or projection.source_batch_version != authority.source_batch_version
            ):
                raise ValueError("seeded projection authority scope mismatch")
            if basis is None or basis.basis_digest != projection.basis_digest:
                raise ValueError("seeded projection basis mismatch")
            self.projections[authority.identity_scope] = projection
            self.basis_digests[authority.identity_scope] = projection.basis_digest
            self.bases[authority.identity_scope] = basis
            effect = BatchFinalizationSideEffect(
                projection.batch_id, projection.basis_digest, projection.outcome
            )
            self.audit_records = (effect,)
            self.semantic_outbox = (effect,)

    async def require_finalization_authority(self, *, batch_id: str) -> FinalizeBatchAuthority:
        self.authority_checks += 1
        if not self.authority_available:
            raise AuthorityProjectionUnavailable
        if not self.authority_allowed or batch_id != self.authority.batch_id:
            raise AuthorityPermissionDenied
        if not self.authority_current:
            raise AuthorityStateConflict(reason="batch_finalization_authority_superseded")
        return self.authority

    async def lookup_stored(
        self, *, identity_scope: BatchFinalizationIdentityScope
    ) -> BatchFinalizationProjection | None:
        self.stored_lookups += 1
        return self.projections.get(identity_scope)

    async def get_mutation_snapshot_for_update(
        self, *, batch_id: str
    ) -> BatchFinalizationMutationSnapshot:
        self.mutation_snapshot_reads += 1
        if batch_id != self.mutation_snapshot.batch_id:
            raise KeyError(batch_id)
        return self.mutation_snapshot

    async def publish_finalization(
        self, *, publication: BatchFinalizationPublication
    ) -> ReplayResult[BatchFinalizationProjection]:
        self.publication_attempts += 1
        self.last_publication = publication
        scope = publication.identity_scope
        stored_digest = self.basis_digests.get(scope)
        if stored_digest is not None:
            stored = self.projections[scope]
            if stored_digest != publication.basis.basis_digest:
                raise IdempotencyConflict(
                    scope=f"batch:{publication.basis.batch_id}:finalization",
                    key=f"{scope[0]}:{scope[2]}",
                    stored_digest=stored_digest,
                    received_digest=publication.basis.basis_digest,
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
        staged_digests = dict(self.basis_digests)
        staged_bases = dict(self.bases)
        staged_projections = dict(self.projections)
        staged_audit = self.audit_records
        staged_outbox = self.semantic_outbox
        self._raise_at("basis")
        staged_digests[scope] = publication.basis.basis_digest
        staged_bases[scope] = publication.basis
        self._raise_at("projection")
        staged_projections[scope] = publication.projection
        self._raise_at("audit")
        staged_audit += (publication.side_effect,)
        self._raise_at("outbox")
        staged_outbox += (publication.side_effect,)
        self._raise_at("commit")
        self.basis_digests = staged_digests
        self.bases = staged_bases
        self.projections = staged_projections
        self.audit_records = staged_audit
        self.semantic_outbox = staged_outbox
        self.publication_commits += 1
        return ReplayResult(publication.projection, False)

    def _raise_at(self, point: str) -> None:
        if self.fault_at == point:
            self.fault_at = None
            raise RuntimeError(f"batch_finalization_fault:{point}")

    @property
    def basis_count(self) -> int:
        return len(self.bases)

    @property
    def audit_count(self) -> int:
        return len(self.audit_records)

    @property
    def outbox_count(self) -> int:
        return len(self.semantic_outbox)
