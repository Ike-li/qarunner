"""T-M4-PYTEST-ADAPTER-001: the pytest plugin that runs *inside* the sandbox.

Two distinct test styles are deliberately combined in this one file:

1. Direct, in-process calls into ``_CaseAccumulator``/``_truncate``/the hook
   functions — these are what actually satisfy the 100% coverage gate.
   ``Pytester.run()`` (below) spawns a genuinely separate subprocess, and the
   host coverage.py instance has no visibility into code executed there (no
   ``COVERAGE_PROCESS_START`` subprocess hook is wired up, deliberately: that
   would be a global test-infra change out of proportion to one small
   module) — a subprocess-only test suite would silently show this file at
   0% and fail the gate despite being fully behaviorally tested.
2. Pytest's own ``pytester`` fixture — the standard, documented mechanism for
   testing pytest plugins — to run a real, separate pytest process (matching
   production: the plugin always runs in a genuinely fresh process, one per
   container exec, so its module-level accumulator is never reused across
   runs) against small fixture suites, with the plugin loaded exactly as
   production loads it: ``-p qarunner.pytest_plugin.qarunner_canonical_plugin``.
   This is the real proof that hook wiring/report semantics match production;
   the direct tests above alone couldn't catch a mismatch against pytest's
   actual report objects.

``Pytester.run()`` spawns a real subprocess that inherits the current
process's environment (it copies ``os.environ`` and only adds/extends
``PYTHONPATH``) and runs with the current working directory already chdir'd
into ``pytester.path`` by the fixture itself — so ``QARUNNER_CASE_RESULTS_PATH``
is set via ``monkeypatch.setenv`` in *this* process, not passed as an
unsupported ``env=``/``cwd=`` kwarg to ``run()``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qarunner.pytest_plugin import qarunner_canonical_plugin as plugin

pytest_plugins = ["pytester"]

_PLUGIN = "qarunner.pytest_plugin.qarunner_canonical_plugin"


def _read_results(results_path: Path) -> dict:
    return json.loads(results_path.read_text())


def _fake_report(
    nodeid: str,
    *,
    when: str = "call",
    passed: bool = False,
    failed: bool = False,
    skipped: bool = False,
    duration: float = 0.1,
    longreprtext: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        nodeid=nodeid,
        when=when,
        duration=duration,
        passed=passed,
        failed=failed,
        skipped=skipped,
        longreprtext=longreprtext,
    )


class TestTruncate:
    def test_none_passes_through(self) -> None:
        assert plugin._truncate(None) is None

    def test_short_message_passes_through_unchanged(self) -> None:
        assert plugin._truncate("boom") == "boom"

    def test_oversized_message_gets_truncated_with_marker(self) -> None:
        message = plugin._truncate("x" * 30_000)
        assert message is not None
        assert len(message) < 30_000
        assert message.endswith("...[truncated]")


class TestCaseAccumulator:
    def test_single_pass(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", passed=True))
        (case,) = acc.build_cases()
        assert case == {
            "stable_case_id": "t.py::test_a",
            "outcome": "passed",
            "duration_ms": 100,
            "message": None,
        }

    def test_call_failure_records_message(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", failed=True, longreprtext="assert False"))
        (case,) = acc.build_cases()
        assert case["outcome"] == "failed"
        assert case["message"] == "assert False"

    def test_setup_failure_is_reported_as_error(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", when="setup", failed=True))
        (case,) = acc.build_cases()
        assert case["outcome"] == "error"

    def test_teardown_failure_escalates_over_passed_call(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", when="call", passed=True))
        acc.add_report(_fake_report("t.py::test_a", when="teardown", failed=True))
        (case,) = acc.build_cases()
        assert case["outcome"] == "error"

    def test_teardown_failure_escalates_even_over_an_existing_failure(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", when="call", failed=True))
        acc.add_report(_fake_report("t.py::test_a", when="teardown", failed=True))
        (case,) = acc.build_cases()
        assert case["outcome"] == "error"

    def test_second_failure_does_not_overwrite_first_message(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(
            _fake_report("t.py::test_a", when="call", failed=True, longreprtext="first")
        )
        acc.add_report(
            _fake_report("t.py::test_a", when="teardown", failed=True, longreprtext="second")
        )
        (case,) = acc.build_cases()
        assert case["message"] == "first"

    def test_a_second_call_failure_does_not_re_escalate_an_existing_failure(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", when="call", failed=True))
        acc.add_report(_fake_report("t.py::test_a", when="call", failed=True))
        (case,) = acc.build_cases()
        assert case["outcome"] == "failed"

    def test_skip_after_nothing_marks_skipped(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", skipped=True))
        (case,) = acc.build_cases()
        assert case["outcome"] == "skipped"

    def test_skip_after_failure_does_not_downgrade(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", when="call", failed=True))
        acc.add_report(_fake_report("t.py::test_a", when="teardown", skipped=True))
        (case,) = acc.build_cases()
        assert case["outcome"] == "failed"

    def test_pass_after_failure_does_not_erase_failure(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", when="call", failed=True))
        acc.add_report(_fake_report("t.py::test_a", when="teardown", passed=True))
        (case,) = acc.build_cases()
        assert case["outcome"] == "failed"

    def test_duplicate_nodeid_sums_duration_across_phases(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_a", when="setup", passed=True, duration=0.05))
        acc.add_report(_fake_report("t.py::test_a", when="call", passed=True, duration=0.10))
        (case,) = acc.build_cases()
        assert case["duration_ms"] == 150

    def test_two_distinct_nodeids_preserve_first_seen_order(self) -> None:
        acc = plugin._CaseAccumulator()
        acc.add_report(_fake_report("t.py::test_b", passed=True))
        acc.add_report(_fake_report("t.py::test_a", passed=True))
        cases = acc.build_cases()
        assert [c["stable_case_id"] for c in cases] == ["t.py::test_b", "t.py::test_a"]


def test_pytest_runtest_logreport_delegates_to_module_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = plugin._CaseAccumulator()
    monkeypatch.setattr(plugin, "_accumulator", fresh)

    plugin.pytest_runtest_logreport(_fake_report("t.py::test_a", passed=True))

    (case,) = fresh.build_cases()
    assert case["stable_case_id"] == "t.py::test_a"


def test_pytest_sessionfinish_writes_payload_when_path_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = plugin._CaseAccumulator()
    fresh.add_report(_fake_report("t.py::test_a", passed=True))
    monkeypatch.setattr(plugin, "_accumulator", fresh)
    results_path = tmp_path / "nested" / "case-results.json"
    monkeypatch.setenv(plugin._ENV_RESULTS_PATH, str(results_path))

    plugin.pytest_sessionfinish(session=None, exitstatus=0)

    payload = _read_results(results_path)
    assert payload["schema_version"] == plugin.CASE_RESULT_SCHEMA_VERSION
    assert payload["cases"][0]["stable_case_id"] == "t.py::test_a"


def test_pytest_sessionfinish_is_a_noop_without_env_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(plugin._ENV_RESULTS_PATH, raising=False)

    plugin.pytest_sessionfinish(session=None, exitstatus=0)

    assert list(tmp_path.iterdir()) == []


def test_mixed_outcome_suite_produces_expected_canonical_cases(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makepyfile(
        test_mixed="""
        import pytest

        def test_pass():
            assert True

        def test_fail():
            assert False

        def test_skip():
            pytest.skip("nope")

        @pytest.fixture
        def broken_fixture():
            raise RuntimeError("setup boom")

        def test_setup_error(broken_fixture):
            assert True

        @pytest.mark.xfail(reason="known broken")
        def test_expected_fail():
            assert False

        @pytest.mark.xfail(reason="unexpectedly fixed")
        def test_unexpected_pass():
            assert True
        """
    )
    results_path = tmp_path / "case-results.json"
    monkeypatch.setenv("QARUNNER_CASE_RESULTS_PATH", str(results_path))

    result = pytester.run("python", "-m", "pytest", "-p", _PLUGIN)

    result.assert_outcomes(passed=1, failed=1, skipped=1, errors=1, xfailed=1, xpassed=1)
    payload = _read_results(results_path)
    assert payload["schema_version"] == "qep.pytest-case-result.v1"
    by_id = {c["stable_case_id"]: c for c in payload["cases"]}
    assert len(by_id) == 6
    assert by_id["test_mixed.py::test_pass"]["outcome"] == "passed"
    assert by_id["test_mixed.py::test_fail"]["outcome"] == "failed"
    assert by_id["test_mixed.py::test_fail"]["message"]
    assert by_id["test_mixed.py::test_skip"]["outcome"] == "skipped"
    assert by_id["test_mixed.py::test_setup_error"]["outcome"] == "error"
    # xfail/xpass fold into SKIPPED/PASSED (documented v1 simplification).
    assert by_id["test_mixed.py::test_expected_fail"]["outcome"] == "skipped"
    assert by_id["test_mixed.py::test_unexpected_pass"]["outcome"] == "passed"


def test_passing_call_with_failing_teardown_is_reported_as_error(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A teardown failure escalates over an already-recorded PASSED call
    outcome — matches pytest's own terminal-reporting convention."""
    pytester.makepyfile(
        test_teardown="""
        import pytest

        @pytest.fixture
        def cleanup_fails():
            yield
            raise RuntimeError("teardown boom")

        def test_body_passes_but_teardown_fails(cleanup_fails):
            assert True
        """
    )
    results_path = tmp_path / "case-results.json"
    monkeypatch.setenv("QARUNNER_CASE_RESULTS_PATH", str(results_path))

    pytester.run("python", "-m", "pytest", "-p", _PLUGIN)

    payload = _read_results(results_path)
    (case,) = payload["cases"]
    assert case["outcome"] == "error"


