"""T-M0-FENCE/EVIDENCE: trusted finalize is required for terminal outcomes."""

import pytest


def _retry_provenance():
    from qarunner.domain import (
        RetryProvenance,
        UnknownAdjudicationDecision,
        canonical_digest,
    )

    def digest(label: str):
        return canonical_digest(
            schema_version="qep.test-retry-provenance.v1",
            payload={"label": label},
        )

    return RetryProvenance(
        retry_intent_id="retry-001",
        retry_intent_digest=digest("intent"),
        source_attempt_id="attempt-001",
        source_attempt_no=1,
        source_fence=1,
        adjudication_id="adjudication-001",
        adjudication_digest=digest("adjudication"),
        decision=UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY,
    )


def _uploading_attempt():
    from qarunner.domain import Attempt, AttemptState, WorkerRef, canonical_digest

    committed = Attempt.create(
        attempt_id="attempt-002",
        run_id="run-001",
        attempt_no=2,
        fence=2,
        assignment_id="assignment-002",
        worker=WorkerRef(worker_id="worker-001", generation=3),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"run_id": "run-001"},
        ),
        start_commit_key="commit-002",
        retry_provenance=_retry_provenance(),
    )
    provisioning = committed.transition(AttemptState.PROVISIONING, expected_version=0)
    running = provisioning.transition(AttemptState.RUNNING, expected_version=1)
    return running.transition(AttemptState.UPLOADING, expected_version=2)


def _finalize_inputs():
    from qarunner.domain import (
        ArtifactClass,
        ArtifactPath,
        EvidenceProposal,
        EvidenceRequirements,
        PlatformExitClass,
        TrustedExitFacts,
        ValidatedCaseSummary,
        VerifiedArtifact,
        canonical_digest,
    )

    result_path = ArtifactPath("case-results.json")
    artifact = VerifiedArtifact(
        path=result_path,
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=128,
        digest=canonical_digest(
            schema_version="qep.artifact-content.v1",
            payload={"cases": [{"id": "case-001", "outcome": "passed"}]},
        ),
    )
    trusted_exit = TrustedExitFacts(
        source_event_id="event-exit-001",
        pid=731,
        exit_class=PlatformExitClass.COMPLETED,
        exit_code=0,
        signal=None,
        oom=False,
        timeout=False,
    )
    case_summary = ValidatedCaseSummary(
        schema_version="qep.case-summary.v1",
        source_artifact_path=result_path,
        source_artifact_digest=artifact.digest,
        expected=1,
        passed=1,
        failed=0,
        skipped=0,
        not_reported=0,
        unexpected=0,
    )
    proposal = EvidenceProposal(
        root_digest=canonical_digest(
            schema_version="qep.worker-evidence-proposal.v1",
            payload={"attempt_id": "attempt-002", "placeholder": True},
        ),
    )
    requirements = EvidenceRequirements(
        required_artifact_paths=frozenset({ArtifactPath("case-results.json")})
    )
    return proposal, trusted_exit, case_summary, (artifact,), requirements


def _authority(*, fence: int = 2, generation: int = 3):
    from qarunner.domain import AttemptAuthority, WorkerRef

    return AttemptAuthority(
        current_fence=fence,
        current_worker=WorkerRef(worker_id="worker-001", generation=generation),
    )


def test_old_fence_cannot_finalize_evidence_or_terminal_state() -> None:
    """A prior Attempt incarnation cannot overwrite current Evidence or outcome."""
    from qarunner.domain import StaleFence, WorkerRef

    attempt = _uploading_attempt()
    proposal, trusted_exit, _, artifacts, requirements = _finalize_inputs()

    with pytest.raises(StaleFence) as caught:
        attempt.finalize_evidence(
            proposal=proposal,
            trusted_exit=trusted_exit,
            case_summary=None,
            artifacts=artifacts,
            requirements=requirements,
            authority=_authority(fence=3),
            worker=WorkerRef(worker_id="worker-001", generation=3),
            fence=2,
            expected_version=3,
        )

    assert caught.value.code == "stale_fence"
    assert caught.value.current_fence == 3
    assert caught.value.received_fence == 2
    assert attempt.evidence is None
    assert attempt.version == 3


