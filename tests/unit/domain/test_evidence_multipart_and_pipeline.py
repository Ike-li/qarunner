"""T-M4-EVIDENCE-001: chunk digests / finalize errors block terminal success;
retry does not overwrite a prior Attempt's Evidence.

Covers the M4 same-host vertical-slice of the full multi-host Upload Session
protocol (DES §7.3): multi-part declared digests must recompute locally before
an Artifact is verified; root-digest / chunk-digest mismatch leaves the
Attempt non-terminal; a later retry Attempt finalizes independently without
mutating the prior Attempt's stored Evidence or logical paths.
"""

from __future__ import annotations

import pytest

from qarunner.domain import (
    ArtifactClass,
    ArtifactPath,
    ArtifactValidationError,
    AttemptAuthority,
    AttemptState,
    DeclaredArtifact,
    DeclaredArtifactPart,
    Digest,
    EvidenceDigestMismatch,
    EvidenceNotReady,
    EvidenceProposal,
    EvidenceRequirements,
    PlatformExitClass,
    PytestAttemptResultClass,
    ValidatedCaseSummary,
    WorkerRef,
    build_evidence_manifest,
    canonical_digest,
    classify_pytest_attempt_result,
    complete_declared_artifact_parts,
    trusted_exit_facts_from_pytest_result,
    verify_declared_artifact,
)
from qarunner.domain.evidence import EvidenceOutcome

_SOURCE_PATH = ArtifactPath("case-results.json")
_SOURCE_DIGEST = canonical_digest(schema_version="qep.artifact-content.v1", payload={"body": "ok"})


def _digest(label: str) -> Digest:
    return canonical_digest(schema_version="qep.test-evidence-chunk.v1", payload={"label": label})


def _summary(*, failed: int = 0, passed: int = 1) -> ValidatedCaseSummary:
    expected = failed + passed
    return ValidatedCaseSummary(
        schema_version="qep.pytest-case-result.v1",
        source_artifact_path=_SOURCE_PATH,
        source_artifact_digest=_SOURCE_DIGEST,
        expected=expected,
        passed=passed,
        failed=failed,
        skipped=0,
        not_reported=0,
        unexpected=0,
    )


def _authority(*, fence: int = 1, generation: int = 1) -> AttemptAuthority:
    return AttemptAuthority(
        current_fence=fence,
        current_worker=WorkerRef(worker_id="worker-001", generation=generation),
    )


# ── Multi-part complete (chunk digests) ──────────────────────────────────────


def test_multipart_complete_promotes_when_every_part_and_total_recompute() -> None:
    parts = (
        DeclaredArtifactPart(part_no=1, size_bytes=16, digest=_digest("p1")),
        DeclaredArtifactPart(part_no=2, size_bytes=32, digest=_digest("p2")),
    )
    total = _digest("total")
    verified = complete_declared_artifact_parts(
        path=_SOURCE_PATH,
        content_class=ArtifactClass.STRUCTURED_RESULT,
        declared_parts=parts,
        declared_total_digest=total,
        recomputed_parts=(
            (_digest("p1"), 16),
            (_digest("p2"), 32),
        ),
        recomputed_total_digest=total,
    )
    assert verified.path == _SOURCE_PATH
    assert verified.size_bytes == 48
    assert verified.digest == total
    assert verified.content_class is ArtifactClass.STRUCTURED_RESULT


def test_multipart_complete_rejects_noncontiguous_part_numbers() -> None:
    parts = (
        DeclaredArtifactPart(part_no=1, size_bytes=8, digest=_digest("p1")),
        DeclaredArtifactPart(part_no=3, size_bytes=8, digest=_digest("p3")),
    )
    with pytest.raises(ArtifactValidationError) as caught:
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=parts,
            declared_total_digest=_digest("total"),
            recomputed_parts=((_digest("p1"), 8), (_digest("p3"), 8)),
            recomputed_total_digest=_digest("total"),
        )
    assert caught.value.field == "part_no"


def test_multipart_complete_rejects_part_digest_mismatch() -> None:
    parts = (DeclaredArtifactPart(part_no=1, size_bytes=8, digest=_digest("p1")),)
    with pytest.raises(ArtifactValidationError) as caught:
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=parts,
            declared_total_digest=_digest("total"),
            recomputed_parts=((_digest("forged"), 8),),
            recomputed_total_digest=_digest("total"),
        )
    assert caught.value.field == "part_digest"


