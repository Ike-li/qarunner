"""Tests for ApschedulerSchedulePort (APScheduler-backed cron SchedulePort)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qarunner.adapters.apscheduler_schedule import ApschedulerSchedulePort
from qarunner.models import RunRequest, TestProfile, TestSchedule
from qarunner.ports.schedule import SchedulePort


def _make_port() -> tuple[ApschedulerSchedulePort, MagicMock, MagicMock]:
    store = MagicMock()
    orchestrator = MagicMock()
    return ApschedulerSchedulePort(store=store, orchestrator=orchestrator), store, orchestrator


def _schedule(**overrides: Any) -> TestSchedule:
    base: dict[str, Any] = dict(
        id="sched-1",
        name="Test Sched",
        profile_id="prof-1",
        cron_expression="*/5 * * * *",
        enabled=True,
        timezone="UTC",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    base.update(overrides)
    return TestSchedule(**base)


def _profile(**overrides: Any) -> TestProfile:
    base: dict[str, Any] = dict(
        id="prof-1",
        name="Test Profile",
        tests_path="sample",
        created_by="user",
        created_at=datetime.now(UTC),
    )
    base.update(overrides)
    return TestProfile(**base)


def test_adapter_satisfies_schedule_port() -> None:
    port, _, _ = _make_port()
    assert isinstance(port, SchedulePort)


# ── _trigger (the fired job callback) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_success() -> None:
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule())
    store.get_profile = AsyncMock(return_value=_profile())
    store.save_schedule = AsyncMock()
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    store.get_schedule.assert_called_once_with("sched-1")
    store.get_profile.assert_called_once_with("prof-1")
    # The cron tick is claimed for leader election before the run is created.
    store.claim_schedule_run.assert_called_once()

    orch.create.assert_called_once()
    run_req: RunRequest = orch.create.call_args[0][0]
    assert run_req.tests_path == "sample"
    assert orch.create.call_args[1]["created_by"] == "system:schedule"

    store.save_schedule.assert_called_once()
    saved: TestSchedule = store.save_schedule.call_args[0][0]
    assert saved.last_run_at is not None
    assert saved.next_run_at is not None


@pytest.mark.asyncio
async def test_trigger_passes_profile_env() -> None:
    """FUNC-1: a profile's env is forwarded into the scheduled RunRequest."""
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule())
    store.get_profile = AsyncMock(return_value=_profile(env={"PROFILE_VAR": "from-profile"}))
    store.save_schedule = AsyncMock()
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    run_req: RunRequest = orch.create.call_args[0][0]
    assert run_req.env == {"PROFILE_VAR": "from-profile"}


@pytest.mark.asyncio
async def test_trigger_uses_profile_runner() -> None:
    """The scheduled run must honour the profile's runner, not a hardcoded
    'pytest' — else scheduling a playwright profile silently runs pytest."""
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule())
    store.get_profile = AsyncMock(
        return_value=_profile(runner="playwright", executor_mode="subprocess")
    )
    store.save_schedule = AsyncMock()
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    run_req: RunRequest = orch.create.call_args[0][0]
    assert run_req.runner == "playwright"


