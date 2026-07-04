"""Tests for LoginThrottle — brute-force lockout with exponential backoff (SEC-5)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from qarunner.core.login_throttle import LoginThrottle
from qarunner.errors import LoginLockedOut
from tests.fakes.fake_clock import FakeClock

KEY = "alice|1.2.3.4"


def _throttle(clock: FakeClock | None = None) -> LoginThrottle:
    return LoginThrottle(
        clock=clock or FakeClock(),
        threshold=3,
        base_seconds=60.0,
        max_seconds=240.0,
    )


def _retry_after(throttle: LoginThrottle, key: str) -> int | None:
    """Return the lockout's Retry-After, or None if *key* is not locked."""
    try:
        throttle.check(key)
    except LoginLockedOut as exc:
        return exc.retry_after
    return None


def test_check_is_noop_for_unknown_key() -> None:
    throttle = _throttle()
    throttle.check("never-seen")  # must not raise


def test_below_threshold_does_not_lock() -> None:
    throttle = _throttle()
    throttle.record_failure(KEY)
    throttle.record_failure(KEY)
    # 2 of 3 failures — still allowed.
    assert _retry_after(throttle, KEY) is None


def test_threshold_failures_lock() -> None:
    throttle = _throttle()
    for _ in range(3):
        throttle.record_failure(KEY)
    assert _retry_after(throttle, KEY) == 60


def test_lock_blocks_until_window_elapses() -> None:
    clock = FakeClock()
    throttle = _throttle(clock)
    for _ in range(3):
        throttle.record_failure(KEY)

    # Just before expiry: still locked.
    clock.current = clock.current + timedelta(seconds=59)
    assert _retry_after(throttle, KEY) == 1

    # After the window: lock clears.
    clock.current = clock.current + timedelta(seconds=2)
    assert _retry_after(throttle, KEY) is None


def test_success_resets_failures() -> None:
    throttle = _throttle()
    throttle.record_failure(KEY)
    throttle.record_failure(KEY)
    throttle.record_success(KEY)
    # Counter cleared — a fresh failure does not immediately lock.
    throttle.record_failure(KEY)
    assert _retry_after(throttle, KEY) is None


def test_backoff_escalates_across_lockouts() -> None:
    clock = FakeClock()
    throttle = _throttle(clock)
    durations = []
    for _ in range(3):
        for _ in range(3):
            throttle.record_failure(KEY)
        durations.append(_retry_after(throttle, KEY))
        # Wait out the current lock so the next cycle can escalate.
        clock.current = clock.current + timedelta(seconds=durations[-1] + 1)
        throttle.check(KEY)  # clears the expired lock window
    # 60 → 120 → capped at max_seconds (240, not 480).
    assert durations == [60, 120, 240]


def test_keys_are_isolated() -> None:
    throttle = _throttle()
    other = "bob|9.9.9.9"
    for _ in range(3):
        throttle.record_failure(KEY)
    assert _retry_after(throttle, KEY) == 60
    assert _retry_after(throttle, other) is None


@pytest.mark.parametrize("remaining", [0.1, 30.4, 59.9])
def test_retry_after_rounds_up(remaining: float) -> None:
    """Retry-After never under-reports the wait (ceil, floored at 1)."""
    clock = FakeClock()
    throttle = _throttle(clock)
    for _ in range(3):
        throttle.record_failure(KEY)
    clock.current = clock.current + timedelta(seconds=60 - remaining)
    import math

    assert _retry_after(throttle, KEY) == max(1, math.ceil(remaining))


def test_lru_eviction_at_max_capacity() -> None:
    throttle = _throttle()
    # Populate throttle to max capacity of 10000
    for i in range(10000):
        throttle.record_failure(f"user_{i}")
    # Adding one more should evict the oldest (user_0)
    throttle.record_failure("user_new")
    assert "user_0" not in throttle._attempts
    assert "user_new" in throttle._attempts


def test_eviction_skips_actively_locked_entry() -> None:
    # A flood of fresh keys must not evict an active lockout: eviction prefers an
    # unlocked entry even when a locked one is older (else an attacker bypasses
    # their own lock by flooding the table).
    throttle = LoginThrottle(
        clock=FakeClock(), threshold=2, base_seconds=60.0, max_seconds=60.0, max_identities=2
    )
    throttle.record_failure("A")  # A: 1 failure
    throttle.record_failure("A")  # A: 2 → locked (oldest, locked)
    throttle.record_failure("B")  # B: 1 failure (younger, unlocked)
    throttle.record_failure("C")  # cap reached → evict; A is locked so B is dropped
    assert "A" in throttle._attempts  # active lock preserved despite being oldest
    assert "B" not in throttle._attempts  # younger unlocked entry evicted
    assert "C" in throttle._attempts


def test_eviction_falls_back_to_oldest_when_all_locked() -> None:
    # Pathological: every tracked identity is actively locked → sacrifice the oldest.
    throttle = LoginThrottle(
        clock=FakeClock(), threshold=1, base_seconds=60.0, max_seconds=60.0, max_identities=2
    )
    throttle.record_failure("A")  # threshold=1 → A locked
    throttle.record_failure("B")  # B locked
    throttle.record_failure("C")  # all locked → evict the oldest (A)
    assert "A" not in throttle._attempts
    assert "B" in throttle._attempts
    assert "C" in throttle._attempts
