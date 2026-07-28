"""T-M4-RESULT-001: canonical pytest attempt result classification.

Maps platform-owned process facts + the schema-bounded case-results
aggregate into a single Attempt-level class:

  passed | test_failed | infra_failed | cancelled | unknown

Rules must stay aligned with ``evidence._classify_outcome`` so a later
Evidence finalize of the same facts cannot disagree with the Worker-boundary
classification (platform infra/timeout dominates user case content; cancelled
requires an explicit cancel signal; completed requires a validated case
summary).
"""

from __future__ import annotations

import pytest

from qarunner.domain import (
    ArtifactPath,
    PlatformExitClass,
    PytestAttemptResultClass,
    ValidatedCaseSummary,
    canonical_digest,
    classify_pytest_attempt_result,
)

_SOURCE_PATH = ArtifactPath("case-results.json")
_SOURCE_DIGEST = canonical_digest(schema_version="qep.artifact-content.v1", payload={"x": 1})


def _summary(*, expected: int, passed: int, failed: int, skipped: int = 0) -> ValidatedCaseSummary:
    return ValidatedCaseSummary(
        schema_version="qep.pytest-case-result.v1",
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
        expected=expected,
        passed=passed,
        failed=failed,
        skipped=skipped,
        not_reported=0,
        unexpected=0,
    )


# ── Priority: cancel / infra process facts ───────────────────────────────────


def test_cancelled_signal_classifies_as_cancelled_even_with_passing_summary() -> None:
    """Control-plane cancel dominates any user-controlled case content."""
    result = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=True,
        case_summary=_summary(expected=1, passed=1, failed=0),
    )
    assert result.result_class is PytestAttemptResultClass.CANCELLED
    assert result.platform_exit_class is PlatformExitClass.CANCELLED
    assert result.timeout is False
    assert result.oom is False


def test_hard_timeout_classifies_as_infra_failed_even_with_passing_summary() -> None:
    result = classify_pytest_attempt_result(
        exit_code=-1,
        timed_out=True,
        cancelled=False,
        case_summary=_summary(expected=1, passed=1, failed=0),
    )
    assert result.result_class is PytestAttemptResultClass.INFRA_FAILED
    assert result.platform_exit_class is PlatformExitClass.INFRA_FAILED
    assert result.timeout is True


def test_oom_classifies_as_infra_failed() -> None:
    result = classify_pytest_attempt_result(
        exit_code=137,
        timed_out=False,
        oom=True,
        cancelled=False,
        case_summary=None,
    )
    assert result.result_class is PytestAttemptResultClass.INFRA_FAILED
    assert result.platform_exit_class is PlatformExitClass.INFRA_FAILED
    assert result.oom is True


def test_cancelled_wins_over_timeout_when_both_observed() -> None:
    """If the control plane already decided to cancel, the explainable terminal
    class is cancelled — not a race between timeout and cancel."""
    result = classify_pytest_attempt_result(
        exit_code=-1,
        timed_out=True,
        cancelled=True,
        case_summary=None,
    )
    assert result.result_class is PytestAttemptResultClass.CANCELLED
    assert result.platform_exit_class is PlatformExitClass.CANCELLED


# ── Missing / incomplete case results ────────────────────────────────────────


def test_missing_case_summary_after_process_exit_is_infra_failed() -> None:
    """Plugin never wrote case-results.json → process-side infra (or crash
    before sessionfinish), not a test_failed and not silent pass."""
    result = classify_pytest_attempt_result(
        exit_code=1,
        timed_out=False,
        cancelled=False,
        case_summary=None,
    )
    assert result.result_class is PytestAttemptResultClass.INFRA_FAILED
    assert result.platform_exit_class is PlatformExitClass.INFRA_FAILED


def test_missing_exit_code_without_cancel_or_timeout_is_unknown() -> None:
    """No observed process end and no cancel/timeout signal → cannot classify
    as any terminal platform class; control plane must enter the unknown path."""
    result = classify_pytest_attempt_result(
        exit_code=None,
        timed_out=False,
        cancelled=False,
        case_summary=None,
    )
    assert result.result_class is PytestAttemptResultClass.UNKNOWN
    # Unknown does not invent a COMPLETED platform exit.
    assert result.platform_exit_class is PlatformExitClass.INFRA_FAILED
    assert result.requires_unknown_observation is True


# ── Completed process + validated case summary ───────────────────────────────


def test_exit_zero_with_no_failures_is_passed() -> None:
    result = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(expected=2, passed=2, failed=0),
    )
    assert result.result_class is PytestAttemptResultClass.PASSED
    assert result.platform_exit_class is PlatformExitClass.COMPLETED
    assert result.requires_unknown_observation is False


def test_nonzero_exit_with_case_failures_is_test_failed() -> None:
    result = classify_pytest_attempt_result(
        exit_code=1,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(expected=2, passed=1, failed=1),
    )
    assert result.result_class is PytestAttemptResultClass.TEST_FAILED
    assert result.platform_exit_class is PlatformExitClass.COMPLETED


def test_nonzero_exit_with_zero_case_failures_is_infra_failed() -> None:
    """pytest usage/internal/collection errors (exit 2/3/4/5-with-empty already
    filtered by the adapter) with no failing cases are platform/infra, not
    test_failed — user content cannot rebrand them as assertions."""
    result = classify_pytest_attempt_result(
        exit_code=2,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(expected=0, passed=0, failed=0),
    )
    assert result.result_class is PytestAttemptResultClass.INFRA_FAILED
    assert result.platform_exit_class is PlatformExitClass.INFRA_FAILED


def test_exit_zero_with_case_failures_is_unknown_contradiction() -> None:
    """Process claims success while the canonical summary reports failures —
    neither test_failed (exit contradicts) nor passed; force unknown review."""
    result = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(expected=1, passed=0, failed=1),
    )
    assert result.result_class is PytestAttemptResultClass.UNKNOWN
    assert result.requires_unknown_observation is True


def test_incomplete_case_summary_is_unknown() -> None:
    """not_reported / unexpected leave the denominator incomplete → unknown,
    never silently passed/test_failed."""
    incomplete = ValidatedCaseSummary(
        schema_version="qep.pytest-case-result.v1",
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
        expected=2,
        passed=1,
        failed=0,
        skipped=0,
        not_reported=1,
        unexpected=0,
    )
    result = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=False,
        case_summary=incomplete,
    )
    assert result.result_class is PytestAttemptResultClass.UNKNOWN
    assert result.requires_unknown_observation is True


# ── Input validation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"exit_code": True, "timed_out": False, "cancelled": False, "case_summary": None},
        {"exit_code": 0, "timed_out": "no", "cancelled": False, "case_summary": None},
        {"exit_code": 0, "timed_out": False, "cancelled": 1, "case_summary": None},
        {
            "exit_code": 0,
            "timed_out": False,
            "cancelled": False,
            "oom": "yes",
            "case_summary": None,
        },
        {"exit_code": 0, "timed_out": False, "cancelled": False, "case_summary": object()},
    ],
)
def test_classifier_rejects_untyped_inputs(kwargs) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError):
        classify_pytest_attempt_result(**kwargs)
