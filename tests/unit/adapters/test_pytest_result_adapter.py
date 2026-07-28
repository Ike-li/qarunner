"""T-M4-PYTEST-ADAPTER-001: load_validated_pytest_result adapter behavior."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qarunner.adapters.pytest_result_adapter import load_validated_pytest_result
from qarunner.domain.pytest_execution_result import (
    CASE_RESULT_SCHEMA_VERSION,
    CaseOutcome,
    PytestResultAdapterRejected,
)

_NO_TESTS_COLLECTED_EXIT_CODE = 5


def _write_results(results_dir: Path, payload: dict | str | bytes) -> Path:
    path = results_dir / "case-results.json"
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_bytes(payload)
    return path


def _valid_payload() -> dict:
    return {
        "schema_version": CASE_RESULT_SCHEMA_VERSION,
        "cases": [
            {
                "stable_case_id": "test_mod.py::test_a",
                "outcome": "passed",
                "duration_ms": 12,
                "message": None,
            },
            {
                "stable_case_id": "test_mod.py::test_b",
                "outcome": "failed",
                "duration_ms": 34,
                "message": "assert False",
            },
        ],
    }


def test_missing_directory_returns_none(tmp_path: Path) -> None:
    result = load_validated_pytest_result(str(tmp_path / "never-created"), exit_code=0)
    assert result is None


def test_missing_file_returns_none(tmp_path: Path) -> None:
    result = load_validated_pytest_result(str(tmp_path), exit_code=0)
    assert result is None


def test_oversized_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "case-results.json"
    path.write_bytes(b"0" * (10 * 1024 * 1024 + 1))

    with pytest.raises(PytestResultAdapterRejected, match="exceeds"):
        load_validated_pytest_result(str(tmp_path), exit_code=0)


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    _write_results(tmp_path, "{not valid json")

    with pytest.raises(PytestResultAdapterRejected, match="not valid JSON"):
        load_validated_pytest_result(str(tmp_path), exit_code=0)


def test_non_object_root_is_rejected(tmp_path: Path) -> None:
    _write_results(tmp_path, "[]")

    with pytest.raises(PytestResultAdapterRejected, match="must be an object"):
        load_validated_pytest_result(str(tmp_path), exit_code=0)


def test_cases_not_a_list_is_rejected(tmp_path: Path) -> None:
    _write_results(tmp_path, {"schema_version": CASE_RESULT_SCHEMA_VERSION, "cases": "nope"})

    with pytest.raises(PytestResultAdapterRejected, match="'cases' must be an array"):
        load_validated_pytest_result(str(tmp_path), exit_code=0)


def test_rejection_from_domain_layer_propagates(tmp_path: Path) -> None:
    _write_results(tmp_path, {"schema_version": "wrong.version", "cases": []})

    with pytest.raises(PytestResultAdapterRejected, match="unsupported schema_version"):
        load_validated_pytest_result(str(tmp_path), exit_code=_NO_TESTS_COLLECTED_EXIT_CODE)


def test_empty_cases_with_no_tests_collected_exit_code_is_accepted(tmp_path: Path) -> None:
    _write_results(tmp_path, {"schema_version": CASE_RESULT_SCHEMA_VERSION, "cases": []})

    result = load_validated_pytest_result(str(tmp_path), exit_code=_NO_TESTS_COLLECTED_EXIT_CODE)

    assert result is not None
    cases, summary = result
    assert cases == ()
    assert summary.expected == 0


def test_valid_payload_returns_validated_cases_and_locally_recomputed_digest(
    tmp_path: Path,
) -> None:
    path = _write_results(tmp_path, _valid_payload())
    raw_bytes = path.read_bytes()
    expected_digest = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"

    result = load_validated_pytest_result(str(tmp_path), exit_code=1)

    assert result is not None
    cases, summary = result
    assert [c.stable_case_id for c in cases] == [
        "test_mod.py::test_a",
        "test_mod.py::test_b",
    ]
    assert cases[0].outcome is CaseOutcome.PASSED
    assert cases[1].outcome is CaseOutcome.FAILED
    assert summary.source_artifact_digest.value == expected_digest
    assert summary.source_artifact_path.value == "case-results.json"
    assert summary.expected == 2
    assert summary.passed == 1
    assert summary.failed == 1
