"""Atomic transaction boundary for related application facts."""

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from qarunner.application.ports.audit import AuditLog
from qarunner.application.ports.evidence import EvidenceManifestIndex
from qarunner.application.ports.facts import VersionedFactStore


@runtime_checkable
class ApplicationUnitOfWork(Protocol):
    """Publish related Fact, Evidence index, and Audit writes atomically."""

    @property
    def facts(self) -> VersionedFactStore: ...

    @property
    def evidence(self) -> EvidenceManifestIndex: ...

    @property
    def audit(self) -> AuditLog: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
