"""Adapter tests for Playwright result loading (M5, T-M5-PLAYWRIGHT-ADAPTER-001)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qarunner.adapters.playwright_result_adapter import load_validated_playwright_result
from qarunner.domain import CaseOutcome, PlaywrightResultAdapterRejected
from qarunner.domain.playwright_execution_result import PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION


def _write_result(tmp_path: Path, payload: dict) -> Path:
    (tmp_path / "case-results.json").write_text(json.dumps(payload))
    return tmp_path


def _valid_payload(*, cases: list[dict] | None = None) -> dict:
    return {
        "schema_version": PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
        "cases": cases
        if cases is not None
        else [
            {
                "stable_case_id": "chromium::tests/a.spec.ts::test a",
                "project": "chromium",
                "file": "tests/a.spec.ts",
                "title": "test a",
                "outcome": "passed",
                "duration_ms": 100,
                "message": None,
            }
        ],
    }


def test_load_validated_playwright_result_round_trips(tmp_path: Path) -> None:
    _write_result(tmp_path, _valid_payload())
    loaded = load_validated_playwright_result(str(tmp_path), exit_code=0)
    assert loaded is not None
    cases, summary = loaded
    assert len(cases) == 1
    assert cases[0].outcome is CaseOutcome.PASSED
    assert cases[0].stable_case_id == "chromium::tests/a.spec.ts::test a"
    assert summary.expected == 1
    assert summary.passed == 1


def test_load_returns_none_when_absent(tmp_path: Path) -> None:
    assert load_validated_playwright_result(str(tmp_path), exit_code=0) is None


def test_load_rejects_oversized(tmp_path: Path) -> None:
    huge = _valid_payload()
    huge["cases"][0]["message"] = "x" * (11 * 1024 * 1024)
    _write_result(tmp_path, huge)
    with pytest.raises(PlaywrightResultAdapterRejected, match="exceeds"):
        load_validated_playwright_result(str(tmp_path), exit_code=0)


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "case-results.json").write_text("not json")
    with pytest.raises(PlaywrightResultAdapterRejected, match="not valid JSON"):
        load_validated_playwright_result(str(tmp_path), exit_code=0)


def test_load_rejects_non_dict_root(tmp_path: Path) -> None:
    (tmp_path / "case-results.json").write_text("[]")
    with pytest.raises(PlaywrightResultAdapterRejected, match="object"):
        load_validated_playwright_result(str(tmp_path), exit_code=0)


def test_load_rejects_non_array_cases(tmp_path: Path) -> None:
    _write_result(
        tmp_path, {"schema_version": PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION, "cases": "bad"}
    )
    with pytest.raises(PlaywrightResultAdapterRejected, match="array"):
        load_validated_playwright_result(str(tmp_path), exit_code=0)