def test_retired_generation_cannot_finalize_while_matching_the_old_attempt() -> None:
    """Worker Registry authority supersedes the generation stored on an old Attempt."""
    from qarunner.domain import StaleGeneration, WorkerRef

    attempt = _uploading_attempt()
    proposal, trusted_exit, _, artifacts, requirements = _finalize_inputs()

    with pytest.raises(StaleGeneration) as caught:
        attempt.finalize_evidence(
            proposal=proposal,
            trusted_exit=trusted_exit,
            case_summary=None,
            artifacts=artifacts,
            requirements=requirements,
            authority=_authority(generation=4),
            worker=WorkerRef(worker_id="worker-001", generation=3),
            fence=2,
            expected_version=3,
        )

    assert caught.value.current_worker == WorkerRef(worker_id="worker-001", generation=4)
    assert caught.value.received_worker == WorkerRef(worker_id="worker-001", generation=3)
    assert attempt.evidence is None
    assert attempt.version == 3


def test_current_authority_and_verified_evidence_finalize_passed() -> None:
    """Only server-rebuilt trusted Evidence can make an Attempt pass."""
    from qarunner.domain import EvidenceProposal, WorkerRef, build_evidence_manifest

    attempt = _uploading_attempt()
    _, trusted_exit, case_summary, artifacts, requirements = _finalize_inputs()
    candidate = build_evidence_manifest(
        attempt_id=attempt.id,
        run_id=attempt.run_id,
        attempt_no=attempt.attempt_no,
        assignment_id=attempt.assignment_id,
        fence=attempt.fence,
        worker=attempt.worker,
        execution_spec_digest=attempt.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
    )

    finalized = attempt.finalize_evidence(
        proposal=EvidenceProposal(root_digest=candidate.root_digest),
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
        requirements=requirements,
        authority=_authority(),
        worker=WorkerRef(worker_id="worker-001", generation=3),
        fence=2,
        expected_version=3,
    )

    assert finalized.replayed is False
    assert finalized.evidence == candidate
    assert finalized.attempt.evidence == candidate
    assert finalized.attempt.state.value == "passed"
    assert finalized.attempt.version == 4
    assert attempt.evidence is None
    assert attempt.state.value == "uploading"
    assert attempt.version == 3


def test_untrusted_success_event_cannot_replace_missing_trusted_exit_facts() -> None:
    """A Worker payload that says SUCCESS is not a platform-owned exit fact."""
    from qarunner.domain import (
        AttemptEvent,
        EvidenceNotReady,
        WorkerRef,
        canonical_digest,
    )

    attempt = _uploading_attempt()
    _, _, case_summary, artifacts, requirements = _finalize_inputs()
    success_event = AttemptEvent(
        event_id="event-untrusted-success-001",
        event_seq=1,
        event_type="evidence_ready",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"untrusted_content": {"status": "SUCCESS"}},
        ),
    )
    with_event = attempt.record_event(
        success_event,
        authority=_authority(),
        worker=WorkerRef(worker_id="worker-001", generation=3),
        fence=2,
        expected_version=3,
    )

    with pytest.raises(EvidenceNotReady) as caught:
        with_event.finalize_evidence(
            proposal=_finalize_inputs()[0],
            trusted_exit=None,
            case_summary=case_summary,
            artifacts=artifacts,
            requirements=requirements,
            authority=_authority(),
            worker=WorkerRef(worker_id="worker-001", generation=3),
            fence=2,
            expected_version=4,
        )

    assert caught.value.code == "evidence_not_ready"
    assert with_event.evidence is None
    assert with_event.state.value == "uploading"
    assert with_event.version == 4


