"""APScheduler integration for running automated test profiles on cron schedules."""

from __future__ import annotations

import logging
import zoneinfo
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from qarunner.models import RunRequest, TestSchedule

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


async def trigger_schedule_run(app: FastAPI, schedule_id: str) -> None:
    """Triggered by APScheduler when a schedule is fired."""
    container = app.state.container
    schedule = await container.store.get_schedule(schedule_id)
    if not schedule or not schedule.enabled:
        logger.warning("Schedule %s triggered but not found or disabled in database", schedule_id)
        return

    profile = await container.store.get_profile(schedule.profile_id)
    if not profile:
        logger.error(
            "Profile %s bound to schedule %s not found. Cannot run.",
            schedule.profile_id,
            schedule_id,
        )
        return

    # Leader election for multi-replica deployments (CONC-2): every replica runs
    # its own in-process scheduler and fires this job at the same cron tick. Claim
    # the tick in the DB so only the winning replica creates a run; the rest skip.
    try:
        from croniter import croniter
        tz = zoneinfo.ZoneInfo(schedule.timezone)
        fire_time = croniter(schedule.cron_expression, datetime.now(tz)).get_prev(datetime)
    except Exception:
        fire_time = datetime.now(UTC)
    if not await container.store.claim_schedule_run(schedule_id, fire_time):
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

    # Map profile to run request
    run_req = RunRequest(
        tests_path=profile.tests_path,
        runner="pytest",
        args=[],
        allure=True,
        timeout=profile.timeout,
        executor_mode=profile.executor_mode,
        selected_files=profile.selected_files,
        selected_markers=profile.selected_markers,
        extra_args=profile.extra_args,
        env=profile.env,
    )

    try:
        # Create and run
        await container.orchestrator.create(run_req, created_by="system:schedule")

        # Update schedule execution times
        now = datetime.now(UTC)
        schedule = schedule.model_copy(update={"last_run_at": now})

        # Calculate next run time
        try:
            from croniter import croniter
            tz = zoneinfo.ZoneInfo(schedule.timezone)
            now_tz = datetime.now(tz)
            iter = croniter(schedule.cron_expression, now_tz)
            schedule = schedule.model_copy(update={"next_run_at": iter.get_next(datetime)})
        except Exception:
            logger.exception("Failed to calculate next run time for schedule %s", schedule_id)

        await container.store.save_schedule(schedule)
    except Exception:
        logger.exception("Error executing scheduled run for schedule %s", schedule_id)


def add_or_update_schedule_job(app: FastAPI, schedule: TestSchedule) -> None:
    """Add or update a schedule job in APScheduler."""
    scheduler: AsyncIOScheduler | None = getattr(app.state, "scheduler", None)
    if not scheduler:
        return

    # If job already exists, remove it
    if scheduler.get_job(schedule.id):
        scheduler.remove_job(schedule.id)

    if not schedule.enabled:
        return

    try:
        trigger = CronTrigger.from_crontab(schedule.cron_expression, timezone=schedule.timezone)
        scheduler.add_job(
            trigger_schedule_run,
            trigger,
            args=[app, schedule.id],
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


def remove_schedule_job(app: FastAPI, schedule_id: str) -> None:
    """Remove a schedule job from APScheduler."""
    scheduler: AsyncIOScheduler | None = getattr(app.state, "scheduler", None)
    if scheduler and scheduler.get_job(schedule_id):
        scheduler.remove_job(schedule_id)
        logger.info("Removed schedule job: %s", schedule_id)


async def start_scheduler(app: FastAPI) -> None:
    """Initialise and start the APScheduler background runner."""
    container = app.state.container
    scheduler = AsyncIOScheduler()
    app.state.scheduler = scheduler

    # Load all schedules
    schedules = await container.store.list_schedules()
    for schedule in schedules:
        if schedule.enabled:
            # Update next_run_at statically at startup
            try:
                from croniter import croniter
                tz = zoneinfo.ZoneInfo(schedule.timezone)
                now_tz = datetime.now(tz)
                iter = croniter(schedule.cron_expression, now_tz)
                next_run = iter.get_next(datetime)
                schedule = schedule.model_copy(update={"next_run_at": next_run})
                await container.store.save_schedule(schedule)
            except Exception:
                pass
            add_or_update_schedule_job(app, schedule)

    scheduler.start()
    logger.info("APScheduler background scheduler started.")


async def shutdown_scheduler(app: FastAPI) -> None:
    """Shutdown the APScheduler background runner."""
    scheduler: AsyncIOScheduler | None = getattr(app.state, "scheduler", None)
    if scheduler:
        scheduler.shutdown()
        logger.info("APScheduler background scheduler shut down.")