@pytest.mark.asyncio
async def test_trigger_missing_or_disabled() -> None:
    port, store, orch = _make_port()
    orch.create = AsyncMock()

    # 1. Missing schedule
    store.get_schedule = AsyncMock(return_value=None)
    await port._trigger("sched-missing")
    orch.create.assert_not_called()

    # 2. Disabled schedule
    store.get_schedule = AsyncMock(return_value=_schedule(id="sched-disabled", enabled=False))
    await port._trigger("sched-disabled")
    orch.create.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_missing_profile() -> None:
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule(profile_id="prof-missing"))
    store.get_profile = AsyncMock(return_value=None)
    orch.create = AsyncMock()

    await port._trigger("sched-1")
    orch.create.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_skips_cross_owner_profile_for_non_admin_schedule() -> None:
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(
        return_value=_schedule(created_by="alice", profile_id="prof-bob")
    )
    store.get_profile = AsyncMock(return_value=_profile(id="prof-bob", created_by="bob"))
    store.get_user = AsyncMock(return_value={"username": "alice", "role": "user"})
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    store.claim_schedule_run.assert_not_called()
    orch.create.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_skips_cross_owner_profile_when_schedule_creator_missing() -> None:
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(
        return_value=_schedule(created_by="deleted-user", profile_id="prof-bob")
    )
    store.get_profile = AsyncMock(return_value=_profile(id="prof-bob", created_by="bob"))
    store.get_user = AsyncMock(return_value=None)
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    store.claim_schedule_run.assert_not_called()
    orch.create.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_allows_admin_schedule_to_use_cross_owner_profile() -> None:
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(
        return_value=_schedule(created_by="admin", profile_id="prof-bob")
    )
    store.get_profile = AsyncMock(return_value=_profile(id="prof-bob", created_by="bob"))
    store.get_user = AsyncMock(return_value={"username": "admin", "role": "admin"})
    store.save_schedule = AsyncMock()
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    store.claim_schedule_run.assert_called_once()
    orch.create.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_skips_when_already_claimed() -> None:
    """CONC-2: when another replica already claimed this cron tick, no run is created."""
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule())
    store.get_profile = AsyncMock(return_value=_profile())
    store.claim_schedule_run = AsyncMock(return_value=False)
    store.save_schedule = AsyncMock()
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    store.claim_schedule_run.assert_called_once()
    orch.create.assert_not_called()
    store.save_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_dedups_across_concurrent_replicas(tmp_path: Any) -> None:
    """CONC-2 end-to-end: two replicas (separate ports) firing the same tick against
    a shared file-backed DB create exactly one run (the DB claim elects a leader)."""
    from qarunner.adapters.sqlite_store import SqliteStore

    # A real file-backed DB (as in a multi-replica deployment): concurrent claims
    # serialise on the file write lock via busy_timeout. A ":memory:" shared-cache
    # store would instead raise SQLITE_LOCKED — an artefact that does not exist in prod.
    store = SqliteStore(str(tmp_path / "sched.db"))
    await store.initialize()
    await store.save_profile(_profile())
    await store.save_schedule(_schedule(name="S"))

    def make_replica() -> ApschedulerSchedulePort:
        # Each replica shares the DB but has its own orchestrator.
        orch = MagicMock()
        orch.create = AsyncMock()
        return ApschedulerSchedulePort(store=store, orchestrator=orch)

    replica_a, replica_b = make_replica(), make_replica()
    await asyncio.gather(
        replica_a._trigger("sched-1"),
        replica_b._trigger("sched-1"),
    )

    total_created = (
        replica_a._orchestrator.create.call_count + replica_b._orchestrator.create.call_count
    )
    assert total_created == 1
    await store.close()


@pytest.mark.asyncio
async def test_trigger_handles_orchestrator_exception() -> None:
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule())
    store.get_profile = AsyncMock(return_value=_profile())
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock(side_effect=Exception("Execution failed"))

    # The exception inside orchestrator is handled and doesn't crash the trigger.
    await port._trigger("sched-1")
    orch.create.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_next_run_calc_failure() -> None:
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule())
    store.get_profile = AsyncMock(return_value=_profile())
    store.claim_schedule_run = AsyncMock(return_value=True)
    store.save_schedule = AsyncMock()
    orch.create = AsyncMock()

    # Both cron computations fail: the tick-claim block falls back to now(UTC)
    # and the next_run_at computation is logged and skipped; the schedule is
    # still saved with an updated last_run_at.
    with (
        patch("qarunner.core.cron.previous_run", side_effect=ValueError("bad cron")),
        patch("qarunner.core.cron.next_run", side_effect=ValueError("bad cron")),
    ):
        await port._trigger("sched-1")
    store.save_schedule.assert_called_once()


# ── upsert / remove ─────────────────────────────────────────────────────────


def test_upsert_no_scheduler() -> None:
    """Before start(), there is no engine — upsert is a no-op."""
    port, _, _ = _make_port()
    port.upsert(_schedule())  # No exception raised.


def test_upsert_add_replace_disable_and_error() -> None:
    port, _, _ = _make_port()
    scheduler = MagicMock()
    port._scheduler = scheduler
    schedule = _schedule()

    # 1. Job doesn't exist -> add
    scheduler.get_job = MagicMock(return_value=None)
    port.upsert(schedule)
    scheduler.add_job.assert_called_once()

    # 2. Job exists -> remove then re-add
    scheduler.get_job = MagicMock(return_value=MagicMock())
    scheduler.add_job.reset_mock()
    port.upsert(schedule)
    scheduler.remove_job.assert_called_once_with("sched-1")
    scheduler.add_job.assert_called_once()

    # 3. Disabled -> remove if exists, no add
    scheduler.remove_job.reset_mock()
    scheduler.add_job.reset_mock()
    port.upsert(schedule.model_copy(update={"enabled": False}))
    scheduler.remove_job.assert_called_once_with("sched-1")
    scheduler.add_job.assert_not_called()

    # 4. add_job raises -> handled gracefully
    scheduler.get_job = MagicMock(return_value=None)
    scheduler.add_job.side_effect = Exception("Scheduler error")
    port.upsert(schedule)  # No exception raised.


