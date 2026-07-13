"""Deterministic append-only Audit Fake."""

from qarunner.application.ports.audit import AuditRecord
from qarunner.application.ports.common import PortContractError, ReplayResult


class InMemoryAuditLog:
    """Keep immutable audit facts in deterministic append order."""

    def __init__(self) -> None:
        self.__records: list[AuditRecord] = []

    async def append(self, record: AuditRecord) -> ReplayResult[AuditRecord]:
        for stored_record in self.__records:
            if stored_record == record:
                return ReplayResult(value=stored_record, replayed=True)
            if stored_record.event_id == record.event_id:
                raise PortContractError(
                    resource="audit_log",
                    field="event_id",
                    reason="conflicting_content",
                )
        self.__records.append(record)
        return ReplayResult(value=record, replayed=False)

    async def list_for_object(self, object_kind: str, object_id: str) -> tuple[AuditRecord, ...]:
        return tuple(
            record
            for record in self.__records
            if record.object_kind == object_kind and record.object_id == object_id
        )
