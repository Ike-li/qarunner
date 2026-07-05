"""Tests for api.schemas — RunResponse, run_to_response, RunListResponse."""

from __future__ import annotations

from datetime import UTC, datetime

from qarunner.api.schemas import RunListResponse, RunResponse, profile_to_response, run_to_response
from qarunner.models import (
    ReportRef,
    Run,
    RunStatus,
    TestProfile,
    TestSummary,
)

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_run(**overrides: object) -> Run:
    """Helper: build a minimal Run, overriding any field."""
    base: dict[str, object] = dict(
        id="run-001",
        status=RunStatus.QUEUED,
        runner="pytest",
        created_by="test_user",
        tests_path="tests/",
        created_at=NOW,
    )
    base.update(overrides)
    return Run(**base)  # type: ignore[arg-type]


# ── run_to_response basics ─────────────────────────────────────────────


def test_run_to_response_copies_fields() -> None:
    run = _make_run(profile_id="profile-001")
    resp = run_to_response(run)

    assert isinstance(resp, RunResponse)
    assert resp.id == "run-001"
    assert resp.status == RunStatus.QUEUED
    assert resp.runner == "pytest"
    assert resp.created_by == "test_user"
    assert resp.tests_path == "tests/"
    assert resp.summary is None
    assert resp.report is None
    assert resp.exit_code is None
    assert resp.error is None
    assert resp.passed is None
    assert resp.created_at == NOW
    assert resp.started_at is None
    assert resp.finished_at is None
    assert resp.profile_id == "profile-001"


def test_profile_to_response_preserves_runner() -> None:
    profile = TestProfile(
        id="profile-001",
        name="Playwright daily",
        tests_path="my-e2e-suite",
        runner="playwright",
        executor_mode="docker",
        created_by="test_user",
        created_at=NOW,
    )

    resp = profile_to_response(profile)

    assert resp.runner == "playwright"
    assert resp.executor_mode == "docker"


def test_run_to_response_with_summary_and_report() -> None:
    summary = TestSummary(total=5, passed=5, failed=0, skipped=0, error=0, duration_ms=120)
    report = ReportRef(
        allure_results_dir="/tmp/results",
        allure_report_file="/tmp/r.html",
        html_generated=True,
    )
    started = datetime(2025, 6, 1, 12, 0, 1, tzinfo=UTC)
    finished = datetime(2025, 6, 1, 12, 0, 5, tzinfo=UTC)

    run = _make_run(
        status=RunStatus.COMPLETED,
        summary=summary,
        report=report,
        exit_code=0,
        started_at=started,
        finished_at=finished,
    )
    resp = run_to_response(run)

    assert resp.summary is summary
    assert resp.report is report
    assert resp.exit_code == 0
    assert resp.started_at == started
    assert resp.finished_at == finished


# ── passed field logic ─────────────────────────────────────────────────


def test_passed_true_when_completed_no_failures() -> None:
    summary = TestSummary(total=3, passed=3, failed=0, skipped=0, error=0, duration_ms=50)
    run = _make_run(status=RunStatus.COMPLETED, summary=summary)
    assert run_to_response(run).passed is True


def test_passed_false_when_completed_with_failures() -> None:
    summary = TestSummary(total=3, passed=1, failed=2, skipped=0, error=0, duration_ms=50)
    run = _make_run(status=RunStatus.COMPLETED, summary=summary)
    assert run_to_response(run).passed is False


def test_passed_false_when_completed_with_errors() -> None:
    summary = TestSummary(total=3, passed=2, failed=0, skipped=0, error=1, duration_ms=50)
    run = _make_run(status=RunStatus.COMPLETED, summary=summary)
    assert run_to_response(run).passed is False


def test_passed_none_when_failed() -> None:
    run = _make_run(status=RunStatus.FAILED)
    assert run_to_response(run).passed is None


def test_passed_none_when_queued() -> None:
    run = _make_run(status=RunStatus.QUEUED)
    assert run_to_response(run).passed is None


def test_passed_none_when_running() -> None:
    run = _make_run(status=RunStatus.RUNNING)
    assert run_to_response(run).passed is None


def test_passed_none_when_timeout() -> None:
    run = _make_run(status=RunStatus.TIMEOUT)
    assert run_to_response(run).passed is None


def test_passed_none_when_completed_without_summary() -> None:
    run = _make_run(status=RunStatus.COMPLETED, summary=None)
    assert run_to_response(run).passed is None


# ── RunListResponse ────────────────────────────────────────────────────


def test_run_list_response() -> None:
    r1 = _make_run(id="a")
    r2 = _make_run(id="b")
    resp = RunListResponse(runs=[run_to_response(r1), run_to_response(r2)])
    assert len(resp.runs) == 2
    assert resp.runs[0].id == "a"
    assert resp.runs[1].id == "b"
