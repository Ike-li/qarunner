"""Tests for SlidingWindowRateLimiter."""

from __future__ import annotations

from datetime import timedelta

import pytest

from qarunner.core.rate_limit import SlidingWindowRateLimiter
from qarunner.errors import RateLimited
from tests.fakes.fake_clock import FakeClock

KEY = "alice"


def _limiter(clock: FakeClock | None = None, **kw) -> SlidingWindowRateLimiter:
    defaults = dict(max_calls=3, window_seconds=60.0)
    defaults.update(kw)
    return SlidingWindowRateLimiter(clock=clock or FakeClock(), **defaults)


def test_allows_up_to_max_calls_then_blocks() -> None:
    lim = _limiter(max_calls=3)
    lim.check_and_record(KEY)
    lim.check_and_record(KEY)
    lim.check_and_record(KEY)
    with pytest.raises(RateLimited) as exc:
        lim.check_and_record(KEY)
    assert exc.value.retry_after >= 1


def test_window_expiry_frees_budget() -> None:
    clock = FakeClock()
    lim = _limiter(clock=clock, max_calls=2, window_seconds=60.0)
    lim.check_and_record(KEY)
    lim.check_and_record(KEY)
    with pytest.raises(RateLimited):
        lim.check_and_record(KEY)

    clock.current = clock.current + timedelta(seconds=61)
    lim.check_and_record(KEY)  # budget refreshed


def test_max_calls_zero_disables() -> None:
    lim = _limiter(max_calls=0)
    for _ in range(50):
        lim.check_and_record(KEY)


def test_identities_are_isolated() -> None:
    lim = _limiter(max_calls=1)
    lim.check_and_record("a")
    lim.check_and_record("b")  # different key still allowed
    with pytest.raises(RateLimited):
        lim.check_and_record("a")


def test_evicts_lru_when_cap_reached() -> None:
    lim = _limiter(max_calls=1, max_identities=2)
    lim.check_and_record("old")
    lim.check_and_record("mid")
    # third identity forces eviction of least-recently-used "old"
    lim.check_and_record("new")
    # "old" was evicted — a fresh call is allowed again
    lim.check_and_record("old")