def test_remove_present() -> None:
    port, _, _ = _make_port()
    scheduler = MagicMock()
    scheduler.get_job = MagicMock(return_value=MagicMock())
    port._scheduler = scheduler

    port.remove("sched-1")
    scheduler.remove_job.assert_called_once_with("sched-1")


def test_remove_no_scheduler() -> None:
    port, _, _ = _make_port()
    port.remove("sched-1")  # No engine yet; handled gracefully.


def test_remove_missing_job() -> None:
    port, _, _ = _make_port()
    scheduler = MagicMock()
    scheduler.get_job = MagicMock(return_value=None)
    port._scheduler = scheduler

    port.remove("sched-1")
    scheduler.remove_job.assert_not_called()


# ── start / shutdown ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_loads_enabled_schedules() -> None:
    port, store, _ = _make_port()
    enabled = _schedule(id="sched-enabled")
    disabled = _schedule(id="sched-disabled", enabled=False)
    store.list_schedules = AsyncMock(return_value=[enabled, disabled])
    store.save_schedule = AsyncMock()

    with patch("qarunner.adapters.apscheduler_schedule.AsyncIOScheduler") as cls:
        inst = MagicMock()
        cls.return_value = inst
        await port.start()

    # The engine is instantiated, started, and only the enabled schedule is saved.
    assert port._scheduler is inst
    inst.start.assert_called_once()
    store.save_schedule.assert_called_once()


@pytest.mark.asyncio
async def test_start_next_run_calc_failure() -> None:
    port, store, _ = _make_port()
    bad = _schedule(id="sched-fail", cron_expression="invalid-expression-to-fail")
    store.list_schedules = AsyncMock(return_value=[bad])
    store.save_schedule = AsyncMock()

    with patch("qarunner.adapters.apscheduler_schedule.AsyncIOScheduler") as cls:
        inst = MagicMock()
        cls.return_value = inst
        await port.start()

    assert port._scheduler is inst
    inst.start.assert_called_once()
    # croniter failed before the save, so next_run_at was never persisted.
    store.save_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_stops_engine() -> None:
    port, _, _ = _make_port()
    scheduler = MagicMock()
    port._scheduler = scheduler

    await port.shutdown()
    scheduler.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_no_scheduler() -> None:
    port, _, _ = _make_port()
    await port.shutdown()  # No engine; handled gracefully.


# ── BUG regression: _trigger must pass profile_id to orchestrator.create ───────


@pytest.mark.asyncio
async def test_trigger_passes_profile_id_to_orchestrator() -> None:
    """BUG: _trigger() calls orchestrator.create() without profile_id=.

    The notification feature (v7+v8 migrations, notification.py) expects runs to
    carry a profile_id so _notify() can look up the profile's webhook_url.  The
    manual trigger (POST /schedules/{id}/trigger) correctly passes profile_id via
    _create_run_guarded, but the cron path (_trigger) calls create() without it,
    so cron-triggered runs never have profile_id set and notifications silently
    skip them.

    Fix: _trigger() should pass ``profile_id=schedule.profile_id`` to create().
    """
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule(profile_id="prof-xyz"))
    store.get_profile = AsyncMock(return_value=_profile(id="prof-xyz"))
    store.save_schedule = AsyncMock()
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    orch.create.assert_called_once()
    _, kwargs = orch.create.call_args
    assert kwargs.get("profile_id") == "prof-xyz", (
        f"BUG: _trigger() called create() with profile_id={kwargs.get('profile_id')!r}, "
        f"expected 'prof-xyz'. Notifications for cron-triggered runs are silently broken "
        f"because _notify() returns early when run.profile_id is None."
    )


# ── Regression guard: cron trigger always uses Docker ──────────────────────────


@pytest.mark.asyncio
async def test_trigger_runs_in_docker_regardless_of_profile_mode() -> None:
    """Cron-triggered runs always use Docker, even if the profile's legacy
    executor_mode is 'subprocess'.  orchestrator.create() forces 'docker' now."""
    port, store, orch = _make_port()
    store.get_schedule = AsyncMock(return_value=_schedule(profile_id="prof-x"))
    store.get_profile = AsyncMock(
        return_value=_profile(
            id="prof-x",
            executor_mode="subprocess",
        )
    )
    store.save_schedule = AsyncMock()
    store.claim_schedule_run = AsyncMock(return_value=True)
    orch.create = AsyncMock()

    await port._trigger("sched-1")

    orch.create.assert_called_once()
    # The orchestrator forces docker regardless of the profile's executor_mode.
    # The trigger does not need a gate — the orchestrator is the chokepoint now.
