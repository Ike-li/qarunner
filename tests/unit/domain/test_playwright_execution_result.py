"""T-M5-PLAYWRIGHT-ADAPTER-001: canonical per-case Playwright execution results.

Mirrors M4 pytest adapter: sandbox produces schema-bounded case-results.json;
control plane accepts only that JSON (never raw junit/HTML/trace tooling).
Stable Case ID is project::file::title; locator kind is playwright_test.
"""

from __future__ import annotations

import pytest

from qarunner.domain import (
    ArtifactPath,
    CaseOutcome,
    DomainValidationError,
    PlaywrightResultAdapterRejected,
    accept_playwright_execution_result,
    canonical_digest,
    playwright_stable_case_id,
    playwright_test_locator,
)
from qarunner.domain.playwright_execution_result import PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION

_SOURCE_PATH = ArtifactPath("case-results.json")
_SOURCE_DIGEST = canonical_digest(schema_version="qep.artifact-content.v1", payload={"x": 1})


def _raw_case(
    *,
    project: str = "chromium",
    file: str = "tests/shop.spec.ts",
    title: str = "shop checkout",
    outcome: str = "passed",
    duration_ms: int = 10,
    message: str | None = None,
    stable_case_id: str | None = None,
) -> dict:
    sid = stable_case_id or playwright_stable_case_id(project=project, file=file, title=title)
    return {
        "stable_case_id": sid,
        "project": project,
        "file": file,
        "title": title,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "message": message,
    }


# ── locator / stable id ──────────────────────────────────────────────────────


def test_playwright_stable_case_id_joins_project_file_title() -> None:
    assert (
        playwright_stable_case_id(project="chromium", file="tests/a.spec.ts", title="does thing")
        == "chromium::tests/a.spec.ts::does thing"
    )


def test_playwright_test_locator_parts() -> None:
    locator = playwright_test_locator(
        project="chromium", file="tests/a.spec.ts", title="does thing"
    )
    assert locator.kind == "playwright_test"
    assert dict(locator.parts) == {
        "project": "chromium",
        "file": "tests/a.spec.ts",
        "title": "does thing",
    }
    assert locator.schema_version == "qep.playwright-locator.v1"


def test_playwright_test_locator_rejects_blank_parts() -> None:
    with pytest.raises(DomainValidationError):
        playwright_test_locator(project=" ", file="f", title="t")
    with pytest.raises(DomainValidationError):
        playwright_test_locator(project="p", file="", title="t")
    with pytest.raises(DomainValidationError):
        playwright_test_locator(project="p", file="f", title="  ")


# ── accept ───────────────────────────────────────────────────────────────────


def test_accept_mixed_outcomes_builds_summary() -> None:
    cases, summary = accept_playwright_execution_result(
        schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
        raw_cases=(
            _raw_case(title="pass", outcome="passed"),
            _raw_case(title="fail", outcome="failed", message="expect(1).toBe(2)"),
            _raw_case(title="skip", outcome="skipped"),
            _raw_case(title="boom", outcome="error", message="setup crashed"),
        ),
        exit_code=1,
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
    )
    assert len(cases) == 4
    by_title = {dict(c.framework_locator.parts)["title"]: c for c in cases}
    assert by_title["pass"].outcome is CaseOutcome.PASSED
    assert by_title["fail"].outcome is CaseOutcome.FAILED
    assert by_title["skip"].outcome is CaseOutcome.SKIPPED
    assert by_title["boom"].outcome is CaseOutcome.ERROR
    assert summary.expected == 4
    assert summary.passed == 1
    assert summary.failed == 2  # failed + error
    assert summary.skipped == 1
    assert summary.schema_version == PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION


def test_accept_rejects_wrong_schema_and_duplicates() -> None:
    with pytest.raises(PlaywrightResultAdapterRejected) as schema:
        accept_playwright_execution_result(
            schema_version="qep.pytest-case-result.v1",
            raw_cases=(_raw_case(),),
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )
    assert "schema_version" in schema.value.reason

    with pytest.raises(PlaywrightResultAdapterRejected) as dup:
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=(_raw_case(), _raw_case()),
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )
    assert "duplicate" in dup.value.reason


def test_accept_rejects_stable_id_mismatch_with_parts() -> None:
    raw = _raw_case(stable_case_id="wrong-id")
    with pytest.raises(PlaywrightResultAdapterRejected) as caught:
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=(raw,),
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )
    assert "stable_case_id" in caught.value.reason


def test_accept_rejects_malformed_entries() -> None:
    with pytest.raises(PlaywrightResultAdapterRejected):
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=({"outcome": "passed"},),
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )
    with pytest.raises(PlaywrightResultAdapterRejected):
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=(_raw_case(outcome="flaky"),),
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )
    bad = _raw_case()
    bad["duration_ms"] = -1
    with pytest.raises(PlaywrightResultAdapterRejected):
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=(bad,),
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_empty_cases_only_allowed_on_exit_zero() -> None:
    cases, summary = accept_playwright_execution_result(
        schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
        raw_cases=(),
        exit_code=0,
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
    )
    assert cases == ()
    assert summary.expected == 0

    with pytest.raises(PlaywrightResultAdapterRejected):
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=(),
            exit_code=1,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_rejects_bool_exit_code() -> None:
    with pytest.raises(PlaywrightResultAdapterRejected, match="exit_code"):
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=(_raw_case(),),
            exit_code=True,  # type: ignore[arg-type]
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_parse_rejects_blank_project_or_file_or_title() -> None:
    for field, val in [("project", " "), ("file", ""), ("title", "  ")]:
        bad = _raw_case()
        bad[field] = val
        with pytest.raises(PlaywrightResultAdapterRejected, match=field):
            accept_playwright_execution_result(
                schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
                raw_cases=(bad,),
                exit_code=0,
                source_artifact_path=_SOURCE_PATH,
                source_artifact_digest=_SOURCE_DIGEST,
            )


def test_parse_rejects_non_string_message() -> None:
    bad = _raw_case(message=123)  # type: ignore[arg-type]
    with pytest.raises(PlaywrightResultAdapterRejected, match="message"):
        accept_playwright_execution_result(
            schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
            raw_cases=(bad,),
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_stable_case_id_rejects_blank_parts() -> None:
    with pytest.raises(DomainValidationError):
        playwright_stable_case_id(project=" ", file="f", title="t")
    with pytest.raises(DomainValidationError):
        playwright_stable_case_id(project="p", file="", title="t")
    with pytest.raises(DomainValidationError):
        playwright_stable_case_id(project="p", file="f", title="  ")
