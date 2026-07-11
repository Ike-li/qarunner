"""Tests for qarunner.core.regression — cross-run baseline diff (stage 2).

Two contracts:
- ``diff(base_cases, head_cases)``: identity is ``(suite, name)``; a case
  "fails" iff status is ``failed``/``error`` (``passed``/``skipped`` are
  non-failures — the regression view cares about red, not coverage).
- ``select_baseline(head, candidates)``: most recent COMPLETED run preceding
  *head* with the same execution scope (profile_id + tests_path + runner + args).
"""

from datetime import datetime, timedelta

from qarunner.core.regression import diff, select_baseline
from qarunner.models import RegressionDiff, Run, RunStatus, TestCaseResult


def _case(name, status, suite="suite", message=None):
    return TestCaseResult(suite=suite, name=name, status=status, duration_ms=0, message=message)


_T0 = datetime(2026, 6, 1, 12, 0, 0)


def _run(
    run_id,
    *,
    status=RunStatus.COMPLETED,
    runner="pytest",
    tests_path="suite",
    args=None,
    minutes=0,
    profile_id=None,
):
    return Run(
        id=run_id,
        status=status,
        runner=runner,
        created_by="u",
        tests_path=tests_path,
        args=list(args or []),
        created_at=_T0 + timedelta(minutes=minutes),
        profile_id=profile_id,
    )


class TestDiff:
    def test_empty_baseline_makes_every_head_case_new(self):
        result = diff([], [_case("a", "passed"), _case("b", "failed")])
        assert {c.name for c in result.new_cases} == {"a", "b"}
        assert result.new_failures == []
        assert result.fixed == []
        assert result.still_failing == []
        assert result.removed_cases == []

    def test_pass_to_fail_is_new_failure(self):
        result = diff([_case("a", "passed")], [_case("a", "failed")])
        assert [c.name for c in result.new_failures] == ["a"]
        assert result.fixed == []
        assert result.still_failing == []

    def test_fail_to_pass_is_fixed(self):
        result = diff([_case("a", "failed")], [_case("a", "passed")])
        assert [c.name for c in result.fixed] == ["a"]
        assert result.new_failures == []

    def test_fail_to_fail_is_still_failing(self):
        result = diff([_case("a", "failed")], [_case("a", "failed")])
        assert [c.name for c in result.still_failing] == ["a"]
        assert result.new_failures == []
        assert result.fixed == []

    def test_pass_to_pass_falls_in_no_bucket(self):
        result = diff([_case("a", "passed")], [_case("a", "passed")])
        assert result.new_failures == []
        assert result.fixed == []
        assert result.still_failing == []
        assert result.new_cases == []
        assert result.removed_cases == []

    def test_case_only_in_baseline_is_removed(self):
        result = diff([_case("gone", "passed")], [])
        assert [c.name for c in result.removed_cases] == ["gone"]
        assert result.new_cases == []

    def test_error_status_counts_as_failure(self):
        # base passed → head error ⇒ new failure (error is a fail)
        result = diff([_case("a", "passed")], [_case("a", "error")])
        assert [c.name for c in result.new_failures] == ["a"]

    def test_skipped_counts_as_non_failure(self):
        # base failed → head skipped ⇒ fixed (skip is not a failure)
        fixed = diff([_case("a", "failed")], [_case("a", "skipped")]).fixed
        assert [c.name for c in fixed] == ["a"]
        # base passed → head skipped ⇒ no bucket
        r = diff([_case("b", "passed")], [_case("b", "skipped")])
        assert r.new_failures == [] and r.fixed == [] and r.still_failing == []

    def test_identity_is_suite_plus_name(self):
        # same name, different suite ⇒ different identity, not a regression
        result = diff(
            [_case("t", "passed", suite="suiteA")],
            [_case("t", "failed", suite="suiteB")],
        )
        assert [(c.suite, c.name) for c in result.new_cases] == [("suiteB", "t")]
        assert [(c.suite, c.name) for c in result.removed_cases] == [("suiteA", "t")]
        assert result.new_failures == []

    def test_new_failure_carries_head_message(self):
        result = diff(
            [_case("a", "passed")],
            [_case("a", "failed", message="AssertionError: boom")],
        )
        assert result.new_failures[0].message == "AssertionError: boom"

    def test_returns_regression_diff_model(self):
        assert isinstance(diff([], []), RegressionDiff)

    def test_duplicate_identity_within_one_run_uses_last_occurrence(self):
        """BUG-27: retry plugins (e.g. pytest-rerunfailures) can emit more than
        one <testcase> for the same (suite, name) in a single run's junit.xml —
        one element per attempt, in attempt order. diff() must not raise or
        pick arbitrarily: the *last* occurrence (the final retry's outcome,
        since parse_junit_xml preserves document order) determines the case's
        status, matching what a human reading "did this test ultimately pass"
        would expect — an early failed attempt followed by a passing retry is
        not a new_failure."""
        head_cases = [
            _case("flaky", "failed", message="attempt 1"),
            _case("flaky", "passed", message="attempt 2 (retry)"),
        ]
        result = diff([_case("flaky", "passed")], head_cases)
        assert result.new_failures == []
        assert result.fixed == []
        assert result.still_failing == []

        # And the reverse: a passing first attempt followed by a failing
        # retry is what actually gets reported as failing (the run's own
        # summary/exit status reflects the last attempt too).
        head_cases_reversed = [
            _case("flaky", "passed", message="attempt 1"),
            _case("flaky", "failed", message="attempt 2 (retry)"),
        ]
        result2 = diff([_case("flaky", "passed")], head_cases_reversed)
        assert [c.name for c in result2.new_failures] == ["flaky"]
        assert result2.new_failures[0].message == "attempt 2 (retry)"