def test_zero_collected_cases_still_writes_a_valid_empty_payload(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makepyfile(test_empty="")
    results_path = tmp_path / "case-results.json"
    monkeypatch.setenv("QARUNNER_CASE_RESULTS_PATH", str(results_path))

    result = pytester.run(
        "python", "-m", "pytest", "-p", _PLUGIN, "-k", "nonexistent_marker_selects_nothing"
    )

    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    payload = _read_results(results_path)
    assert payload["cases"] == []


def test_oversized_message_is_truncated(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makepyfile(
        test_huge="""
        def test_huge_failure():
            assert False, "x" * 100_000
        """
    )
    results_path = tmp_path / "case-results.json"
    monkeypatch.setenv("QARUNNER_CASE_RESULTS_PATH", str(results_path))

    pytester.run("python", "-m", "pytest", "-p", _PLUGIN)

    payload = _read_results(results_path)
    (case,) = payload["cases"]
    assert len(case["message"]) < 100_000
    assert case["message"].endswith("...[truncated]")


def test_no_results_path_configured_is_a_silent_noop(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the caller never set QARUNNER_CASE_RESULTS_PATH, the plugin must not
    error out a real pytest run that has nothing to do with qarunner."""
    pytester.makepyfile(
        test_plain="""
        def test_pass():
            assert True
        """
    )
    monkeypatch.delenv("QARUNNER_CASE_RESULTS_PATH", raising=False)

    result = pytester.run("python", "-m", "pytest", "-p", _PLUGIN)

    result.assert_outcomes(passed=1)
