"""Application service for the test-schedule lifecycle."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from qarunner.core import cron
from qarunner.errors import InvalidScheduleRequest, ScheduleNotFound
from qarunner.models import TestSchedule

if TYPE_CHECKING:
    from qarunner.api.schemas import TestScheduleCreateRequest, TestScheduleUpdateRequest
    from qarunner.ports.schedule import SchedulePort
    from qarunner.ports.store import RunStore

logger = logging.getLogger(__name__)


class ScheduleService:
    """Validates, persists and registers test schedules.

    Owns the store + scheduler coupling so the HTTP layer stays a thin
    translate-call-return shell; raises domain errors (mapped to HTTP by the
    route) rather than HTTPException.
    """

    def __init__(self, store: RunStore, scheduler: SchedulePort) -> None:
        self._store = store
        self._scheduler = scheduler

    async def _validate(self, profile_id: str, timezone: str, cron_expression: str) -> None:
        if not await self._store.get_profile(profile_id):
            raise InvalidScheduleRequest(f"Profile {profile_id} not found")
        if not cron.is_valid_timezone(timezone):
            raise InvalidScheduleRequest(f"Invalid timezone: {timezone}")
        if not cron.is_valid_cron(cron_expression):
            raise InvalidScheduleRequest("Invalid cron expression")

    @staticmethod
    def _next_run_at(enabled: bool, cron_expression: str, timezone: str) -> datetime | None:
        """Static next-run preview; tolerates a computation failure as None."""
        if not enabled:
            return None
        try:
            return cron.next_run(cron_expression, timezone)
        except (KeyError, ValueError):
            logger.warning(
                "Failed to compute next run for cron %r tz %r",
                cron_expression,
                timezone,
                exc_info=True,
            )
            return None

    async def create(self, req: TestScheduleCreateRequest, created_by: str) -> TestSchedule:
        await self._validate(req.profile_id, req.timezone, req.cron_expression)
        schedule = TestSchedule(
            id=str(uuid4()),
            name=req.name,
            profile_id=req.profile_id,
            cron_expression=req.cron_expression,
            enabled=req.enabled,
            timezone=req.timezone,
            last_run_at=None,
            next_run_at=self._next_run_at(req.enabled, req.cron_expression, req.timezone),
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        await self._store.save_schedule(schedule)
        self._scheduler.upsert(schedule)
        return schedule

    async def update(self, schedule_id: str, req: TestScheduleUpdateRequest) -> TestSchedule:
        existing = await self._store.get_schedule(schedule_id)
        if not existing:
            raise ScheduleNotFound(schedule_id)
        await self._validate(req.profile_id, req.timezone, req.cron_expression)
        updated = TestSchedule(
            id=existing.id,
            name=req.name,
            profile_id=req.profile_id,
            cron_expression=req.cron_expression,
            enabled=req.enabled,
            timezone=req.timezone,
            last_run_at=existing.last_run_at,
            next_run_at=self._next_run_at(req.enabled, req.cron_expression, req.timezone),
            created_by=existing.created_by,
            created_at=existing.created_at,
        )
        await self._store.save_schedule(updated)
        if updated.enabled:
            self._scheduler.upsert(updated)
        else:
            self._scheduler.remove(updated.id)
        return updated

    async def delete(self, schedule_id: str) -> None:
        if not await self._store.delete_schedule(schedule_id):
            raise ScheduleNotFound(schedule_id)
        self._scheduler.remove(schedule_id)
