"""T-M4-PYTEST-ADAPTER-001: canonical per-case pytest execution results.

Turns a sandbox-produced, schema-bounded case-results.json payload into
validated per-case results plus the existing aggregate ValidatedCaseSummary —
the only form the control plane is ever allowed to parse (raw junit.xml stays
opaque archival content, parsed only inside the sandbox by
qarunner.pytest_plugin.qarunner_canonical_plugin).
"""

from __future__ import annotations

import pytest

from qarunner.domain import (
    ArtifactClass,
    ArtifactPath,
    ArtifactValidationError,
    CaseOutcome,
    DeclaredArtifact,
    DomainValidationError,
    EvidenceNotReady,
    PlatformExitClass,
    PytestResultAdapterRejected,
    TrustedExitFacts,
    ValidatedCaseResult,
    WorkerRef,
    accept_pytest_execution_result,
    build_evidence_manifest,
    canonical_digest,
    pytest_nodeid_locator,
    verify_declared_artifact,
)
from qarunner.domain.pytest_execution_result import CASE_RESULT_SCHEMA_VERSION

_SOURCE_PATH = ArtifactPath("case-results.json")
_SOURCE_DIGEST = canonical_digest(schema_version="qep.artifact-content.v1", payload={"x": 1})


def _raw_case(
    stable_case_id: str = "tests/test_a.py::test_x",
    *,
    outcome: str = "passed",
    duration_ms: int = 10,
    message: str | None = None,
) -> dict:
    return {
        "stable_case_id": stable_case_id,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "message": message,
    }


# ── pytest_nodeid_locator ────────────────────────────────────────────────────


def test_pytest_nodeid_locator_splits_file_and_node() -> None:
    locator = pytest_nodeid_locator("tests/test_a.py::TestFoo::test_bar")
    assert locator.kind == "pytest_nodeid"
    assert dict(locator.parts) == {"file": "tests/test_a.py", "node": "TestFoo::test_bar"}


def test_pytest_nodeid_locator_preserves_double_colon_inside_parametrize_id() -> None:
    """Partition on the FIRST '::' only — a parametrize id that itself
    contains '::' must stay intact inside the node part, not get mis-split."""
    nodeid = "tests/test_a.py::test_x[a::b]"
    locator = pytest_nodeid_locator(nodeid)
    assert dict(locator.parts) == {"file": "tests/test_a.py", "node": "test_x[a::b]"}


def test_pytest_nodeid_locator_falls_back_to_full_id_when_no_separator() -> None:
    locator = pytest_nodeid_locator("just_a_module_no_separator")
    assert dict(locator.parts) == {
        "file": "just_a_module_no_separator",
        "node": "just_a_module_no_separator",
    }


def test_pytest_nodeid_locator_rejects_empty() -> None:
    with pytest.raises(DomainValidationError):
        pytest_nodeid_locator("")


# ── ValidatedCaseResult ──────────────────────────────────────────────────────


def test_validated_case_result_rejects_empty_stable_case_id() -> None:
    with pytest.raises(DomainValidationError):
        ValidatedCaseResult(
            stable_case_id="",
            framework_locator=pytest_nodeid_locator("x::y"),
            outcome=CaseOutcome.PASSED,
            duration_ms=0,
            message=None,
        )


def test_validated_case_result_rejects_negative_duration() -> None:
    with pytest.raises(DomainValidationError):
        ValidatedCaseResult(
            stable_case_id="x::y",
            framework_locator=pytest_nodeid_locator("x::y"),
            outcome=CaseOutcome.PASSED,
            duration_ms=-1,
            message=None,
        )


def test_validated_case_result_rejects_non_locator_framework_locator() -> None:
    with pytest.raises(DomainValidationError):
        ValidatedCaseResult(
            stable_case_id="x::y",
            framework_locator="not-a-locator",  # type: ignore[arg-type]
            outcome=CaseOutcome.PASSED,
            duration_ms=0,
            message=None,
        )


def test_validated_case_result_rejects_non_enum_outcome() -> None:
    with pytest.raises(DomainValidationError):
        ValidatedCaseResult(
            stable_case_id="x::y",
            framework_locator=pytest_nodeid_locator("x::y"),
            outcome="passed",  # type: ignore[arg-type]
            duration_ms=0,
            message=None,
        )


def test_validated_case_result_rejects_non_string_message() -> None:
    with pytest.raises(DomainValidationError):
        ValidatedCaseResult(
            stable_case_id="x::y",
            framework_locator=pytest_nodeid_locator("x::y"),
            outcome=CaseOutcome.PASSED,
            duration_ms=0,
            message=123,  # type: ignore[arg-type]
        )


