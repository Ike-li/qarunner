"""APScheduler-backed implementation of the cron SchedulePort."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from qarunner.core import cron
from qarunner.models import RunRequest, UserRole

if TYPE_CHECKING:
    from qarunner.core.orchestrator import RunOrchestrator
    from qarunner.models import TestSchedule
    from qarunner.ports.store import Store

logger = logging.getLogger(__name__)


class ApschedulerSchedulePort:
    """Registers test schedules on an in-process APScheduler engine.

    Holds the injected store/orchestrator so the fired job can resolve the
    schedule and create the run without reaching into web-framework state. The
    store is expected to also provide schedule/profile persistence (as
    ``SqliteStore`` does); those methods are used by the trigger callback.
    """

    def __init__(self, store: Store, orchestrator: RunOrchestrator) -> None:
        self._store = store
        self._orchestrator = orchestrator
        self._scheduler: AsyncIOScheduler | None = None

    async def _trigger(self, schedule_id: str) -> None:
        """Fired by APScheduler when a schedule's cron expression elapses."""
        schedule = await self._store.get_schedule(schedule_id)
        if not schedule or not schedule.enabled:
            logger.warning(
                "Schedule %s triggered but not found or disabled in database", schedule_id
            )
            return

        profile = await self._store.get_profile(schedule.profile_id)
        if not profile:
            logger.error(
                "Profile %s bound to schedule %s not found. Cannot run.",
                schedule.profile_id,
                schedule_id,
            )
            return

        if profile.created_by != schedule.created_by:
            creator = await self._store.get_user(schedule.created_by)
            creator_role = str(creator.get("role", "") if creator else "")
            if creator_role != UserRole.ADMIN.value:
                logger.error(
                    "Profile %s bound to schedule %s is owned by %s, not schedule creator %s. "
                    "Skipping.",
                    schedule.profile_id,
                    schedule_id,
                    profile.created_by,
                    schedule.created_by,
                )
                return

        # Leader election for multi-replica deployments (CONC-2): every replica runs
        # its own in-process scheduler and fires this job at the same cron tick. Claim
        # the tick in the DB so only the winning replica creates a run; the rest skip.
        try:
            fire_time = cron.previous_run(schedule.cron_expression, schedule.timezone)
        except (KeyError, ValueError):
            logger.warning(
                "Failed to compute fire time for schedule %s; using now()",
                schedule_id,
                exc_info=True,
            )
            fire_time = datetime.now(UTC)
        if not await self._store.claim_schedule_run(schedule_id, fire_time):
            logger.info(
                "Schedule %s already claimed for this tick by another replica; skipping",
                schedule_id,
            )
            return

        logger.info(
            "Executing scheduled test run for schedule '%s' (profile: '%s')",
            schedule.name,
            profile.name,
        )

        # Map profile to run request (shared with the manual trigger, P2-6).
        run_req = RunRequest.from_profile(profile)

        try:
            # Create and run
            await self._orchestrator.create(
                run_req, created_by="system:schedule", profile_id=schedule.profile_id
            )

            # BUG-6: orchestrator.create() awaits, so a PUT/DELETE on this
            # schedule may land before we get here. A full-row save_schedule()
            # using the snapshot read at the top of this method would clobber
            # those changes — or resurrect a concurrently deleted row, since a
            # full-row UPSERT has nothing to conflict with once it's gone.
            # Persist only next_run_at, narrowly, the same way
            # claim_schedule_run narrowly owns last_run_at.
            try:
                next_at = cron.next_run(schedule.cron_expression, schedule.timezone)
            except Exception:
                logger.exception("Failed to calculate next run time for schedule %s", schedule_id)
            else:
                await self._store.update_schedule_next_run(schedule_id, next_at)
        except Exception:
            logger.exception("Error executing scheduled run for schedule %s", schedule_id)

    def upsert(self, schedule: TestSchedule) -> None:
        """Add or update a schedule job in APScheduler."""
        scheduler = self._scheduler
        if not scheduler:
            return

        # If job already exists, remove it
        if scheduler.get_job(schedule.id):
            scheduler.remove_job(schedule.id)

        if not schedule.enabled:
            return

        try:
            trigger = CronTrigger.from_crontab(
                schedule.cron_expression, timezone=schedule.timezone
            )
            scheduler.add_job(
                self._trigger,
                trigger,
                args=[schedule.id],
                id=schedule.id,
                replace_existing=True,
            )
            logger.info(
                "Registered schedule job: %s with expression '%s' [%s]",
                schedule.id,
                schedule.cron_expression,
                schedule.timezone,
            )
        except Exception:
            logger.exception("Failed to add schedule job %s to scheduler", schedule.id)

    def remove(self, schedule_id: str) -> None:
        """Remove a schedule job from APScheduler."""
        scheduler = self._scheduler
        if scheduler and scheduler.get_job(schedule_id):
            scheduler.remove_job(schedule_id)
            logger.info("Removed schedule job: %s", schedule_id)

    async def start(self) -> None:
        """Initialise and start the APScheduler background runner."""
        scheduler = AsyncIOScheduler()
        self._scheduler = scheduler

        # Load all schedules
        schedules = await self._store.list_schedules()
        for schedule in schedules:
            if schedule.enabled:
                # Update next_run_at statically at startup
                try:
                    next_at = cron.next_run(schedule.cron_expression, schedule.timezone)
                    schedule = schedule.model_copy(update={"next_run_at": next_at})
                    await self._store.save_schedule(schedule)
                except (KeyError, ValueError):
                    logger.warning(
                        "Failed to compute startup next-run for schedule %s",
                        schedule.id,
                        exc_info=True,
                    )
                self.upsert(schedule)

        scheduler.start()
        logger.info("APScheduler background scheduler started.")

    async def shutdown(self) -> None:
        """Shutdown the APScheduler background runner."""
        scheduler = self._scheduler
        if scheduler:
            scheduler.shutdown()
            logger.info("APScheduler background scheduler shut down.")
