"""Ports for Batch-owned pre-execution commands."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from qarunner.application.handoff import BatchMaterializedScopeHandoff
from qarunner.application.ports.common import ReplayResult
from qarunner.domain.batch import Batch, BatchRejection, BatchRejectionStage
from qarunner.domain.cancellation import (
    BatchCancellationIntent,
    BatchCancellationScope,
    CancellationSource,
)
from qarunner.domain.digest import Digest


class AuthorityProjectionUnavailable(RuntimeError):
    """Current cancellation authority cannot be established safely."""


class AuthorityPermissionDenied(RuntimeError):
    """The current caller has no permission on the requested object."""


class InternalAuthorityRetired(RuntimeError):
    """An internal reconciler or phase-owner capability is no longer current."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class AuthorityStateConflict(RuntimeError):
    """The requested phase/reconciler authority is no longer current."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class AuthorityProjectionStamp:
    """Current local projection position; maximum freshness window remains policy-owned."""

    source: str
    projection_version: int
    revocation_watermark: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BatchCancellationAuthority:
    """Server-derived facts required to materialize a cancellation intent."""

    batch_id: str
    project_id: str
    suite_revision_id: str
    actor_id: str
    source: CancellationSource
    authorization_digest: Digest
    scope: BatchCancellationScope
    projection: AuthorityProjectionStamp
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class BatchCancellationSideEffect:
    """Minimal identity shared by transactional audit and semantic delivery."""

    batch_id: str
    intent_digest: Digest


@dataclass(frozen=True, slots=True)
class BatchClosureSideEffect:
    batch_id: str
    basis_digest: Digest


@dataclass(frozen=True, slots=True)
class BatchRejectionSideEffect:
    batch_id: str
    rejection_digest: Digest
    basis_digest: Digest


@dataclass(frozen=True, slots=True)
class BatchClosureAuthority:
    project_id: str
    suite_revision_id: str
    source_batch_version: int
    authority_digest: Digest
    scope: BatchCancellationScope
    projection: AuthorityProjectionStamp
    write_epoch: int


@dataclass(frozen=True, slots=True)
class BatchRejectionAuthority:
    """Server-derived phase ownership and scope for a rejection command."""

    batch_id: str
    project_id: str
    suite_revision_id: str
    source_batch_version: int
    stage: BatchRejectionStage
    authority_digest: Digest
    scope: BatchCancellationScope
    projection: AuthorityProjectionStamp
    recorded_at: datetime
    write_epoch: int


@runtime_checkable
class BatchPreexecutionGateway(Protocol):
    """Narrow authority and persistence boundary for pre-execution Batch work."""

    async def require_cancel_authority(self, *, batch_id: str) -> BatchCancellationAuthority:
        """Require live authority without exposing any stored command identity."""

    async def get_batch_for_update(self, *, batch_id: str) -> Batch:
        """Return the authoritative Batch snapshot inside the command boundary."""

    async def publish_cancellation(self, *, batch: Batch, intent: BatchCancellationIntent) -> None:
        """Atomically publish Batch, audit, and semantic outbox facts."""

    async def require_closure_authority(
        self,
        *,
        batch_id: str,
        reconciler_id: str,
        closure_epoch: int,
        project_id: str,
        suite_revision_id: str,
        source_batch_version: int,
    ) -> BatchClosureAuthority: ...

    async def require_rejection_authority(
        self,
        *,
        batch_id: str,
        phase_owner_id: str,
        rejection_epoch: int,
    ) -> BatchRejectionAuthority: ...

    async def publish_materialized_handoff(
        self, *, handoff: BatchMaterializedScopeHandoff
    ) -> ReplayResult[BatchMaterializedScopeHandoff]: ...

    async def publish_preexecution_closure(self, *, batch: Batch) -> None: ...

    async def publish_preexecution_rejection(
        self, *, batch: Batch, rejection: BatchRejection
    ) -> None: ...
