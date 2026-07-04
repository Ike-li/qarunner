"""Flaky-test detection — pure function, no DB (stage 3).

A test is "flaky" when its pass/fail outcome oscillates across runs rather than
moving monotonically (a one-off fix or regression is not flaky). Each status is
binarised to fail vs non-fail (``failed``/``error`` are failures; ``passed``/
``skipped`` are not, matching the diff view), then adjacent flips are counted.

The default policy is calibrated to avoid classifying a one-off regression and
fix (pass→fail→pass) as flaky. A case needs enough recent observations plus a
continued alternating pattern before it is flagged.
"""

from __future__ import annotations

from dataclasses import dataclass

_FAILED_STATUSES = frozenset({"failed", "error"})


@dataclass(frozen=True)
class FlakyPolicy:
    """Thresholds for classifying recent case history as flaky."""

    min_observations: int = 4
    flip_threshold: int = 3


DEFAULT_POLICY = FlakyPolicy()


def flakiness(statuses: list[str], policy: FlakyPolicy = DEFAULT_POLICY) -> tuple[bool, int]:
    """Return ``(is_flaky, flip_count)`` for a chronological status sequence.

    ``flip_count`` is the number of adjacent fail<->non-fail transitions; the
    case is flaky once enough observations exist and the flip count reaches the
    policy threshold. A monotone history, single transition, or small
    regression/fix sample is not flaky.
    """
    fails = [s in _FAILED_STATUSES for s in statuses]
    flips = sum(1 for a, b in zip(fails, fails[1:], strict=False) if a != b)
    return (len(statuses) >= policy.min_observations and flips >= policy.flip_threshold, flips)
