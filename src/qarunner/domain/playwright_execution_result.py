"""Canonical per-case Playwright execution results (M5, T-M5-PLAYWRIGHT-ADAPTER-001).

Sandbox-side reporter writes schema-bounded ``case-results.json``; the control
plane accepts only that JSON — never raw junit/HTML/trace tooling (mirrors
T-M4-PYTEST-ADAPTER-001's zero control-plane report parsing).

Stable Case ID is ``project::file::title`` so it is unique across Playwright
projects; the framework locator kind is ``playwright_test`` with the same
three parts (collection already requires project/file/title).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from qarunner.domain.digest import Digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.evidence import ArtifactPath, ValidatedCaseSummary
from qarunner.domain.manifest import FrameworkLocator
from qarunner.domain.pytest_execution_result import CaseOutcome, ValidatedCaseResult

PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION = "qep.playwright-case-result.v1"
_PLAYWRIGHT_LOCATOR_SCHEMA = "qep.playwright-locator.v1"


class PlaywrightResultAdapterRejected(ValueError):
    """Raised when a sandbox-produced Playwright case-results.json is malformed."""

    code = "playwright_result_adapter_rejected"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"playwright result adapter output rejected: {reason}")


def playwright_stable_case_id(*, project: str, file: str, title: str) -> str:
    """Single shared composition for Playwright Case identity."""
    for field, value in (("project", project), ("file", file), ("title", title)):
        if not isinstance(value, str) or not value.strip():
            raise DomainValidationError(
                entity_type="playwright_case_id", field=field, reason="invalid"
            )
    return f"{project.strip()}::{file.strip()}::{title.strip()}"


def playwright_test_locator(*, project: str, file: str, title: str) -> FrameworkLocator:
    """Build the collection/execution-shared ``playwright_test`` locator."""
    for field, value in (("project", project), ("file", file), ("title", title)):
        if not isinstance(value, str) or not value.strip():
            raise DomainValidationError(
                entity_type="playwright_test_locator", field=field, reason="invalid"
            )
    return FrameworkLocator(
        schema_version=_PLAYWRIGHT_LOCATOR_SCHEMA,
        kind="playwright_test",
        parts=(
            ("project", project.strip()),
            ("file", file.strip()),
            ("title", title.strip()),
        ),
    )


def accept_playwright_execution_result(
    *,
    schema_version: str,
    raw_cases: Sequence[Mapping[str, object]],
    exit_code: int,
    source_artifact_path: ArtifactPath,
    source_artifact_digest: Digest,
) -> tuple[tuple[ValidatedCaseResult, ...], ValidatedCaseSummary]:
    """Validate sandbox-produced Playwright ``case-results.json``.

    Rejects wrong schema, malformed entries, duplicate stable ids, and
    stable_case_id that does not match project/file/title. Empty case lists
    are only allowed on exit_code 0 (vacuous pass — no tests scheduled).
    ERROR folds into the aggregate failed bucket like the pytest path.
    """
    if schema_version != PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION:
        raise PlaywrightResultAdapterRejected(
            reason=f"unsupported schema_version {schema_version!r}"
        )
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise PlaywrightResultAdapterRejected(reason="exit_code must be an integer")

    if not raw_cases:
        if exit_code != 0:
            raise PlaywrightResultAdapterRejected(
                reason="empty case list without a successful (0) exit code"
            )
        return (), _summary(source_artifact_path, source_artifact_digest, 0, 0, 0, 0)

    seen_ids: set[str] = set()
    cases: list[ValidatedCaseResult] = []
    passed = failed = skipped = 0
    for raw in raw_cases:
        case = _parse_raw_case(raw)
        if case.stable_case_id in seen_ids:
            raise PlaywrightResultAdapterRejected(
                reason=f"duplicate stable_case_id {case.stable_case_id!r}"
            )
        seen_ids.add(case.stable_case_id)
        cases.append(case)
        if case.outcome is CaseOutcome.PASSED:
            passed += 1
        elif case.outcome is CaseOutcome.SKIPPED:
            skipped += 1
        else:
            failed += 1

    summary = _summary(
        source_artifact_path, source_artifact_digest, len(cases), passed, failed, skipped
    )
    return tuple(cases), summary


def _summary(
    source_artifact_path: ArtifactPath,
    source_artifact_digest: Digest,
    expected: int,
    passed: int,
    failed: int,
    skipped: int,
) -> ValidatedCaseSummary:
    return ValidatedCaseSummary(
        schema_version=PLAYWRIGHT_CASE_RESULT_SCHEMA_VERSION,
        source_artifact_path=source_artifact_path,
        source_artifact_digest=source_artifact_digest,
        expected=expected,
        passed=passed,
        failed=failed,
        skipped=skipped,
        not_reported=0,
        unexpected=0,
    )


def _parse_raw_case(raw: Mapping[str, object]) -> ValidatedCaseResult:
    try:
        stable_case_id = raw["stable_case_id"]
        project = raw["project"]
        file_part = raw["file"]
        title = raw["title"]
        outcome_value = raw["outcome"]
        duration_ms = raw["duration_ms"]
        message = raw.get("message")
    except (KeyError, TypeError) as exc:
        raise PlaywrightResultAdapterRejected(reason=f"malformed case entry: {exc}") from None
    try:
        outcome = CaseOutcome(outcome_value)
    except ValueError:
        raise PlaywrightResultAdapterRejected(
            reason=f"invalid outcome {outcome_value!r}"
        ) from None
    for field, value in (
        ("stable_case_id", stable_case_id),
        ("project", project),
        ("file", file_part),
        ("title", title),
    ):
        if not isinstance(value, str) or not value.strip():
            raise PlaywrightResultAdapterRejected(reason=f"{field} must be a non-empty string")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise PlaywrightResultAdapterRejected(reason="duration_ms must be a non-negative integer")
    if message is not None and not isinstance(message, str):
        raise PlaywrightResultAdapterRejected(reason="message must be a string or null")

    expected_id = playwright_stable_case_id(project=project, file=file_part, title=title)
    if stable_case_id.strip() != expected_id:
        raise PlaywrightResultAdapterRejected(
            reason=(
                f"stable_case_id {stable_case_id!r} does not match "
                f"project/file/title composition {expected_id!r}"
            )
        )
    return ValidatedCaseResult(
        stable_case_id=expected_id,
        framework_locator=playwright_test_locator(project=project, file=file_part, title=title),
        outcome=outcome,
        duration_ms=duration_ms,
        message=message,
    )
