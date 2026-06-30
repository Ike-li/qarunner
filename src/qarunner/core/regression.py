"""Cross-run baseline diff — pure functions, no DB (stage 2).

Given a baseline run's per-case results and the current (head) run's, classify
each case into regression buckets keyed by identity ``(suite, name)``. A case
"fails" when its status is ``failed`` or ``error``; ``passed`` and ``skipped``
are non-failures — the regression view cares about red, not coverage.
"""

from __future__ import annotations

from qarunner.models import RegressionDiff, Run, RunStatus, TestCaseResult

_FAILED_STATUSES = frozenset({"failed", "error"})


def _identity(case: TestCaseResult) -> tuple[str, str]:
    return (case.suite, case.name)


def _is_failure(case: TestCaseResult) -> bool:
    return case.status in _FAILED_STATUSES


def diff(
    base_cases: list[TestCaseResult],
    head_cases: list[TestCaseResult],
) -> RegressionDiff:
    """Classify *head_cases* against *base_cases* into regression buckets.

    Identity is ``(suite, name)``. For a case present in both runs: new_failure
    (base ok → head fails), fixed (base fails → head ok), still_failing (both
    fail). Cases only in head are ``new_cases``; cases only in base are
    ``removed_cases``. A non-fail → non-fail transition yields no signal.
    """
    base_by = {_identity(c): c for c in base_cases}
    head_by = {_identity(c): c for c in head_cases}

    new_failures: list[TestCaseResult] = []
    fixed: list[TestCaseResult] = []
    still_failing: list[TestCaseResult] = []
    new_cases: list[TestCaseResult] = []
    removed_cases: list[TestCaseResult] = []

    for key, head in head_by.items():
        base = base_by.get(key)
        if base is None:
            new_cases.append(head)
            continue
        head_fail = _is_failure(head)
        base_fail = _is_failure(base)
        if head_fail and not base_fail:
            new_failures.append(head)
        elif not head_fail and base_fail:
            fixed.append(head)
        elif head_fail and base_fail:
            still_failing.append(head)
        # else: non-fail → non-fail, no regression signal

    for key, base in base_by.items():
        if key not in head_by:
            removed_cases.append(base)

    return RegressionDiff(
        new_failures=new_failures,
        fixed=fixed,
        still_failing=still_failing,
        new_cases=new_cases,
        removed_cases=removed_cases,
    )


def select_baseline(head: Run, candidates: list[Run]) -> Run | None:
    """Pick the most recent COMPLETED run preceding *head* with the same scope.

    Same execution scope = identical ``tests_path``, ``runner`` and compiled
    ``args`` (markers / selected files / extra args are folded into ``args`` at
    create time, so equal ``args`` ⇒ the same case universe — this is what
    keeps the diff's new/removed buckets meaningful rather than artefacts of a
    different selection). Only ``COMPLETED`` qualifies: FAILED/TIMEOUT/CANCELLED
    runs may hold no or partial cases and would inflate the diff. ``candidates``
    may include *head* itself (excluded by id). Returns ``None`` when no
    comparable baseline exists.
    """
    comparable = [
        r
        for r in candidates
        if r.id != head.id
        and r.status == RunStatus.COMPLETED
        and r.tests_path == head.tests_path
        and r.runner == head.runner
        and r.args == head.args
        and r.created_at < head.created_at
    ]
    if not comparable:
        return None
    return max(comparable, key=lambda r: r.created_at)