# ── accept_pytest_execution_result ──────────────────────────────────────────


def test_accept_pytest_execution_result_builds_cases_and_aggregate() -> None:
    raw_cases = [
        _raw_case("tests/test_a.py::test_pass", outcome="passed", duration_ms=5),
        _raw_case("tests/test_a.py::test_fail", outcome="failed", duration_ms=6, message="boom"),
        _raw_case("tests/test_a.py::test_skip", outcome="skipped", duration_ms=0),
        _raw_case(
            "tests/test_a.py::test_error", outcome="error", duration_ms=1, message="setup boom"
        ),
    ]

    cases, summary = accept_pytest_execution_result(
        schema_version=CASE_RESULT_SCHEMA_VERSION,
        raw_cases=raw_cases,
        exit_code=1,
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
    )

    assert len(cases) == 4
    assert {c.stable_case_id for c in cases} == {
        "tests/test_a.py::test_pass",
        "tests/test_a.py::test_fail",
        "tests/test_a.py::test_skip",
        "tests/test_a.py::test_error",
    }
    assert cases[1].outcome is CaseOutcome.FAILED
    assert cases[1].message == "boom"
    assert cases[3].outcome is CaseOutcome.ERROR

    # ERROR folds into the aggregate's `failed` bucket (an explicit, stated rule).
    assert summary.expected == 4
    assert summary.passed == 1
    assert summary.failed == 2
    assert summary.skipped == 1
    assert summary.not_reported == 0
    assert summary.unexpected == 0
    assert summary.source_artifact_path == _SOURCE_PATH
    assert summary.source_artifact_digest == _SOURCE_DIGEST