class TestSelectBaseline:
    def test_no_candidates_returns_none(self):
        assert select_baseline(_run("h", minutes=10), []) is None

    def test_only_self_returns_none(self):
        head = _run("h", minutes=10)
        assert select_baseline(head, [head]) is None

    def test_picks_earlier_completed_same_scope(self):
        head = _run("h", minutes=10)
        base = _run("b", minutes=5)
        assert select_baseline(head, [head, base]).id == "b"

    def test_picks_most_recent_of_several(self):
        head = _run("h", minutes=10)
        candidates = [head, _run("old", minutes=1), _run("recent", minutes=8)]
        assert select_baseline(head, candidates).id == "recent"

    def test_excludes_different_args(self):
        head = _run("h", args=["-k", "x"], minutes=10)
        other = _run("b", args=["-k", "y"], minutes=5)
        assert select_baseline(head, [head, other]) is None

    def test_excludes_different_runner(self):
        head = _run("h", runner="pytest", minutes=10)
        other = _run("b", runner="playwright", minutes=5)
        assert select_baseline(head, [head, other]) is None

    def test_excludes_different_tests_path(self):
        head = _run("h", tests_path="suiteA", minutes=10)
        other = _run("b", tests_path="suiteB", minutes=5)
        assert select_baseline(head, [head, other]) is None

    def test_excludes_different_profile(self):
        head = _run("h", profile_id="profile-a", minutes=10)
        other = _run("b", profile_id="profile-b", minutes=5)
        assert select_baseline(head, [head, other]) is None

    def test_excludes_non_completed(self):
        head = _run("h", minutes=10)
        failed = _run("b", status=RunStatus.FAILED, minutes=5)
        assert select_baseline(head, [head, failed]) is None

    def test_excludes_later_run(self):
        head = _run("h", minutes=10)
        later = _run("b", minutes=15)
        assert select_baseline(head, [head, later]) is None

    def test_same_args_list_equality_matches(self):
        head = _run("h", args=["-k", "smoke"], minutes=10)
        base = _run("b", args=["-k", "smoke"], minutes=5)
        assert select_baseline(head, [head, base]).id == "b"

    def test_picks_comparable_among_noise(self):
        head = _run("h", args=["-m", "fast"], minutes=10)
        candidates = [
            head,
            _run("later", args=["-m", "fast"], minutes=20),
            _run("diffscope", args=["-m", "slow"], minutes=5),
            _run("failed", status=RunStatus.FAILED, args=["-m", "fast"], minutes=6),
            _run("good", args=["-m", "fast"], minutes=8),
        ]
        assert select_baseline(head, candidates).id == "good"
