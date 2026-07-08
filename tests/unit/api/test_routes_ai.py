"""Tests for the AI failure-analysis endpoints (GET/POST /runs/{id}/ai-analysis)."""

from __future__ import annotations

from datetime import UTC, datetime

from starlette.testclient import TestClient

from qarunner.api import routes
from qarunner.api.deps import get_current_user
from qarunner.models import (
    DiagnosisConfidence,
    FailureDiagnosis,
    ReportRef,
    RootCauseCategory,
    RunStatus,
    TestCaseResult,
    TestSummary,
    User,
    UserRole,
)
from tests.fakes.fake_ai_analyzer import FakeFailureAnalyzer
from tests.unit.api.test_routes import (
    NOW,
    _make_container,
    _make_run_in_store,
    create_app,
)

_DIAG = FailureDiagnosis(
    category=RootCauseCategory.ASSERTION,
    confidence=DiagnosisConfidence.HIGH,
    summary="an assertion failed",
)


def _with_ai(analyzer: object = None):
    container = _make_container()
    container.ai_analyzer = analyzer or FakeFailureAnalyzer(preset=_DIAG)
    return container


def _seed(store, run_id, cases=None, **kw) -> None:
    _make_run_in_store(store, id=run_id, **kw)
    store._cases[run_id] = cases or []


def _failed_case(name: str = "t"):
    return TestCaseResult(suite="s", name=name, status="failed", duration_ms=1, message="boom")


def _completed_summary(passed: int, failed: int) -> TestSummary:
    return TestSummary(
        total=passed + failed, passed=passed, failed=failed, skipped=0, error=0, duration_ms=1
    )


def _as_user(app, username: str, role: UserRole = UserRole.USER) -> None:
    async def _u() -> User:
        return User(username=username, role=role, created_at=NOW)

    app.dependency_overrides[get_current_user] = _u


# ── _run_stdout_tail helper (4 branches) ─────────────────────────────────────


def test_run_stdout_tail_no_run_dir(monkeypatch):
    monkeypatch.setattr(routes, "_safe_run_artifact_dir", lambda *a: None)
    assert routes._run_stdout_tail("root", "id", 100) == ""


def test_run_stdout_tail_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_safe_run_artifact_dir", lambda *a: tmp_path)
    assert routes._run_stdout_tail(str(tmp_path), "id", 100) == ""


def test_run_stdout_tail_reads_and_trims(tmp_path, monkeypatch):
    (tmp_path / "stdout.log").write_text("X" * 500)
    monkeypatch.setattr(routes, "_safe_run_artifact_dir", lambda *a: tmp_path)
    assert len(routes._run_stdout_tail(str(tmp_path), "id", 100)) == 100


def test_run_stdout_tail_read_returns_none(tmp_path, monkeypatch):
    (tmp_path / "stdout.log").write_text("x")
    monkeypatch.setattr(routes, "_safe_run_artifact_dir", lambda *a: tmp_path)
    monkeypatch.setattr(routes, "_read_log_tail", lambda p: None)
    assert routes._run_stdout_tail(str(tmp_path), "id", 100) == ""


# ── GET /runs/{id}/ai-analysis ───────────────────────────────────────────────


def test_get_ai_analysis_404():
    app = create_app(_with_ai())
    with TestClient(app) as client:
        assert client.get("/runs/nope/ai-analysis").status_code == 404


def test_get_ai_analysis_enabled_without_cache():
    container = _with_ai()
    _seed(container.store, "run-g")
    app = create_app(container)
    with TestClient(app) as client:
        body = client.get("/runs/run-g/ai-analysis").json()
    assert body["enabled"] is True
    assert body["diagnosis"] is None


def test_get_ai_analysis_disabled_when_no_provider():
    container = _make_container()  # ai_analyzer stays None
    _seed(container.store, "run-g2")
    app = create_app(container)
    with TestClient(app) as client:
        assert client.get("/runs/run-g2/ai-analysis").json()["enabled"] is False


def test_get_ai_analysis_returns_cached():
    container = _with_ai()
    _seed(container.store, "run-c")
    container.store._ai_diagnoses["run-c"] = _DIAG
    app = create_app(container)
    with TestClient(app) as client:
        body = client.get("/runs/run-c/ai-analysis").json()
    assert body["diagnosis"]["category"] == "assertion"


def test_get_ai_analysis_owner_scope_403():
    container = _with_ai()
    _seed(container.store, "run-o", created_by="alice")
    app = create_app(container)
    _as_user(app, "bob")
    with TestClient(app) as client:
        assert client.get("/runs/run-o/ai-analysis").status_code == 403


# ── POST /runs/{id}/ai-analysis ──────────────────────────────────────────────


def test_post_ai_analysis_404():
    app = create_app(_with_ai())
    with TestClient(app) as client:
        assert client.post("/runs/nope/ai-analysis").status_code == 404


