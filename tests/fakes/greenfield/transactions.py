"""Copy-on-write application transaction Fake."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from types import TracebackType

from qarunner.application.ports.audit import AuditLog, AuditRecord
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.evidence import EvidenceManifestIndex
from qarunner.application.ports.facts import (
    FactCommitResult,
    FactKey,
    FactT,
    VersionedFact,
    VersionedFactCommand,
    VersionedFactStore,
)
from qarunner.domain.evidence import EvidenceManifest
from tests.fakes.greenfield.audit import InMemoryAuditLog
from tests.fakes.greenfield.evidence import InMemoryEvidenceManifestIndex
from tests.fakes.greenfield.facts import InMemoryVersionedFactStore


class _ReadOnlyFactStore:
    def __init__(
        self,
        get_inner: Callable[[], InMemoryVersionedFactStore],
    ) -> None:
        self.__get_inner = get_inner

    async def get(self, key: FactKey) -> VersionedFact | None:
        return await self.__get_inner().get(key)

    async def commit(self, command: VersionedFactCommand[FactT]) -> FactCommitResult[FactT]:
        raise PortContractError(
            resource="application_state",
            field="facts",
            reason="transaction_required",
        )


class _ReadOnlyEvidenceManifestIndex:
    def __init__(
        self,
        get_inner: Callable[[], InMemoryEvidenceManifestIndex],
    ) -> None:
        self.__get_inner = get_inner

    async def get(self, attempt_id: str) -> EvidenceManifest | None:
        return await self.__get_inner().get(attempt_id)

    async def finalize(self, manifest: EvidenceManifest) -> ReplayResult[EvidenceManifest]:
        raise PortContractError(
            resource="application_state",
            field="evidence",
            reason="transaction_required",
        )


class _ReadOnlyAuditLog:
    def __init__(
        self,
        get_inner: Callable[[], InMemoryAuditLog],
    ) -> None:
        self.__get_inner = get_inner

    async def append(self, record: AuditRecord) -> ReplayResult[AuditRecord]:
        raise PortContractError(
            resource="application_state",
            field="audit",
            reason="transaction_required",
        )

    async def list_for_object(self, object_kind: str, object_id: str) -> tuple[AuditRecord, ...]:
        return await self.__get_inner().list_for_object(object_kind, object_id)


class _AbortOnFactFailure:
    def __init__(
        self,
        inner: InMemoryVersionedFactStore,
        ensure_active: Callable[[], None],
        abort: Callable[[], None],
    ) -> None:
        self.__inner = inner
        self.__ensure_active = ensure_active
        self.__abort = abort

    async def get(self, key: FactKey) -> VersionedFact | None:
        self.__ensure_active()
        return await self.__inner.get(key)

    async def commit(self, command: VersionedFactCommand[FactT]) -> FactCommitResult[FactT]:
        self.__ensure_active()
        try:
            return await self.__inner.commit(command)
        except Exception:
            self.__abort()
            raise


class _AbortOnEvidenceFailure:
    def __init__(
        self,
        inner: InMemoryEvidenceManifestIndex,
        ensure_active: Callable[[], None],
        abort: Callable[[], None],
    ) -> None:
        self.__inner = inner
        self.__ensure_active = ensure_active
        self.__abort = abort

    async def get(self, attempt_id: str) -> EvidenceManifest | None:
        self.__ensure_active()
        return await self.__inner.get(attempt_id)

    async def finalize(self, manifest: EvidenceManifest) -> ReplayResult[EvidenceManifest]:
        self.__ensure_active()
        try:
            return await self.__inner.finalize(manifest)
        except Exception:
            self.__abort()
            raise


class _AbortOnAuditFailure:
    def __init__(
        self,
        inner: InMemoryAuditLog,
        ensure_active: Callable[[], None],
        abort: Callable[[], None],
    ) -> None:
        self.__inner = inner
        self.__ensure_active = ensure_active
        self.__abort = abort

    async def append(self, record: AuditRecord) -> ReplayResult[AuditRecord]:
        self.__ensure_active()
        try:
            return await self.__inner.append(record)
        except Exception:
            self.__abort()
            raise

    async def list_for_object(self, object_kind: str, object_id: str) -> tuple[AuditRecord, ...]:
        self.__ensure_active()
        return await self.__inner.list_for_object(object_kind, object_id)


class InMemoryApplicationState:
    """Committed Fake adapters shared by successive application transactions."""

    def __init__(self) -> None:
        self.__facts = InMemoryVersionedFactStore()
        self.__evidence = InMemoryEvidenceManifestIndex()
        self.__audit = InMemoryAuditLog()
        self.__revision = 0
        self.__fact_view = _ReadOnlyFactStore(lambda: self.__facts)
        self.__evidence_view = _ReadOnlyEvidenceManifestIndex(lambda: self.__evidence)
        self.__audit_view = _ReadOnlyAuditLog(lambda: self.__audit)

    @property
    def facts(self) -> VersionedFactStore:
        return self.__fact_view

    @property
    def evidence(self) -> EvidenceManifestIndex:
        return self.__evidence_view

    @property
    def audit(self) -> AuditLog:
        return self.__audit_view

    def _fork(
        self,
    ) -> tuple[
        int,
        InMemoryVersionedFactStore,
        InMemoryEvidenceManifestIndex,
        InMemoryAuditLog,
    ]:
        facts, evidence, audit = deepcopy((self.__facts, self.__evidence, self.__audit))
        return self.__revision, facts, evidence, audit

    def _publish(
        self,
        expected_revision: int,
        facts: InMemoryVersionedFactStore,
        evidence: InMemoryEvidenceManifestIndex,
        audit: InMemoryAuditLog,
    ) -> None:
        if expected_revision != self.__revision:
            raise PortContractError(
                resource="unit_of_work",
                field="state",
                reason="stale_snapshot",
            )
        self.__facts = facts
        self.__evidence = evidence
        self.__audit = audit
        self.__revision += 1


class InMemoryApplicationUnitOfWork:
    """Stage writes on private adapters and publish them in one operation."""

    def __init__(
        self,
        state: InMemoryApplicationState,
        *,
        commit_error: Exception | None = None,
    ) -> None:
        self.__state = state
        self.__commit_error = commit_error
        self.__base_revision: int | None = None
        self.__fact_adapter: InMemoryVersionedFactStore | None = None
        self.__evidence_adapter: InMemoryEvidenceManifestIndex | None = None
        self.__audit_adapter: InMemoryAuditLog | None = None
        self.__facts: VersionedFactStore | None = None
        self.__evidence: EvidenceManifestIndex | None = None
        self.__audit: AuditLog | None = None
        self.__committed = False
        self.__aborted = False
        self.__closed = False

    @property
    def facts(self) -> VersionedFactStore:
        self.__ensure_participants_open()
        assert self.__facts is not None
        return self.__facts

    @property
    def evidence(self) -> EvidenceManifestIndex:
        self.__ensure_participants_open()
        assert self.__evidence is not None
        return self.__evidence

    @property
    def audit(self) -> AuditLog:
        self.__ensure_participants_open()
        assert self.__audit is not None
        return self.__audit

    async def __aenter__(self) -> InMemoryApplicationUnitOfWork:
        if self.__closed:
            raise PortContractError(
                resource="unit_of_work",
                field="state",
                reason="closed",
            )
        if self.__facts is not None:
            raise PortContractError(
                resource="unit_of_work",
                field="state",
                reason="already_active",
            )
        (
            self.__base_revision,
            self.__fact_adapter,
            self.__evidence_adapter,
            self.__audit_adapter,
        ) = self.__state._fork()
        self.__facts = _AbortOnFactFailure(
            self.__fact_adapter,
            self.__ensure_participants_open,
            self.__abort,
        )
        self.__evidence = _AbortOnEvidenceFailure(
            self.__evidence_adapter,
            self.__ensure_participants_open,
            self.__abort,
        )
        self.__audit = _AbortOnAuditFailure(
            self.__audit_adapter,
            self.__ensure_participants_open,
            self.__abort,
        )
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.__committed:
            await self.rollback()

    async def commit(self) -> None:
        self.__ensure_participants_open()
        assert self.__fact_adapter is not None
        assert self.__evidence_adapter is not None
        assert self.__audit_adapter is not None
        assert self.__base_revision is not None
        if self.__aborted:
            raise PortContractError(
                resource="unit_of_work",
                field="state",
                reason="aborted",
            )
        if self.__commit_error is not None:
            error = self.__commit_error
            self.__commit_error = None
            self.__abort()
            raise error
        try:
            self.__state._publish(
                self.__base_revision,
                self.__fact_adapter,
                self.__evidence_adapter,
                self.__audit_adapter,
            )
        except Exception:
            self.__abort()
            raise
        self.__committed = True
        self.__close()

    async def rollback(self) -> None:
        self.__close()

    def __close(self) -> None:
        self.__base_revision = None
        self.__fact_adapter = None
        self.__evidence_adapter = None
        self.__audit_adapter = None
        self.__facts = None
        self.__evidence = None
        self.__audit = None
        self.__closed = True

    def __abort(self) -> None:
        self.__aborted = True

    def __ensure_participants_open(self) -> None:
        if self.__closed:
            raise PortContractError(
                resource="unit_of_work",
                field="state",
                reason="closed",
            )
        if self.__aborted:
            raise PortContractError(
                resource="unit_of_work",
                field="state",
                reason="aborted",
            )
        if self.__facts is None or self.__evidence is None or self.__audit is None:
            raise PortContractError(
                resource="unit_of_work",
                field="state",
                reason="not_active",
            )
