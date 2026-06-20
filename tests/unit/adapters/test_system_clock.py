"""Tests for SystemClock adapter."""

from __future__ import annotations

from datetime import UTC, datetime

from qarunner.adapters.system_clock import SystemClock


def test_system_clock_returns_utc_datetime() -> None:
    clock = SystemClock()
    before = datetime.now(tz=UTC)
    result = clock.now()
    after = datetime.now(tz=UTC)

    assert result.tzinfo is UTC
    assert before <= result <= after


def test_system_clock_successive_calls_increase() -> None:
    clock = SystemClock()
    t1 = clock.now()
    t2 = clock.now()
    assert t2 >= t1
