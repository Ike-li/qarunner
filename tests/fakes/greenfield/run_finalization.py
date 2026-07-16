"""Deterministic copy-on-write Fake for the Run-finalization gateway."""

from __future__ import annotations

from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
)
from qarunner.application.ports.common import ReplayResult
from qarunner.application.ports.run_finalization import (
    FinalizeRunAuthority,
    RunFinalizationIdentityScope,
    RunFinalizationMutationSnapshot,
    RunFinalizationProjection,
    RunFinalizationPublication,
    RunFinalizationSideEffect,
)
from qarunner.domain.digest import Digest
from qarunner.domain.errors import IdempotencyConflict, VersionConflict
from qarunner.domain.run_finalization import RunFinalizationBasis, RunItemResolutionSet


class InMemoryRunFinalizationGateway:
    """Expose ordering and atomic publication without simulating external I/O."""

    def __init__(
        self,
        *,
        authority: FinalizeRunAuthority,
        mutation_snapshot: RunFinalizationMutationSnapshot,
        projection: RunFinalizationProjection | None = None,
        authority_available: bool = True,
        authority_allowed: bool = True,
        authority_current: bool = True,
        publication_error: Exception | None = None,
        fault_at: str | None = None,
    ) -> None:
        self.authority = authority
        self.mutation_snapshot = mutation_snapshot
        self.projection = projection
        self.authority_available = authority_available
        self.authority_allowed = authority_allowed
        self.authority_current = authority_current
        self.publication_error = publication_error
        self.fault_at = fault_at

        self.authority_checks = 0
        self.projection_reads = 0
        self.stored_lookups = 0
        self.mutation_snapshot_reads = 0
        self.publication_attempts = 0
        self.publication_commits = 0
        self.audit_records: tuple[RunFinalizationSideEffect, ...] = ()
        self.semantic_outbox: tuple[RunFinalizationSideEffect, ...] = ()
        self.run_closed_handoffs: dict[str, object] = {}
        self.basis_digests: dict[RunFinalizationIdentityScope, Digest] = {}
        self.bases: dict[RunFinalizationIdentityScope, RunFinalizationBasis] = {}
        self.resolution_sets: dict[RunFinalizationIdentityScope, RunItemResolutionSet] = {}
        self.projections: dict[RunFinalizationIdentityScope, RunFinalizationProjection] = {}
        if projection is not None:
            scope = (
                "qep.run-finalization-basis.v1",
                projection.run_id,
                projection.source_run_version,
            )
            self.basis_digests[scope] = projection.basis_digest
            self.projections[scope] = projection

    async def require_finalization_authority(self, *, run_id: str) -> FinalizeRunAuthority:
        self.authority_checks += 1
        if not self.authority_available:
            raise AuthorityProjectionUnavailable
        if not self.authority_allowed or run_id != self.authority.run_id:
            raise AuthorityPermissionDenied
        if not self.authority_current:
            raise AuthorityStateConflict(reason="run_finalization_authority_superseded")
        return self.authority

    async def lookup_stored(
        self, *, identity_scope: RunFinalizationIdentityScope
    ) -> RunFinalizationProjection | None:
        self.stored_lookups += 1
        return self.projections.get(identity_scope)

    async def get_mutation_snapshot_for_update(
        self, *, run_id: str
    ) -> RunFinalizationMutationSnapshot:
        self.mutation_snapshot_reads += 1
        if run_id != self.mutation_snapshot.run_id:
            raise KeyError(run_id)
        return self.mutation_snapshot

    async def publish_finalization(
        self, *, publication: RunFinalizationPublication
    ) -> ReplayResult[RunFinalizationProjection]:
        self.publication_attempts += 1
        scope = publication.identity_scope
        stored_digest = self.basis_digests.get(scope)
        if stored_digest is not None:
            stored = self.projections[scope]
            if stored_digest != publication.basis.basis_digest:
                raise IdempotencyConflict(
                    scope=f"run:{publication.projection.run_id}:finalization",
                    key=f"{scope[0]}:{scope[2]}",
                    stored_digest=stored_digest,
                    received_digest=publication.basis.basis_digest,
                )
            return ReplayResult(value=stored, replayed=True)

        if publication.authority != self.authority or not self.authority_current:
            raise AuthorityStateConflict(reason="publication_authority_superseded")
        if publication.expected_snapshot != self.mutation_snapshot:
            raise VersionConflict(
                entity_type="run",
                entity_id=self.mutation_snapshot.run_id,
                current_version=self.mutation_snapshot.run_version,
                expected_version=publication.expected_snapshot.run_version,
            )
        if self.publication_error is not None:
            error = self.publication_error
            self.publication_error = None
            raise error

        # Stage every participant privately. No committed field changes until all
        # deterministic fault points have been crossed.
        staged_basis = dict(self.basis_digests)
        staged_basis_values = dict(self.bases)
        staged_resolution_sets = dict(self.resolution_sets)
        staged_projections = dict(self.projections)
        staged_audit = self.audit_records
        staged_outbox = self.semantic_outbox
        staged_handoffs = dict(self.run_closed_handoffs)
        self._raise_at("basis")
        staged_basis[scope] = publication.basis.basis_digest
        staged_basis_values[scope] = publication.basis
        self._raise_at("resolution_set")
        staged_resolution_sets[scope] = publication.resolution_set
        self._raise_at("projection")
        staged_projections[scope] = publication.projection
        self._raise_at("handoff")
        staged_handoffs[publication.handoff.event_id] = publication.handoff
        self._raise_at("audit")
        staged_audit += (publication.side_effect,)
        self._raise_at("outbox")
        staged_outbox += (publication.side_effect,)
        self._raise_at("commit")

        self.basis_digests = staged_basis
        self.bases = staged_basis_values
        self.resolution_sets = staged_resolution_sets
        self.projections = staged_projections
        self.audit_records = staged_audit
        self.semantic_outbox = staged_outbox
        self.run_closed_handoffs = staged_handoffs
        self.projection = publication.projection
        self.publication_commits += 1
        return ReplayResult(value=publication.projection, replayed=False)

    @property
    def audit_count(self) -> int:
        return len(self.audit_records)

    @property
    def outbox_count(self) -> int:
        return len(self.semantic_outbox)

    def _raise_at(self, point: str) -> None:
        if self.fault_at == point:
            self.fault_at = None
            raise RuntimeError(f"run_finalization_fault:{point}")