def test_accept_pytest_execution_result_rejects_wrong_schema_version() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="schema_version"):
        accept_pytest_execution_result(
            schema_version="qep.pytest-case-result.v999",
            raw_cases=[_raw_case()],
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_rejects_duplicate_stable_case_id() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="duplicate"):
        accept_pytest_execution_result(
            schema_version=CASE_RESULT_SCHEMA_VERSION,
            raw_cases=[_raw_case("x::y"), _raw_case("x::y")],
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_rejects_invalid_outcome() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="outcome"):
        accept_pytest_execution_result(
            schema_version=CASE_RESULT_SCHEMA_VERSION,
            raw_cases=[_raw_case(outcome="not_a_real_outcome")],
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_rejects_negative_duration() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="duration_ms"):
        accept_pytest_execution_result(
            schema_version=CASE_RESULT_SCHEMA_VERSION,
            raw_cases=[_raw_case(duration_ms=-5)],
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_rejects_malformed_case_entry() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="malformed"):
        accept_pytest_execution_result(
            schema_version=CASE_RESULT_SCHEMA_VERSION,
            raw_cases=[{"outcome": "passed"}],  # missing stable_case_id/duration_ms
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_rejects_empty_stable_case_id_in_raw_case() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="stable_case_id"):
        accept_pytest_execution_result(
            schema_version=CASE_RESULT_SCHEMA_VERSION,
            raw_cases=[_raw_case("   ")],
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_rejects_non_string_message_in_raw_case() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="message"):
        accept_pytest_execution_result(
            schema_version=CASE_RESULT_SCHEMA_VERSION,
            raw_cases=[_raw_case(message=123)],  # type: ignore[arg-type]
            exit_code=0,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_rejects_empty_without_no_tests_collected_code() -> None:
    with pytest.raises(PytestResultAdapterRejected, match="empty"):
        accept_pytest_execution_result(
            schema_version=CASE_RESULT_SCHEMA_VERSION,
            raw_cases=[],
            exit_code=1,
            source_artifact_path=_SOURCE_PATH,
            source_artifact_digest=_SOURCE_DIGEST,
        )


def test_accept_pytest_execution_result_accepts_empty_with_no_tests_collected_code() -> None:
    cases, summary = accept_pytest_execution_result(
        schema_version=CASE_RESULT_SCHEMA_VERSION,
        raw_cases=[],
        exit_code=5,  # pytest.ExitCode.NO_TESTS_COLLECTED
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
    )
    assert cases == ()
    assert summary.expected == 0
    assert summary.passed == 0


# ── DeclaredArtifact / verify_declared_artifact ─────────────────────────────


def test_declared_artifact_rejects_negative_size() -> None:
    with pytest.raises(ArtifactValidationError):
        DeclaredArtifact(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            size_bytes=-1,
            digest=_SOURCE_DIGEST,
        )


def test_declared_artifact_rejects_non_artifact_path() -> None:
    with pytest.raises(ArtifactValidationError):
        DeclaredArtifact(
            path="not-a-path",  # type: ignore[arg-type]
            content_class=ArtifactClass.STRUCTURED_RESULT,
            size_bytes=1,
            digest=_SOURCE_DIGEST,
        )


def test_declared_artifact_rejects_non_artifact_class() -> None:
    with pytest.raises(ArtifactValidationError):
        DeclaredArtifact(
            path=_SOURCE_PATH,
            content_class="structured_result",  # type: ignore[arg-type]
            size_bytes=1,
            digest=_SOURCE_DIGEST,
        )


def test_declared_artifact_rejects_non_digest() -> None:
    with pytest.raises(ArtifactValidationError):
        DeclaredArtifact(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            size_bytes=1,
            digest="sha256:abc",  # type: ignore[arg-type]
        )


def test_verify_declared_artifact_rejects_non_declared_artifact() -> None:
    with pytest.raises(ArtifactValidationError):
        verify_declared_artifact(
            "not-declared",  # type: ignore[arg-type]
            recomputed_digest=_SOURCE_DIGEST,
            recomputed_size=1,
        )


def test_verify_declared_artifact_promotes_on_match() -> None:
    declared = DeclaredArtifact(
        path=_SOURCE_PATH,
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=128,
        digest=_SOURCE_DIGEST,
    )
    verified = verify_declared_artifact(
        declared, recomputed_digest=_SOURCE_DIGEST, recomputed_size=128
    )
    assert verified.path == _SOURCE_PATH
    assert verified.digest == _SOURCE_DIGEST
    assert verified.size_bytes == 128


def test_verify_declared_artifact_rejects_digest_mismatch() -> None:
    declared = DeclaredArtifact(
        path=_SOURCE_PATH,
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=128,
        digest=_SOURCE_DIGEST,
    )
    other_digest = canonical_digest(schema_version="qep.artifact-content.v1", payload={"x": 2})

    with pytest.raises(ArtifactValidationError):
        verify_declared_artifact(declared, recomputed_digest=other_digest, recomputed_size=128)


def test_verify_declared_artifact_rejects_size_mismatch() -> None:
    declared = DeclaredArtifact(
        path=_SOURCE_PATH,
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=128,
        digest=_SOURCE_DIGEST,
    )

    with pytest.raises(ArtifactValidationError):
        verify_declared_artifact(declared, recomputed_digest=_SOURCE_DIGEST, recomputed_size=64)


# ── integration: interop with the existing Evidence pipeline ────────────────


def test_all_passed_summary_contradicts_nonzero_exit_through_real_evidence_manifest() -> None:
    """The authenticity gap: a canonical result claiming 100% pass must not
    silently win over a platform-trusted nonzero exit code. This is not new
    protection this task adds — it's the EXISTING build_evidence_manifest/
    _classify_outcome cross-check — this test proves accept_pytest_execution_result's
    output correctly interoperates with that existing, already-tested contradiction
    detection, rather than asserting the property only in prose."""
    from qarunner.domain import ArtifactClass as _ArtifactClass
    from qarunner.domain import VerifiedArtifact

    raw_cases = [_raw_case("tests/test_a.py::test_pass", outcome="passed")]
    _, summary = accept_pytest_execution_result(
        schema_version=CASE_RESULT_SCHEMA_VERSION,
        raw_cases=raw_cases,
        exit_code=1,
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
    )
    artifact = VerifiedArtifact(
        path=_SOURCE_PATH,
        content_class=_ArtifactClass.STRUCTURED_RESULT,
        size_bytes=64,
        digest=_SOURCE_DIGEST,
    )
    trusted_exit = TrustedExitFacts(
        source_event_id="event-exit-001",
        pid=731,
        exit_class=PlatformExitClass.COMPLETED,
        exit_code=1,  # nonzero: container-level trusted fact
        signal=None,
        oom=False,
        timeout=False,
    )

    with pytest.raises(EvidenceNotReady) as caught:
        build_evidence_manifest(
            attempt_id="attempt-001",
            run_id="run-001",
            attempt_no=1,
            assignment_id="assignment-001",
            fence=1,
            worker=WorkerRef(worker_id="worker-001", generation=1),
            execution_spec_digest=canonical_digest(
                schema_version="qep.execution-spec.v1", payload={"suite": "x"}
            ),
            trusted_exit=trusted_exit,
            case_summary=summary,  # claims 100% pass (1/1) despite exit_code=1
            artifacts=(artifact,),
        )

    assert caught.value.reason == "process exit and validated case summary are contradictory"
