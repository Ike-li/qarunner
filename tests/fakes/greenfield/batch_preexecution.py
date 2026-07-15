"""Deterministic Fake for Batch pre-execution application slices."""

from qarunner.application.ports.batch_preexecution import AuthorityProjectionUnavailable
from qarunner.domain.batch import Batch


class InMemoryBatchPreexecutionGateway:
    """Expose command ordering and durable state without simulating production I/O."""

    def __init__(self, *, batch: Batch, authority_available: bool = True) -> None:
        self.batch = batch
        self.authority_available = authority_available
        self.authority_checks = 0
        self.batch_reads = 0
        self.semantic_outbox: tuple[object, ...] = ()

    async def require_cancel_authority(self, *, project_id: str, actor_id: str) -> None:
        del project_id, actor_id
        self.authority_checks += 1
        if not self.authority_available:
            raise AuthorityProjectionUnavailable
