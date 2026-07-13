"""Append-only application audit port."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import (
    PortContractError,
    ReplayResult,
    ensure_utc,
)
from qarunner.domain.digest import Digest

_AUDIT_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One immutable security or governance decision record."""

    event_id: str
    actor_id: str
    authentication_strength: str
    request_id: str
    object_kind: str
    object_id: str
    action: str
    decision: str
    reason: str
    before_digest: Digest | None
    after_digest: Digest | None
    occurred_at: datetime
    source: str

    def __post_init__(self) -> None:
        identity_fields = (
            ("event_id", self.event_id),
            ("actor_id", self.actor_id),
            ("request_id", self.request_id),
            ("object_id", self.object_id),
        )
        code_fields = (
            ("authentication_strength", self.authentication_strength),
            ("object_kind", self.object_kind),
            ("action", self.action),
            ("decision", self.decision),
            ("reason", self.reason),
            ("source", self.source),
        )
        for field, value in (*identity_fields, *code_fields):
            if not isinstance(value, str):
                raise PortContractError(
                    resource="audit_record",
                    field=field,
                    reason="not_string",
                )
            if not value.strip():
                raise PortContractError(
                    resource="audit_record",
                    field=field,
                    reason="empty",
                )
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise PortContractError(
                    resource="audit_record",
                    field=field,
                    reason="contains_control_character",
                )
        for field, value in code_fields:
            if _AUDIT_CODE.fullmatch(value) is None:
                raise PortContractError(
                    resource="audit_record",
                    field=field,
                    reason="not_code",
                )
        ensure_utc(
            resource="audit_record",
            field="occurred_at",
            value=self.occurred_at,
        )
        for field, value in (
            ("before_digest", self.before_digest),
            ("after_digest", self.after_digest),
        ):
            if value is not None and not isinstance(value, Digest):
                raise PortContractError(
                    resource="audit_record",
                    field=field,
                    reason="not_digest",
                )


@runtime_checkable
class AuditLog(Protocol):
    """Append audit facts and query immutable object-scoped history."""

    async def append(self, record: AuditRecord) -> ReplayResult[AuditRecord]: ...

    async def list_for_object(
        self, object_kind: str, object_id: str
    ) -> tuple[AuditRecord, ...]: ...