def test_multipart_complete_rejects_total_digest_mismatch() -> None:
    parts = (DeclaredArtifactPart(part_no=1, size_bytes=8, digest=_digest("p1")),)
    with pytest.raises(ArtifactValidationError) as caught:
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=parts,
            declared_total_digest=_digest("declared-total"),
            recomputed_parts=((_digest("p1"), 8),),
            recomputed_total_digest=_digest("recomputed-total"),
        )
    assert caught.value.field == "total_digest"


def test_multipart_complete_rejects_empty_parts() -> None:
    with pytest.raises(ArtifactValidationError) as caught:
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=(),
            declared_total_digest=_digest("total"),
            recomputed_parts=(),
            recomputed_total_digest=_digest("total"),
        )
    assert caught.value.field == "parts"


def test_multipart_part_rejects_invalid_contract() -> None:
    with pytest.raises(ArtifactValidationError):
        DeclaredArtifactPart(part_no=0, size_bytes=1, digest=_digest("p"))
    with pytest.raises(ArtifactValidationError):
        DeclaredArtifactPart(part_no=1, size_bytes=-1, digest=_digest("p"))
    with pytest.raises(ArtifactValidationError):
        DeclaredArtifactPart(part_no=1, size_bytes=1, digest=object())  # type: ignore[arg-type]


def test_multipart_complete_rejects_untyped_or_mismatched_inputs() -> None:
    part = DeclaredArtifactPart(part_no=1, size_bytes=8, digest=_digest("p1"))
    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=object(),  # type: ignore[arg-type]
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=(part,),
            declared_total_digest=_digest("t"),
            recomputed_parts=((_digest("p1"), 8),),
            recomputed_total_digest=_digest("t"),
        )
    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=object(),  # type: ignore[arg-type]
            declared_parts=(part,),
            declared_total_digest=_digest("t"),
            recomputed_parts=((_digest("p1"), 8),),
            recomputed_total_digest=_digest("t"),
        )
    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=(part,),
            declared_total_digest=object(),  # type: ignore[arg-type]
            recomputed_parts=((_digest("p1"), 8),),
            recomputed_total_digest=_digest("t"),
        )
    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=(object(),),  # type: ignore[arg-type]
            declared_total_digest=_digest("t"),
            recomputed_parts=((_digest("p1"), 8),),
            recomputed_total_digest=_digest("t"),
        )
    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=(part,),
            declared_total_digest=_digest("t"),
            recomputed_parts=object(),  # type: ignore[arg-type]
            recomputed_total_digest=_digest("t"),
        )
    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=(part,),
            declared_total_digest=_digest("t"),
            recomputed_parts=((_digest("p1"), 8), (_digest("extra"), 1)),
            recomputed_total_digest=_digest("t"),
        )
    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=(part,),
            declared_total_digest=_digest("t"),
            recomputed_parts=((object(), 8),),  # type: ignore[arg-type]
            recomputed_total_digest=_digest("t"),
        )


# ── Trusted exit from RESULT classification ──────────────────────────────────


def test_trusted_exit_from_passed_classification_is_completed() -> None:
    classified = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(),
    )
    assert classified.result_class is PytestAttemptResultClass.PASSED
    exit_facts = trusted_exit_facts_from_pytest_result(
        result=classified,
        source_event_id="event-exit-001",
        pid=4242,
    )
    assert exit_facts is not None
    assert exit_facts.exit_class is PlatformExitClass.COMPLETED
    assert exit_facts.exit_code == 0
    assert exit_facts.pid == 4242
    assert exit_facts.timeout is False
    assert exit_facts.oom is False


def test_trusted_exit_from_infra_timeout_carries_timeout_flag() -> None:
    classified = classify_pytest_attempt_result(
        exit_code=-1,
        timed_out=True,
        cancelled=False,
        case_summary=None,
    )
    exit_facts = trusted_exit_facts_from_pytest_result(
        result=classified,
        source_event_id="event-timeout-001",
        pid=None,
    )
    assert exit_facts is not None
    assert exit_facts.exit_class is PlatformExitClass.INFRA_FAILED
    assert exit_facts.timeout is True
    assert exit_facts.pid is None


