"""Flaky-test detection — pure function, no DB (stage 3).

A test is "flaky" when its pass/fail outcome oscillates across runs rather than
moving monotonically (a one-off fix or regression is not flaky). Each status is
binarised to fail vs non-fail (``failed``/``error`` are failures; ``passed``/
``skipped`` are not, matching the diff view), then adjacent flips are counted.

The flip threshold is a deliberately conservative default and SHOULD be tuned
against real data — it currently has no empirical basis.
"""

from __future__ import annotations

_FAILED_STATUSES = frozenset({"failed", "error"})

# [GUESS] no empirical basis yet: a case must flip fail<->non-fail at least twice
# (e.g. pass→fail→pass) to count as flaky, so a single fix/regression doesn't.
_FLAKY_FLIP_THRESHOLD = 2


def flakiness(statuses: list[str]) -> tuple[bool, int]:
    """Return ``(is_flaky, flip_count)`` for a chronological status sequence.

    ``flip_count`` is the number of adjacent fail<->non-fail transitions; the
    case is flaky once that reaches ``_FLAKY_FLIP_THRESHOLD``. A monotone history
    (all pass, all fail, or a single transition) is not flaky.
    """
    fails = [s in _FAILED_STATUSES for s in statuses]
    flips = sum(1 for a, b in zip(fails, fails[1:], strict=False) if a != b)
    return (flips >= _FLAKY_FLIP_THRESHOLD, flips)
