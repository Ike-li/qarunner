"""Tests for qarunner.core.cron — cron validation and run-time computation."""

from __future__ import annotations

from datetime import datetime

from qarunner.core import cron


def test_is_valid_timezone() -> None:
    assert cron.is_valid_timezone("UTC")
    assert cron.is_valid_timezone("America/New_York")
    assert not cron.is_valid_timezone("Not/A_Zone")


def test_is_valid_cron() -> None:
    assert cron.is_valid_cron("*/5 * * * *")
    assert not cron.is_valid_cron("not-a-cron")


def test_next_runs_returns_count_in_order() -> None:
    runs = cron.next_runs("*/5 * * * *", "UTC", 3)
    assert len(runs) == 3
    assert all(isinstance(r, datetime) for r in runs)
    assert runs == sorted(runs)


def test_next_runs_defaults_to_one() -> None:
    assert len(cron.next_runs("0 0 * * *", "UTC")) == 1


def test_next_run_after_previous_run() -> None:
    expr, tz = "*/5 * * * *", "UTC"
    # The current tick is in the past; the next fire time is in the future.
    assert cron.previous_run(expr, tz) < cron.next_run(expr, tz)


def test_previous_run_returns_datetime() -> None:
    assert isinstance(cron.previous_run("*/5 * * * *", "UTC"), datetime)
