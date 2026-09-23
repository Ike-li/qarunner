"""Deterministic schedule fire identity (M6, T-M6-SCHEDULE-001).

Each schedule fire has a deterministic identity (schedule_id + fire_time → digest).
Misfire, pause/restore, restart don't produce duplicate Batch because the fire
digest is the idempotency_key for Batch creation. Pure domain — no adapters.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError


class ScheduleStatus(enum.StrEnum):
    """Lifecycle states for a schedule."""

    ENABLED = "enabled"
    PAUSED = "paused"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ScheduleRecord:
    """Canonical schedule vocabulary (DES §11.2)."""

    schedule_id: str
    name: str
    profile_id: str
    cron_expr: str
    timezone: str
    status: ScheduleStatus
    next_fire_at: datetime | None
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        entity = "schedule_record"
        for field in ("schedule_id", "name", "profile_id", "cron_expr", "timezone", "created_by"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(entity_type=entity, field=field, reason="invalid")
        if not isinstance(self.status, ScheduleStatus):
            raise DomainValidationError(entity_type=entity, field="status", reason="invalid")
        if self.next_fire_at is not None:
            _require_utc(entity, "next_fire_at", self.next_fire_at)
        _require_utc(entity, "created_at", self.created_at)

    @property
    def schedule_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.schedule-record.v1",
            payload={
                "schedule_id": self.schedule_id,
                "name": self.name,
                "profile_id": self.profile_id,
                "cron_expr": self.cron_expr,
                "timezone": self.timezone,
                "status": self.status.value,
                "created_by": self.created_by,
            },
        )


@dataclass(frozen=True, slots=True)
class ScheduleFireIdentity:
    """Deterministic identity for one schedule fire (DES §11.2)."""

    schedule_id: str
    fire_at: datetime
    fire_digest: Digest

    def __post_init__(self) -> None:
        entity = "schedule_fire_identity"
        if not isinstance(self.schedule_id, str) or not self.schedule_id.strip():
            raise DomainValidationError(entity_type=entity, field="schedule_id", reason="invalid")
        _require_utc(entity, "fire_at", self.fire_at)
        if not isinstance(self.fire_digest, Digest):
            raise DomainValidationError(entity_type=entity, field="fire_digest", reason="invalid")


def compute_next_fire_at(
    *,
    cron_expr: str,
    timezone: str,
    after: datetime,
    enabled: bool = True,
) -> datetime | None:
    """Compute next fire time from cron expression.

    Returns None when disabled. Rejects invalid cron or timezone.
    """
    if not isinstance(cron_expr, str) or not cron_expr.strip():
        raise DomainValidationError(entity_type="schedule", field="cron_expr", reason="invalid")
    if not isinstance(timezone, str) or not timezone.strip():
        raise DomainValidationError(entity_type="schedule", field="timezone", reason="invalid")
    _require_utc("schedule", "after", after)
    if not enabled:
        return None
    try:
        from croniter import croniter
    except ImportError:  # pragma: no cover — croniter is a declared dependency
        raise DomainValidationError(
            entity_type="schedule", field="cron_expr", reason="croniter_not_installed"
        ) from None
    if not croniter.is_valid(cron_expr):
        raise DomainValidationError(entity_type="schedule", field="cron_expr", reason="invalid")
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(timezone)
    except (KeyError, Exception):
        raise DomainValidationError(
            entity_type="schedule", field="timezone", reason="invalid"
        ) from None
    local_after = after.astimezone(tz)
    it = croniter(cron_expr, local_after)
    next_local = it.get_next(datetime)
    # Convert back to UTC for storage.
    return next_local.astimezone(ZoneInfo("UTC"))


def compute_schedule_fire_identity(
    *,
    schedule_id: str,
    fire_at: datetime,
) -> ScheduleFireIdentity:
    """Deterministic fire identity: schedule_id + fire_at → digest.

    This is the idempotency_key for Batch creation: same fire time always
    produces the same digest, so misfire/restore/restart cannot create
    duplicate Batch for the same fire.
    """
    if not isinstance(schedule_id, str) or not schedule_id.strip():
        raise DomainValidationError(
            entity_type="schedule_fire_identity", field="schedule_id", reason="invalid"
        )
    _require_utc("schedule_fire_identity", "fire_at", fire_at)
    fire_digest = canonical_digest(
        schema_version="qep.schedule-fire.v1",
        payload={
            "schedule_id": schedule_id,
            "fire_at": fire_at.isoformat(),
        },
    )
    return ScheduleFireIdentity(
        schedule_id=schedule_id,
        fire_at=fire_at,
        fire_digest=fire_digest,
    )


def _require_utc(entity: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise DomainValidationError(entity_type=entity, field=field, reason="not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DomainValidationError(entity_type=entity, field=field, reason="not_utc")
