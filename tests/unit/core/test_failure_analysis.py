"""Tests for core.failure_analysis — pure context assembly, prompt, parsing."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from qarunner.core.failure_analysis import (
    build_failure_context,
    build_messages,
    parse_diagnosis,
)
from qarunner.models import (
    DiagnosisConfidence,
    RegressionDiff,
    RootCauseCategory,
    Run,
    RunStatus,
    TestCaseResult,
    TrendPoint,
)


def _run(**kw) -> Run:
    base = {
        "id": "run1",
        "status": RunStatus.FAILED,
        "runner": "pytest",
        "created_by": "alice",
        "tests_path": "api-tests",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "exit_code": 1,
        "profile_id": "prof1",
    }
    base.update(kw)
    return Run(**base)


def _case(suite: str = "s", name: str = "t", status: str = "failed", message="boom"):
    return TestCaseResult(suite=suite, name=name, status=status, duration_ms=1, message=message)


def _trend(pass_rate: float) -> TrendPoint:
    return TrendPoint(
        run_id="r",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        pass_rate=pass_rate,
        total=10,
        passed=int(pass_rate * 10),
        failed=10 - int(pass_rate * 10),
    )


_GOOD_JSON = {
    "category": "assertion",
    "confidence": "HIGH",
    "summary": "an assertion failed",
    "evidence": ["expected 200 got 500"],
    "is_likely_regression": True,
    "next_steps": [{"kind": "inspect_log", "action": "read the traceback", "reference": None}],
}


# ── build_failure_context ────────────────────────────────────────────────────


def test_context_keeps_only_failures():
    ctx = build_failure_context(
        _run(),
        cases=[
            _case(name="ok", status="passed"),
            _case(name="bad", status="failed"),
            _case(name="err", status="error"),
            _case(name="skip", status="skipped"),
        ],
        log_tail="",
        baseline_diff=None,
        trend=[],
        flaky_identities=set(),
        allure_report_url=None,
    )
    assert ctx is not None
    assert {c.name for c in ctx.failed_cases} == {"bad", "err"}


def test_context_none_without_failures():
    ctx = build_failure_context(
        _run(),
        cases=[_case(status="passed"), _case(status="skipped")],
        log_tail="",
        baseline_diff=None,
        trend=[],
        flaky_identities=set(),
        allure_report_url=None,
    )
    assert ctx is None


def test_context_carries_all_fields():
    diff = RegressionDiff(new_failures=[_case()])
    ctx = build_failure_context(
        _run(),
        cases=[_case(suite="auth", name="test_login")],
        log_tail="tail",
        baseline_diff=diff,
        trend=[_trend(0.9)],
        flaky_identities={("auth", "test_login")},
        allure_report_url="http://x/report",
    )
    assert ctx is not None
    assert ctx.flaky_identities == frozenset({("auth", "test_login")})
    assert ctx.baseline_diff is diff
    assert len(ctx.trend) == 1
    assert ctx.allure_report_url == "http://x/report"
    assert ctx.log_tail == "tail"


# ── build_messages ───────────────────────────────────────────────────────────


def test_messages_full_render():
    ctx = build_failure_context(
        _run(),
        cases=[_case(suite="auth", name="test_login", message="A" * 600)],
        log_tail="some log line",
        baseline_diff=RegressionDiff(new_failures=[_case()], fixed=[_case(name="f")]),
        trend=[_trend(0.9), _trend(0.8)],
        flaky_identities={("auth", "test_login")},
        allure_report_url="http://x/report",
    )
    assert ctx is not None
    system, user = build_messages(ctx)
    # System prompt lists the taxonomy the model must classify into.
    assert "new_failure" in system
    assert "historical_flaky" in system
    assert "JSON object" in system
    # User prompt renders every data source.
    assert "Run run1" in user and "runner=pytest" in user and "exit_code=1" in user
    assert "Triggered by profile: prof1" in user
    assert "auth::test_login [historically flaky]" in user
    assert "…" in user  # 600-char message truncated
    assert "new_failures=1" in user and "fixed=1" in user
    assert "Pass-rate trend" in user and "90%" in user and "80%" in user
    assert "Allure report: http://x/report" in user
    assert "some log line" in user


def test_messages_minimal_render():
    ctx = build_failure_context(
        _run(profile_id=None, exit_code=None),
        cases=[_case(message="short")],
        log_tail="",
        baseline_diff=None,
        trend=[],
        flaky_identities=set(),
        allure_report_url=None,
    )
    assert ctx is not None
    _system, user = build_messages(ctx)
    assert "Triggered manually" in user
    assert "No comparable baseline" in user
    assert "(empty)" in user
    assert "[historically flaky]" not in user
    assert "Pass-rate trend" not in user
    assert "Allure report:" not in user
    assert "…" not in user


# ── parse_diagnosis ──────────────────────────────────────────────────────────


def test_parse_plain_json():
    d = parse_diagnosis(json.dumps(_GOOD_JSON))
    assert d.category == RootCauseCategory.ASSERTION
    assert d.confidence == DiagnosisConfidence.HIGH
    assert d.is_likely_regression is True
    assert d.next_steps[0].kind == "inspect_log"


def test_parse_fenced_closed():
    d = parse_diagnosis("```json\n" + json.dumps(_GOOD_JSON) + "\n```")
    assert d.category == RootCauseCategory.ASSERTION


def test_parse_fenced_unclosed():
    d = parse_diagnosis("```json\n" + json.dumps(_GOOD_JSON))
    assert d.category == RootCauseCategory.ASSERTION


def test_parse_bare_fence_degrades():
    d = parse_diagnosis("```")
    assert d.category == RootCauseCategory.ENVIRONMENT
    assert d.confidence == DiagnosisConfidence.LOW


def test_parse_malformed_degrades():
    d = parse_diagnosis("this is not json at all")
    assert d.category == RootCauseCategory.ENVIRONMENT
    assert d.confidence == DiagnosisConfidence.LOW
    assert "Could not parse" in d.summary


def test_parse_non_object_degrades():
    d = parse_diagnosis("[1, 2, 3]")
    assert d.category == RootCauseCategory.ENVIRONMENT
    assert d.confidence == DiagnosisConfidence.LOW