def test_trusted_exit_from_unknown_classification_is_none() -> None:
    """Unknown must not produce terminal platform exit facts — the control plane
    enters the unknown-observation path instead of Evidence finalize."""
    classified = classify_pytest_attempt_result(
        exit_code=None,
        timed_out=False,
        cancelled=False,
        case_summary=None,
    )
    assert classified.result_class is PytestAttemptResultClass.UNKNOWN
    assert (
        trusted_exit_facts_from_pytest_result(
            result=classified,
            source_event_id="event-unknown-001",
            pid=None,
        )
        is None
    )


def test_trusted_exit_from_cancelled_classification_is_cancelled_exit() -> None:
    classified = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=True,
        case_summary=None,
    )
    exit_facts = trusted_exit_facts_from_pytest_result(
        result=classified,
        source_event_id="event-cancel-001",
        pid=99,
    )
    assert exit_facts is not None
    assert exit_facts.exit_class is PlatformExitClass.CANCELLED
    assert exit_facts.pid == 99


def test_trusted_exit_rejects_untyped_inputs() -> None:
    from qarunner.domain import DomainValidationError

    classified = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(),
    )
    with pytest.raises(DomainValidationError):
        trusted_exit_facts_from_pytest_result(
            result=object(),  # type: ignore[arg-type]
            source_event_id="e",
            pid=1,
        )
    with pytest.raises(DomainValidationError):
        trusted_exit_facts_from_pytest_result(
            result=classified,
            source_event_id="",
            pid=1,
        )
    with pytest.raises(DomainValidationError):
        trusted_exit_facts_from_pytest_result(
            result=classified,
            source_event_id="e",
            pid=0,
        )


# ── End-to-end: digest errors block terminal; retry does not overwrite ───────


def test_chunk_digest_mismatch_blocks_artifact_and_terminal_finalize() -> None:
    """A forged multi-part complete cannot produce a VerifiedArtifact, so
    Evidence finalize never reaches a terminal Attempt state."""
    from tests.unit.domain.test_evidence_finalize import _uploading_attempt

    attempt = _uploading_attempt()
    parts = (DeclaredArtifactPart(part_no=1, size_bytes=8, digest=_digest("real")),)

    with pytest.raises(ArtifactValidationError):
        complete_declared_artifact_parts(
            path=_SOURCE_PATH,
            content_class=ArtifactClass.STRUCTURED_RESULT,
            declared_parts=parts,
            declared_total_digest=_digest("total"),
            recomputed_parts=((_digest("forged"), 8),),
            recomputed_total_digest=_digest("total"),
        )

    # Without a verified artifact the required path is missing → EvidenceNotReady.
    classified = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(),
    )
    exit_facts = trusted_exit_facts_from_pytest_result(
        result=classified,
        source_event_id="event-exit-001",
        pid=1001,
    )
    assert exit_facts is not None
    # Candidate rebuild itself fails when the case-summary source artifact is absent.
    with pytest.raises(EvidenceNotReady):
        build_evidence_manifest(
            attempt_id=attempt.id,
            run_id=attempt.run_id,
            attempt_no=attempt.attempt_no,
            assignment_id=attempt.assignment_id,
            fence=attempt.fence,
            worker=attempt.worker,
            execution_spec_digest=attempt.spec_digest,
            trusted_exit=exit_facts,
            case_summary=_summary(),
            artifacts=(),
        )
    assert attempt.evidence is None
    assert attempt.state is AttemptState.UPLOADING


def test_root_digest_mismatch_blocks_terminal_after_verified_artifacts() -> None:
    from tests.unit.domain.test_evidence_finalize import _uploading_attempt

    attempt = _uploading_attempt()
    declared = DeclaredArtifact(
        path=_SOURCE_PATH,
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=128,
        digest=_SOURCE_DIGEST,
    )
    artifact = verify_declared_artifact(
        declared, recomputed_digest=_SOURCE_DIGEST, recomputed_size=128
    )
    classified = classify_pytest_attempt_result(
        exit_code=0,
        timed_out=False,
        cancelled=False,
        case_summary=_summary(),
    )
    exit_facts = trusted_exit_facts_from_pytest_result(
        result=classified,
        source_event_id="event-exit-001",
        pid=1001,
    )
    assert exit_facts is not None
    candidate = build_evidence_manifest(
        attempt_id=attempt.id,
        run_id=attempt.run_id,
        attempt_no=attempt.attempt_no,
        assignment_id=attempt.assignment_id,
        fence=attempt.fence,
        worker=attempt.worker,
        execution_spec_digest=attempt.spec_digest,
        trusted_exit=exit_facts,
        case_summary=_summary(),
        artifacts=(artifact,),
    )
    forged_proposal = EvidenceProposal(root_digest=_digest("forged-root"))
    with pytest.raises(EvidenceDigestMismatch):
        attempt.finalize_evidence(
            proposal=forged_proposal,
            trusted_exit=exit_facts,
            case_summary=_summary(),
            artifacts=(artifact,),
            requirements=EvidenceRequirements(required_artifact_paths=frozenset({_SOURCE_PATH})),
            authority=_authority(fence=attempt.fence, generation=attempt.worker.generation),
            worker=attempt.worker,
            fence=attempt.fence,
            expected_version=attempt.version,
        )
    assert attempt.evidence is None
    assert attempt.state is AttemptState.UPLOADING
    # Sanity: the correct root would have classified as passed.
    assert candidate.outcome is EvidenceOutcome.PASSED


