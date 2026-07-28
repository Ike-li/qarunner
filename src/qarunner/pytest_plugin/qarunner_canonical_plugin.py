"""qarunner canonical pytest result plugin (M4, T-M4-PYTEST-ADAPTER-001).

Baked into the executor image as a standalone file (see ``Dockerfile``) and
loaded via ``-p qarunner_canonical_plugin`` with ``PYTHONPATH`` pointing at its
install location — not injected per-shard alongside the copied test source.
This matters: ``-p <modname>`` resolves via a plain ``sys.path`` import, and
pytest invoked as a bare ``pytest ...`` (vs. ``python -m pytest ...``) does
*not* put the current directory on ``sys.path`` the way ``-m`` does, so a
per-shard-injected file at the workspace root could silently fail to import
depending on invocation form. Baking it into the image with a fixed
``PYTHONPATH`` entry sidesteps that, and also avoids sharing a directory with
(and colliding on the same module name as) the user's own copied source.

This file must not import anything outside the standard library: the
executor image has no ``qarunner`` package installed, only pytest itself.

Writes a small, schema-bounded ``case-results.json`` directly from pytest's
own hook data — ``report.nodeid`` is pytest's authoritative case identity,
never reconstructed from JUnit XML's ``classname``/``name`` attributes, which
is lossy for nested modules and parametrization. ``junit.xml`` (still produced
separately via ``--junitxml=``) stays pure archival/Allure-compat content;
the control plane parses *only* this bounded JSON, never raw junit.xml
(T-M4-PYTEST-ADAPTER-001's "控制面零执行" — zero control-plane execution/
parsing of untrusted report tooling).
"""

from __future__ import annotations

import json
import os

CASE_RESULT_SCHEMA_VERSION = "qep.pytest-case-result.v1"

# Matches the platform's own defensive-parsing philosophy (core/junit.py caps
# a whole junit.xml at 10 MiB) — this bounds one case's captured failure text
# so a single enormous traceback can't blow up the canonical JSON.
_MAX_MESSAGE_CHARS = 20_000

_ENV_RESULTS_PATH = "QARUNNER_CASE_RESULTS_PATH"

_PASSED = "passed"
_FAILED = "failed"
_SKIPPED = "skipped"
_ERROR = "error"


def _truncate(message: str | None) -> str | None:
    if message is None:
        return None
    if len(message) <= _MAX_MESSAGE_CHARS:
        return message
    return message[:_MAX_MESSAGE_CHARS] + "...[truncated]"


class _CaseAccumulator:
    """Folds a test's 1-3 reports (setup/call/teardown) into one outcome.

    A teardown failure escalates over an already-recorded call outcome —
    matching pytest's own terminal-reporting convention, where a passed call
    with a failing teardown is still surfaced as an error, not silently
    dropped. xfail/xpass fold into SKIPPED/PASSED respectively: a documented
    v1 simplification, not a silent omission — a finer-grained xfail-aware
    outcome can be a later refinement.
    """

    def __init__(self) -> None:
        self._order: list[str] = []
        self._entries: dict[str, dict[str, object]] = {}

    def add_report(self, report: object) -> None:
        nodeid = report.nodeid  # type: ignore[attr-defined]
        entry = self._entries.get(nodeid)
        if entry is None:
            entry = {"outcome": _PASSED, "duration_s": 0.0, "message": None}
            self._entries[nodeid] = entry
            self._order.append(nodeid)
        entry["duration_s"] = float(entry["duration_s"]) + float(
            getattr(report, "duration", 0.0) or 0.0
        )

        if report.passed:  # type: ignore[attr-defined]
            return  # a plain pass never downgrades an already-recorded failure/error
        if report.skipped:  # type: ignore[attr-defined]
            if entry["outcome"] == _PASSED:
                entry["outcome"] = _SKIPPED
            return
        # pytest's own TestReport.passed/failed/skipped are always mutually
        # exclusive (exactly one true); having fallen through both checks
        # above, failed is structurally guaranteed true here.
        if report.failed:  # type: ignore[attr-defined]  # pragma: no branch
            when = getattr(report, "when", "call")
            escalated = _ERROR if when in ("setup", "teardown") else _FAILED
            if when == "teardown" or entry["outcome"] == _PASSED:
                entry["outcome"] = escalated
            if entry["message"] is None:
                entry["message"] = _truncate(getattr(report, "longreprtext", None))

    def build_cases(self) -> list[dict[str, object]]:
        cases = []
        for nodeid in self._order:
            entry = self._entries[nodeid]
            cases.append(
                {
                    "stable_case_id": nodeid,
                    "outcome": entry["outcome"],
                    "duration_ms": int(round(float(entry["duration_s"]) * 1000)),
                    "message": entry["message"],
                }
            )
        return cases


_accumulator = _CaseAccumulator()


def pytest_runtest_logreport(report: object) -> None:
    _accumulator.add_report(report)


def pytest_sessionfinish(session: object, exitstatus: object) -> None:
    results_path = os.environ.get(_ENV_RESULTS_PATH)
    if not results_path:
        return  # not wired up to emit canonical results for this invocation
    payload = {
        "schema_version": CASE_RESULT_SCHEMA_VERSION,
        "cases": _accumulator.build_cases(),
    }
    os.makedirs(os.path.dirname(results_path), exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