def test_forged_passing_summary_cannot_override_trusted_infra_failure() -> None:
    """Platform timeout/OOM classification dominates user-controlled result content."""
    from dataclasses import replace

    from qarunner.domain import (
        EvidenceProposal,
        PlatformExitClass,
        WorkerRef,
        build_evidence_manifest,
    )

    attempt = _uploading_attempt()
    _, passing_exit, passing_summary, artifacts, requirements = _finalize_inputs()
    trusted_failure = replace(
        passing_exit,
        exit_class=PlatformExitClass.INFRA_FAILED,
        exit_code=None,
        timeout=True,
    )
    candidate = build_evidence_manifest(
        attempt_id=attempt.id,
        run_id=attempt.run_id,
        attempt_no=attempt.attempt_no,
        assignment_id=attempt.assignment_id,
        fence=attempt.fence,
        worker=attempt.worker,
        execution_spec_digest=attempt.spec_digest,
        trusted_exit=trusted_failure,
        case_summary=passing_summary,
        artifacts=artifacts,
    )

    finalized = attempt.finalize_evidence(
        proposal=EvidenceProposal(root_digest=candidate.root_digest),
        trusted_exit=trusted_failure,
        case_summary=passing_summary,
        artifacts=artifacts,
        requirements=requirements,
        authority=_authority(),
        worker=WorkerRef(worker_id="worker-001", generation=3),
        fence=2,
        expected_version=3,
    )

    assert finalized.attempt.state.value == "infra_failed"
    assert finalized.evidence.outcome.value == "infra_failed"
    assert finalized.evidence.platform_exit.timeout is True


def test_missing_required_artifact_prevents_terminal_state() -> None:
    """Suite policy, rather than a global non-empty rule, defines required paths."""
    from qarunner.domain import (
        ArtifactPath,
        EvidenceNotReady,
        EvidenceProposal,
        EvidenceRequirements,
        WorkerRef,
        build_evidence_manifest,
    )

    attempt = _uploading_attempt()
    _, trusted_exit, case_summary, artifacts, _ = _finalize_inputs()
    candidate = build_evidence_manifest(
        attempt_id=attempt.id,
        run_id=attempt.run_id,
        attempt_no=attempt.attempt_no,
        assignment_id=attempt.assignment_id,
        fence=attempt.fence,
        worker=attempt.worker,
        execution_spec_digest=attempt.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
    )
    requirements = EvidenceRequirements(
        required_artifact_paths=frozenset(
            {ArtifactPath("case-results.json"), ArtifactPath("runner.log")}
        )
    )

    with pytest.raises(EvidenceNotReady) as caught:
        attempt.finalize_evidence(
            proposal=EvidenceProposal(root_digest=candidate.root_digest),
            trusted_exit=trusted_exit,
            case_summary=case_summary,
            artifacts=artifacts,
            requirements=requirements,
            authority=_authority(),
            worker=WorkerRef(worker_id="worker-001", generation=3),
            fence=2,
            expected_version=3,
        )

    assert caught.value.reason == "required artifacts are missing: runner.log"
    assert attempt.evidence is None
    assert attempt.state.value == "uploading"


def test_claimed_root_must_match_server_rebuilt_manifest() -> None:
    """An untrusted Worker root cannot replace the control-plane calculation."""
    from qarunner.domain import EvidenceDigestMismatch, WorkerRef

    attempt = _uploading_attempt()
    wrong_proposal, trusted_exit, case_summary, artifacts, requirements = _finalize_inputs()

    with pytest.raises(EvidenceDigestMismatch) as caught:
        attempt.finalize_evidence(
            proposal=wrong_proposal,
            trusted_exit=trusted_exit,
            case_summary=case_summary,
            artifacts=artifacts,
            requirements=requirements,
            authority=_authority(),
            worker=WorkerRef(worker_id="worker-001", generation=3),
            fence=2,
            expected_version=3,
        )

    assert caught.value.claimed == wrong_proposal.root_digest
    assert caught.value.computed != wrong_proposal.root_digest
    assert attempt.evidence is None
    assert attempt.version == 3
