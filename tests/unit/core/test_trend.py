"""Tests for core.trend — cross-run pass-rate trend (stage 1)."""

from __future__ import annotations

from datetime import UTC, datetime

from qarunner.core.trend import trend_points
from qarunner.models import Run, RunStatus, TestSummary


def _run(
    rid: str,
    path: str,
    day: int,
    *,
    status: RunStatus = RunStatus.COMPLETED,
    with_summary: bool = True,
    passed: int = 8,
    total: int = 10,
) -> Run:
    summary = (
        TestSummary(
            total=total,
            passed=passed,
            failed=total - passed,
            skipped=0,
            error=0,
            duration_ms=1,
        )
        if with_summary
        else None
    )
    return Run(
        id=rid,
        status=status,
        runner="pytest",
        created_by="u",
        tests_path=path,
        created_at=datetime(2025, 1, day, tzinfo=UTC),
        summary=summary,
    )


def test_trend_points_oldest_first_for_suite() -> None:
    runs = [
        _run("r3", "suite_a", 3, passed=9),
        _run("r1", "suite_a", 1, passed=5),
        _run("r2", "suite_a", 2, passed=7),
    ]
    pts = trend_points(runs, "suite_a")
    assert [p.run_id for p in pts] == ["r1", "r2", "r3"]  # ascending by created_at
    assert pts[0].pass_rate == 0.5
    assert pts[2].passed == 9
    assert pts[1].total == 10


def test_trend_points_excludes_other_suite_noncompleted_and_summaryless() -> None:
    runs = [
        _run("ok", "suite_a", 1),
        _run("other_suite", "suite_b", 2),
        _run("not_completed", "suite_a", 3, status=RunStatus.FAILED),
        _run("no_summary", "suite_a", 4, with_summary=False),
    ]
    pts = trend_points(runs, "suite_a")
    assert [p.run_id for p in pts] == ["ok"]


def test_trend_points_limit_keeps_most_recent_window() -> None:
    runs = [_run(f"r{d}", "suite_a", d) for d in range(1, 6)]  # days 1..5
    pts = trend_points(runs, "suite_a", limit=2)
    assert [p.run_id for p in pts] == ["r4", "r5"]  # 2 most recent, still ascending


def test_trend_points_empty_when_no_match() -> None:
    assert trend_points([], "suite_a") == []


def test_trend_points_limit_zero_returns_empty() -> None:
    """BUG: Python's `matching[-0:]` is `matching[0:]` (all elements) — a
    plain `[-limit:]` slice silently inverts the limit=0 "no data" contract
    that GET /cases/history (SQL `LIMIT 0`) already honours."""
    runs = [_run(f"r{d}", "suite_a", d) for d in range(1, 4)]
    assert trend_points(runs, "suite_a", limit=0) == []