def test_retry_attempt_finalize_does_not_overwrite_prior_attempt_evidence() -> None:
    """A later Attempt (higher fence / attempt_no) finalizes its own Evidence;
    the prior Attempt's stored Evidence and logical artifact path stay intact."""
    from qarunner.domain import Attempt, RetryProvenance, canonical_digest
    from tests.unit.domain.test_evidence_finalize import (
        _retry_provenance,
        _uploading_attempt,
    )
    from tests.unit.domain.test_evidence_finalize_idempotency import (
        _finalize,
        _valid_finalize_inputs,
    )

    first_attempt = _uploading_attempt()
    first_inputs = _valid_finalize_inputs(first_attempt)
    first = _finalize(first_attempt, first_inputs)
    assert first.attempt.evidence is not None
    first_root = first.evidence.root_digest
    first_paths = tuple(a.path.value for a in first.evidence.artifacts)

    # A real retry Attempt: distinct id/fence/attempt_no, still uploading.
    # Provenance points at the first Attempt as its source.
    source = first.attempt
    retry_prov = RetryProvenance(
        retry_intent_id="retry-002",
        retry_intent_digest=canonical_digest(
            schema_version="qep.test-retry-provenance.v1",
            payload={"label": "retry-002"},
        ),
        source_attempt_id=source.id,
        source_attempt_no=source.attempt_no,
        source_fence=source.fence,
        authority=_retry_provenance().authority,
    )
    retry_committed = Attempt.create(
        attempt_id="attempt-retry-003",
        run_id=source.run_id,
        attempt_no=source.attempt_no + 1,
        fence=source.fence + 1,
        assignment_id="assignment-retry-003",
        worker=source.worker,
        spec_digest=source.spec_digest,
        start_commit_key="commit-retry-003",
        retry_provenance=retry_prov,
    )
    retry = (
        retry_committed.transition(AttemptState.PROVISIONING, expected_version=0)
        .transition(AttemptState.RUNNING, expected_version=1)
        .transition(AttemptState.UPLOADING, expected_version=2)
    )
    _, trusted_exit, case_summary, artifacts, requirements = first_inputs
    retry_candidate = build_evidence_manifest(
        attempt_id=retry.id,
        run_id=retry.run_id,
        attempt_no=retry.attempt_no,
        assignment_id=retry.assignment_id,
        fence=retry.fence,
        worker=retry.worker,
        execution_spec_digest=retry.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
    )
    retry_finalized = retry.finalize_evidence(
        proposal=EvidenceProposal(root_digest=retry_candidate.root_digest),
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
        requirements=requirements,
        authority=_authority(fence=retry.fence, generation=retry.worker.generation),
        worker=retry.worker,
        fence=retry.fence,
        expected_version=retry.version,
    )

    # Retry succeeded on its own identity.
    assert retry_finalized.replayed is False
    assert retry_finalized.attempt.evidence is not None
    assert retry_finalized.attempt.id == "attempt-retry-003"
    assert retry_finalized.evidence.root_digest == retry_candidate.root_digest
    # Distinct root from attempt 1 (attempt_id/fence/attempt_no bind into the digest).
    assert retry_finalized.evidence.root_digest != first_root

    # Prior Attempt is untouched — no overwrite of Evidence or paths.
    assert first.attempt.evidence is first.evidence
    assert first.attempt.evidence.root_digest == first_root
    assert tuple(a.path.value for a in first.attempt.evidence.artifacts) == first_paths
    assert first.attempt.state is AttemptState.PASSED
