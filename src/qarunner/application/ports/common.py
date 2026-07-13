"""Shared application-port values and stable contract errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ReplayResult[ReplayT]:
    """The first accepted value or a replay of that exact value."""

    value: ReplayT
    replayed: bool


class PortContractError(ValueError):
    """A deterministic application-port contract failure."""

    code = "application_port/contract_error"

    def __init__(self, *, resource: str, field: str, reason: str) -> None:
        self.resource = resource
        self.field = field
        self.reason = reason
        super().__init__(f"{resource} {field} is invalid: {reason}")


def ensure_utc(*, resource: str, field: str, value: object) -> datetime:
    """Return a UTC datetime or fail with a stable port error."""
    if not isinstance(value, datetime):
        raise PortContractError(resource=resource, field=field, reason="not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise PortContractError(resource=resource, field=field, reason="not_utc")
    return value