def test_post_ai_analysis_disabled():
    container = _make_container()  # None
    _seed(container.store, "run-d")
    app = create_app(container)
    with TestClient(app) as client:
        assert client.post("/runs/run-d/ai-analysis").json()["enabled"] is False


def test_post_ai_analysis_owner_scope_403():
    container = _with_ai()
    _seed(container.store, "run-x9", created_by="alice")
    app = create_app(container)
    _as_user(app, "bob")
    with TestClient(app) as client:
        assert client.post("/runs/run-x9/ai-analysis").status_code == 403


def test_post_ai_analysis_no_failing_cases():
    container = _with_ai()
    _seed(
        container.store,
        "run-nf",
        cases=[TestCaseResult(suite="s", name="ok", status="passed", duration_ms=1)],
    )
    app = create_app(container)
    with TestClient(app) as client:
        body = client.post("/runs/run-nf/ai-analysis").json()
    assert body["enabled"] is True
    assert body["diagnosis"] is None
    assert "No failing" in body["detail"]


def test_post_ai_analysis_generates_and_caches():
    analyzer = FakeFailureAnalyzer(preset=_DIAG)
    container = _with_ai(analyzer)
    _seed(container.store, "run-p", tests_path="api", cases=[_failed_case()])
    app = create_app(container)
    with TestClient(app) as client:
        body = client.post("/runs/run-p/ai-analysis").json()
    assert body["diagnosis"]["category"] == "assertion"
    assert container.store._ai_diagnoses["run-p"] is _DIAG  # cached
    assert len(analyzer.calls) == 1
    assert analyzer.calls[0].failed_cases[0].name == "t"


def test_post_ai_analysis_non_admin_owner_scope():
    analyzer = FakeFailureAnalyzer(preset=_DIAG)
    container = _with_ai(analyzer)
    _seed(
        container.store,
        "run-bo",
        created_by="bob",
        status=RunStatus.COMPLETED,
        tests_path="b",
        cases=[_failed_case()],
    )
    app = create_app(container)
    _as_user(app, "bob")
    with TestClient(app) as client:
        assert client.post("/runs/run-bo/ai-analysis").status_code == 200


def test_post_ai_analysis_selects_baseline():
    analyzer = FakeFailureAnalyzer(preset=_DIAG)
    container = _with_ai(analyzer)
    store = container.store
    _seed(
        store,
        "base",
        status=RunStatus.COMPLETED,
        tests_path="api",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        summary=_completed_summary(1, 0),
        cases=[TestCaseResult(suite="s", name="t", status="passed", duration_ms=1)],
    )
    _seed(
        store,
        "head",
        status=RunStatus.COMPLETED,
        tests_path="api",
        created_at=datetime(2025, 6, 1, tzinfo=UTC),
        summary=_completed_summary(0, 1),
        cases=[_failed_case()],
    )
    app = create_app(container)
    with TestClient(app) as client:
        assert client.post("/runs/head/ai-analysis").status_code == 200
    ctx = analyzer.calls[0]
    assert ctx.baseline_diff is not None
    assert len(ctx.baseline_diff.new_failures) == 1  # base passed → head failed


def test_post_ai_analysis_marks_flaky_case():
    analyzer = FakeFailureAnalyzer(preset=_DIAG)
    container = _with_ai(analyzer)
    store = container.store
    for i, st in enumerate(["passed", "failed", "passed", "failed"]):
        _seed(
            store,
            f"h{i}",
            status=RunStatus.COMPLETED,
            tests_path="fl",
            created_at=datetime(2025, 1, i + 1, tzinfo=UTC),
            summary=_completed_summary(1, 0) if st == "passed" else _completed_summary(0, 1),
            cases=[TestCaseResult(suite="s", name="t", status=st, duration_ms=1)],
        )
    _seed(
        store,
        "fh",
        status=RunStatus.COMPLETED,
        tests_path="fl",
        created_at=datetime(2025, 1, 10, tzinfo=UTC),
        summary=_completed_summary(0, 1),
        cases=[_failed_case()],
    )
    app = create_app(container)
    with TestClient(app) as client:
        assert client.post("/runs/fh/ai-analysis").status_code == 200
    assert ("s", "t") in analyzer.calls[0].flaky_identities


def test_post_ai_analysis_includes_allure_url():
    analyzer = FakeFailureAnalyzer(preset=_DIAG)
    container = _with_ai(analyzer)
    _seed(
        container.store,
        "run-ar",
        status=RunStatus.COMPLETED,
        tests_path="a",
        report=ReportRef(
            allure_results_dir="/x", allure_report_file="index.html", html_generated=True
        ),
        cases=[_failed_case()],
    )
    app = create_app(container)
    with TestClient(app) as client:
        client.post("/runs/run-ar/ai-analysis")
    assert analyzer.calls[0].allure_report_url == "/runs/run-ar/report"
