"""Tests for core.flaky — flaky-test detection (stage 3)."""

from __future__ import annotations

from qarunner.core.flaky import FlakyPolicy, flakiness


def test_oscillating_history_is_flaky() -> None:
    is_flaky, flips = flakiness(["passed", "failed", "passed", "failed"])
    assert is_flaky is True
    assert flips == 3


def test_stable_failures_are_not_flaky() -> None:
    assert flakiness(["failed", "failed", "failed"]) == (False, 0)


def test_stable_passes_are_not_flaky() -> None:
    assert flakiness(["passed", "passed"]) == (False, 0)


def test_single_transition_is_not_flaky() -> None:
    # A one-off fix (fail→pass) flips once — below the threshold.
    assert flakiness(["failed", "passed"]) == (False, 1)


def test_two_flips_with_only_three_observations_is_not_flaky_by_default() -> None:
    # pass→fail→pass can be a regression + fix; calibrated default waits for
    # another alternating observation before calling the case flaky.
    assert flakiness(["passed", "failed", "passed"]) == (False, 2)


def test_policy_can_be_tuned_for_smaller_local_datasets() -> None:
    policy = FlakyPolicy(min_observations=3, flip_threshold=2)
    assert flakiness(["passed", "failed", "passed"], policy=policy) == (True, 2)


def test_error_is_failure_and_skipped_is_not() -> None:
    # error is a failure; skipped sits on the non-fail side (like the diff view),
    # so pass→error→skipped→error flips at each step.
    is_flaky, flips = flakiness(["passed", "error", "skipped", "error"])
    assert is_flaky is True
    assert flips == 3


def test_empty_history_is_not_flaky() -> None:
    assert flakiness([]) == (False, 0)
