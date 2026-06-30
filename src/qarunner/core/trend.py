"""Cross-run pass-rate trend — pure function, no DB (stage 1).

Turn a list of runs into an ascending time series of pass_rate / counts for one
suite (``tests_path``), so the dashboard can plot how the suite's health moves
across runs. Only COMPLETED runs carrying a summary contribute a point (others
have no pass_rate); the newest ``limit`` are returned oldest-first so a chart
reads left-to-right in time.
"""

from __future__ import annotations

from qarunner.models import Run, RunStatus, TrendPoint


def trend_points(runs: list[Run], tests_path: str, limit: int = 50) -> list[TrendPoint]:
    """Most-recent ``limit`` trend points for *tests_path*, oldest-first.

    A run contributes only when it ran the same suite, is COMPLETED, and carries
    a summary (so ``pass_rate`` exists). Sorted by ``created_at``; the trailing
    ``limit`` are kept so the chart shows the latest window in chronological
    order.
    """
    matching = sorted(
        (
            r
            for r in runs
            if r.tests_path == tests_path
            and r.status == RunStatus.COMPLETED
            and r.summary is not None
        ),
        key=lambda r: r.created_at,
    )
    return [
        TrendPoint(
            run_id=r.id,
            created_at=r.created_at,
            pass_rate=r.summary.pass_rate,
            total=r.summary.total,
            passed=r.summary.passed,
            failed=r.summary.failed,
        )
        for r in matching[-limit:]
    ]
