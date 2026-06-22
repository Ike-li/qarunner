"""Tests for qarunner.core.scheduler (APScheduler integration)."""

from __future__ import annotations

import asyncio
from datetime import datetime, UTC
import zoneinfo
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from qarunner.core.scheduler import (
    trigger_schedule_run,
    add_or_update_schedule_job,
    remove_schedule_job,
    start_scheduler,
    shutdown_scheduler,
)
from qarunner.models import RunRequest, TestProfile, TestSchedule


@pytest.fixture
def mock_app() -> FastAPI:
    app = FastAPI()
    app.state.container = MagicMock()
    app.state.scheduler = MagicMock()
    return app


@pytest.mark.asyncio
async def test_trigger_schedule_run_success(mock_app: FastAPI) -> None:
    container = mock_app.state.container
    
    # Setup schedule and profile
    schedule = TestSchedule(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    profile = TestProfile(
        id="prof-1",
        name="Test Profile",
        tests_path="sample",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    
    container.store.get_schedule = AsyncMock(return_value=schedule)
    container.store.get_profile = AsyncMock(return_value=profile)
    container.store.save_schedule = AsyncMock()
    container.orchestrator.create = AsyncMock()
    
    await trigger_schedule_run(mock_app, "sched-1")
    
    container.store.get_schedule.assert_called_once_with("sched-1")
    container.store.get_profile.assert_called_once_with("prof-1")
    
    # Verify orchestrator was called with a matching RunRequest
    container.orchestrator.create.assert_called_once()
    run_req: RunRequest = container.orchestrator.create.call_args[0][0]
    assert run_req.tests_path == "sample"
    assert container.orchestrator.create.call_args[1]["created_by"] == "system:schedule"
    
    # Verify schedule was updated and saved
    container.store.save_schedule.assert_called_once()
    saved_schedule: TestSchedule = container.store.save_schedule.call_args[0][0]
    assert saved_schedule.last_run_at is not None
    assert saved_schedule.next_run_at is not None


@pytest.mark.asyncio
async def test_trigger_schedule_run_passes_profile_env(mock_app: FastAPI) -> None:
    """FUNC-1: a profile's env is forwarded into the scheduled RunRequest."""
    container = mock_app.state.container
    schedule = TestSchedule(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    profile = TestProfile(
        id="prof-1",
        name="Test Profile",
        tests_path="sample",
        created_by="user",
        created_at=datetime.now(UTC),
        env={"PROFILE_VAR": "from-profile"},
    )
    container.store.get_schedule = AsyncMock(return_value=schedule)
    container.store.get_profile = AsyncMock(return_value=profile)
    container.store.save_schedule = AsyncMock()
    container.orchestrator.create = AsyncMock()

    await trigger_schedule_run(mock_app, "sched-1")

    run_req: RunRequest = container.orchestrator.create.call_args[0][0]
    assert run_req.env == {"PROFILE_VAR": "from-profile"}


@pytest.mark.asyncio
async def test_trigger_schedule_run_missing_or_disabled(mock_app: FastAPI) -> None:
    container = mock_app.state.container
    
    # 1. Missing schedule
    container.store.get_schedule = AsyncMock(return_value=None)
    await trigger_schedule_run(mock_app, "sched-missing")
    container.orchestrator.create.assert_not_called()
    
    # 2. Disabled schedule
    disabled_sched = TestSchedule(
        id="sched-disabled",
        name="Disabled Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=False,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    container.store.get_schedule = AsyncMock(return_value=disabled_sched)
    await trigger_schedule_run(mock_app, "sched-disabled")
    container.orchestrator.create.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_schedule_run_missing_profile(mock_app: FastAPI) -> None:
    container = mock_app.state.container
    schedule = TestSchedule(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-missing",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    container.store.get_schedule = AsyncMock(return_value=schedule)
    container.store.get_profile = AsyncMock(return_value=None)
    
    await trigger_schedule_run(mock_app, "sched-1")
    container.orchestrator.create.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_schedule_run_exceptions(mock_app: FastAPI) -> None:
    container = mock_app.state.container
    schedule = TestSchedule(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    profile = TestProfile(
        id="prof-1",
        name="Test Profile",
        tests_path="sample",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    
    container.store.get_schedule = AsyncMock(return_value=schedule)
    container.store.get_profile = AsyncMock(return_value=profile)
    container.orchestrator.create = AsyncMock(side_effect=Exception("Execution failed"))
    
    # Verify that the exception inside orchestrator is handled and doesn't crash the trigger
    await trigger_schedule_run(mock_app, "sched-1")
    container.orchestrator.create.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_schedule_run_next_run_calc_failure(mock_app: FastAPI) -> None:
    container = mock_app.state.container
    schedule = TestSchedule(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    profile = TestProfile(
        id="prof-1",
        name="Test Profile",
        tests_path="sample",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    
    container.store.get_schedule = AsyncMock(return_value=schedule)
    container.store.get_profile = AsyncMock(return_value=profile)
    container.orchestrator.create = AsyncMock()
    container.store.save_schedule = AsyncMock()
    
    # Mock croniter inside trigger_schedule_run to fail
    with patch("croniter.croniter", side_effect=ValueError("Invalid timezone/cron")):
        await trigger_schedule_run(mock_app, "sched-1")
        # Should still save the schedule with updated last_run_at
        container.store.save_schedule.assert_called_once()


def test_add_or_update_schedule_job(mock_app: FastAPI) -> None:
    scheduler = mock_app.state.scheduler
    
    schedule = TestSchedule(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    
    # 1. Job doesn't exist
    scheduler.get_job = MagicMock(return_value=None)
    add_or_update_schedule_job(mock_app, schedule)
    scheduler.add_job.assert_called_once()
    
    # 2. Job exists -> should be removed and re-added
    scheduler.get_job = MagicMock(return_value=MagicMock())
    scheduler.add_job.reset_mock()
    add_or_update_schedule_job(mock_app, schedule)
    scheduler.remove_job.assert_called_once_with("sched-1")
    scheduler.add_job.assert_called_once()
    
    # 3. Schedule disabled -> should only remove if exists and not add
    disabled_sched = schedule.model_copy(update={"enabled": False})
    scheduler.remove_job.reset_mock()
    scheduler.add_job.reset_mock()
    add_or_update_schedule_job(mock_app, disabled_sched)
    scheduler.remove_job.assert_called_once_with("sched-1")
    scheduler.add_job.assert_not_called()
    
    # 4. APScheduler trigger error
    scheduler.add_job.side_effect = Exception("Scheduler error")
    add_or_update_schedule_job(mock_app, schedule) # Should handle error gracefully


def test_add_or_update_schedule_job_no_scheduler() -> None:
    # App state has no scheduler
    app = FastAPI()
    app.state.container = MagicMock()
    schedule = TestSchedule(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    add_or_update_schedule_job(app, schedule) # No exception raised


def test_remove_schedule_job(mock_app: FastAPI) -> None:
    scheduler = mock_app.state.scheduler
    scheduler.get_job = MagicMock(return_value=MagicMock())
    
    remove_schedule_job(mock_app, "sched-1")
    scheduler.remove_job.assert_called_once_with("sched-1")


def test_remove_schedule_job_no_scheduler() -> None:
    app = FastAPI()  # No scheduler set
    remove_schedule_job(app, "sched-1")  # Should handle gracefully, covers 102->exit


def test_remove_schedule_job_missing(mock_app: FastAPI) -> None:
    scheduler = mock_app.state.scheduler
    scheduler.get_job = MagicMock(return_value=None)
    
    remove_schedule_job(mock_app, "sched-1")
    scheduler.remove_job.assert_not_called()  # Should not attempt removal, covers 102->exit



@pytest.mark.asyncio
async def test_start_scheduler(mock_app: FastAPI) -> None:
    container = mock_app.state.container
    
    schedule1 = TestSchedule(
        id="sched-enabled",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    schedule2 = TestSchedule(
        id="sched-disabled",
        name="Test Sched 2",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=False,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    
    container.store.list_schedules = AsyncMock(return_value=[schedule1, schedule2])
    container.store.save_schedule = AsyncMock()
    
    with patch("qarunner.core.scheduler.AsyncIOScheduler") as mock_scheduler_class:
        mock_sched_inst = MagicMock()
        mock_scheduler_class.return_value = mock_sched_inst
        
        await start_scheduler(mock_app)
        
        # Verify scheduler is instantiated, started, and saved schedules are processed
        assert mock_app.state.scheduler == mock_sched_inst
        mock_sched_inst.start.assert_called_once()
        container.store.save_schedule.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_scheduler(mock_app: FastAPI) -> None:
    scheduler = mock_app.state.scheduler
    await shutdown_scheduler(mock_app)
    scheduler.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_scheduler_no_scheduler() -> None:
    app = FastAPI()
    await shutdown_scheduler(app)  # Should not raise exception, covers 137->exit


@pytest.mark.asyncio
async def test_start_scheduler_next_run_calc_failure(mock_app: FastAPI) -> None:
    container = mock_app.state.container
    schedule = TestSchedule(
        id="sched-fail",
        name="Fail Sched",
        profile_id="prof-1",
        cron_expression="invalid-expression-to-fail",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    container.store.list_schedules = AsyncMock(return_value=[schedule])
    container.store.save_schedule = AsyncMock()

    with patch("qarunner.core.scheduler.AsyncIOScheduler") as mock_scheduler_class:
        mock_sched_inst = MagicMock()
        mock_scheduler_class.return_value = mock_sched_inst
        
        await start_scheduler(mock_app)
        
        assert mock_app.state.scheduler == mock_sched_inst
        mock_sched_inst.start.assert_called_once()
        container.store.save_schedule.assert_not_called()

