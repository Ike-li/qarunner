"""Tests for qarunner.models — drives Step 2 implementation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError


class TestRunStatus:
    """RunStatus enum should expose the four terminal/queue states."""

    def test_has_queued(self):
        from qarunner.models import RunStatus

        assert RunStatus.QUEUED == "queued"

    def test_has_running(self):
        from qarunner.models import RunStatus

        assert RunStatus.RUNNING == "running"

    def test_has_completed(self):
        from qarunner.models import RunStatus

        assert RunStatus.COMPLETED == "completed"

    def test_has_failed(self):
        from qarunner.models import RunStatus

        assert RunStatus.FAILED == "failed"

    def test_has_timeout(self):
        from qarunner.models import RunStatus

        assert RunStatus.TIMEOUT == "timeout"


class TestRunModel:
    """Run is a frozen Pydantic model representing a single test execution."""

    def test_create_minimal_run(self):
        from qarunner.models import Run, RunStatus

        now = datetime.now(tz=UTC)
        run = Run(
            id="abc-123",
            status=RunStatus.QUEUED,
            runner="pytest",
            created_by="test_user",
            tests_path="sample_tests",
            created_at=now,
        )
        assert run.id == "abc-123"
        assert run.status == RunStatus.QUEUED
        assert run.summary is None
        assert run.report is None
        assert run.exit_code is None
        assert run.error is None
        assert run.started_at is None
        assert run.finished_at is None

    def test_run_is_frozen(self):
        from qarunner.models import Run, RunStatus

        now = datetime.now(tz=UTC)
        run = Run(
            id="abc-123",
            status=RunStatus.QUEUED,
            runner="pytest",
            created_by="test_user",
            tests_path="sample_tests",
            created_at=now,
        )
        with pytest.raises(ValidationError):
            run.id = "changed"


class TestTestSummary:
    """TestSummary holds aggregate counts from a test run."""

    def test_pass_rate(self):
        from qarunner.models import TestSummary

        summary = TestSummary(
            total=10,
            passed=8,
            failed=1,
            skipped=1,
            error=0,
            duration_ms=5000,
        )
        assert summary.pass_rate == pytest.approx(0.8)

    def test_pass_rate_all_passing(self):
        from qarunner.models import TestSummary

        summary = TestSummary(
            total=5,
            passed=5,
            failed=0,
            skipped=0,
            error=0,
            duration_ms=1000,
        )
        assert summary.pass_rate == pytest.approx(1.0)

    def test_pass_rate_zero_total(self):
        from qarunner.models import TestSummary

        summary = TestSummary(
            total=0,
            passed=0,
            failed=0,
            skipped=0,
            error=0,
            duration_ms=0,
        )
        assert summary.pass_rate == pytest.approx(0.0)


class TestProcessResult:
    """ProcessResult wraps subprocess execution output."""

    def test_timed_out_default_false(self):
        from qarunner.models import ProcessResult

        result = ProcessResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_ms=100,
        )
        assert result.timed_out is False


class TestCollectResult:
    """CollectResult groups a TestSummary with individual case results."""

    def test_create_collect_result(self):
        from qarunner.models import CollectResult, TestCaseResult, TestSummary

        summary = TestSummary(
            total=1,
            passed=1,
            failed=0,
            skipped=0,
            error=0,
            duration_ms=50,
        )
        case = TestCaseResult(
            suite="test_foo",
            name="test_bar",
            status="passed",
            duration_ms=50,
        )
        result = CollectResult(summary=summary, cases=[case])
        assert len(result.cases) == 1
        assert result.cases[0].name == "test_bar"


class TestReportRef:
    """ReportRef tracks allure output locations."""

    def test_html_not_generated_by_default(self):
        from qarunner.models import ReportRef

        ref = ReportRef(
            allure_results_dir="/tmp/results",
        )
        assert ref.html_generated is False
        assert ref.allure_report_file is None


class TestPytestWarningSuppression:
    """Model classes starting with 'Test' set __test__ = False to avoid Pytest
    collection warnings."""

    def test_pytest_collection_suppressed(self):
        from qarunner.models import TestProfile, TestSchedule, TestSummary

        assert getattr(TestProfile, "__test__", None) is False
        assert getattr(TestSchedule, "__test__", None) is False
        assert getattr(TestSummary, "__test__", None) is False


class TestRunRequestValidation:
    """RunRequest bounds untrusted API input at the boundary (P2-5/P2-6)."""

    def test_rejects_zero_timeout(self):
        from qarunner.models import RunRequest

        with pytest.raises(ValidationError):
            RunRequest(tests_path="t", timeout=0)

    def test_rejects_negative_timeout(self):
        from qarunner.models import RunRequest

        with pytest.raises(ValidationError):
            RunRequest(tests_path="t", timeout=-5)

    def test_rejects_oversized_timeout(self):
        from qarunner.models import MAX_TIMEOUT_SECONDS, RunRequest

        with pytest.raises(ValidationError):
            RunRequest(tests_path="t", timeout=MAX_TIMEOUT_SECONDS + 1)

    def test_accepts_none_and_in_range_timeout(self):
        from qarunner.models import MAX_TIMEOUT_SECONDS, RunRequest

        cap = MAX_TIMEOUT_SECONDS
        assert RunRequest(tests_path="t").timeout is None
        assert RunRequest(tests_path="t", timeout=1).timeout == 1
        assert RunRequest(tests_path="t", timeout=cap).timeout == cap

    @pytest.mark.parametrize(
        "tests_path",
        [
            "suiteA/../victimSuite",
            "..",
            "../victimSuite",
            "suiteA/../../victimSuite",
            "suiteA/..",
            "suiteA\\..\\victimSuite",  # backslash form, same segment once normalised
        ],
    )
    def test_rejects_tests_path_with_traversal_segment(self, tests_path: str):
        """A '..' segment lets the owner-scope check (which only inspects the
        first path component) and the actual execution path (which resolves
        the full string) disagree about which suite is being touched —
        letting a user run tests against, and read the results of, a suite
        they don't own. Reject it outright at the API boundary."""
        from qarunner.models import RunRequest

        with pytest.raises(ValidationError, match="must not contain"):
            RunRequest(tests_path=tests_path)

    def test_accepts_tests_path_without_traversal(self):
        from qarunner.models import RunRequest

        assert RunRequest(tests_path="suiteA/sub/dir").tests_path == "suiteA/sub/dir"

    def test_defaults_executor_mode_to_docker(self):
        from qarunner.models import RunRequest

        assert RunRequest(tests_path="t").executor_mode == "docker"
