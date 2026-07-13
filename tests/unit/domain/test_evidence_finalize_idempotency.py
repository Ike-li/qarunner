"""Evidence finalize idempotency, conflict, CAS, and state-ordering contracts."""

from dataclasses import replace

import pytest


def _running_attempt():
    from qarunner.domain import Attempt, AttemptState, WorkerRef, canonical_digest

    committed = Attempt.create(
        attempt_id="attempt-idempotency-001",
        run_id="run-idempotency-001",
        attempt_no=1,
        fence=7,
        assignment_id="assignment-idempotency-001",
        worker=WorkerRef(worker_id="worker-001", generation=4),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"run_id": "run-idempotency-001"},
        ),
        start_commit_key="commit-idempotency-001",
    )
    provisioning = committed.transition(AttemptState.PROVISIONING, expected_version=0)
    return provisioning.transition(AttemptState.RUNNING, expected_version=1)


def _uploading_attempt():
    from qarunner.domain import AttemptState

    running = _running_attempt()
    return running.transition(AttemptState.UPLOADING, expected_version=2)


def _authority(attempt, *, fence: int | None = None, generation: int | None = None):
    from qarunner.domain import AttemptAuthority, WorkerRef

    return AttemptAuthority(
        current_fence=attempt.fence if fence is None else fence,
        current_worker=WorkerRef(
            worker_id=attempt.worker.worker_id,
            generation=attempt.worker.generation if generation is None else generation,
        ),
    )


def _valid_finalize_inputs(attempt):
    from qarunner.domain import (
        ArtifactClass,
        ArtifactPath,
        EvidenceProposal,
        EvidenceRequirements,
        PlatformExitClass,
        TrustedExitFacts,
        ValidatedCaseSummary,
        VerifiedArtifact,
        build_evidence_manifest,
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
    artifacts = (artifact,)
    requirements = EvidenceRequirements(required_artifact_paths=frozenset({result_path}))
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
    proposal = EvidenceProposal(root_digest=candidate.root_digest)
    return proposal, trusted_exit, case_summary, artifacts, requirements


def _finalize(attempt, inputs, *, authority=None, expected_version: int | None = None):
    proposal, trusted_exit, case_summary, artifacts, requirements = inputs
    return attempt.finalize_evidence(
        proposal=proposal,
        trusted_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=artifacts,
        requirements=requirements,
        authority=_authority(attempt) if authority is None else authority,
        worker=attempt.worker,
        fence=attempt.fence,
        expected_version=attempt.version if expected_version is None else expected_version,
    )


def test_exact_finalize_replay_wins_before_stale_cas() -> None:
    """Response-loss replay returns stored Evidence despite the old expected version."""
    attempt = _uploading_attempt()
    inputs = _valid_finalize_inputs(attempt)
    first = _finalize(attempt, inputs)

    replay = _finalize(first.attempt, inputs, expected_version=attempt.version)

    assert replay.replayed is True
    assert replay.attempt == first.attempt
    assert replay.evidence == first.evidence
    assert replay.attempt.version == 4


def test_changed_payload_reusing_finalized_claimed_root_is_conflict() -> None:
    """An old valid root cannot make changed server facts look like an exact replay."""
    from qarunner.domain import EvidenceConflict

    attempt = _uploading_attempt()
    inputs = _valid_finalize_inputs(attempt)
    first = _finalize(attempt, inputs)
    proposal, trusted_exit, case_summary, artifacts, requirements = inputs
    changed_inputs = (
        proposal,
        replace(trusted_exit, pid=trusted_exit.pid + 1),
        case_summary,
        artifacts,
        requirements,
    )

    with pytest.raises(EvidenceConflict) as caught:
        _finalize(first.attempt, changed_inputs, expected_version=attempt.version)

    assert caught.value.stored_root == first.evidence.root_digest
    assert caught.value.received_root != first.evidence.root_digest
    assert first.attempt.evidence == first.evidence
    assert first.attempt.version == 4


def test_different_claimed_root_conflicts_before_cas_without_overwrite() -> None:
    """A conflicting replay is rejected as Evidence conflict even with stale CAS."""
    from qarunner.domain import EvidenceConflict, EvidenceProposal, canonical_digest

    attempt = _uploading_attempt()
    inputs = _valid_finalize_inputs(attempt)
    first = _finalize(attempt, inputs)
    _, trusted_exit, case_summary, artifacts, requirements = inputs
    different_root = canonical_digest(
        schema_version="qep.worker-evidence-proposal.v1",
        payload={"attempt_id": attempt.id, "revision": 2},
    )
    conflicting_inputs = (
        EvidenceProposal(root_digest=different_root),
        trusted_exit,
        case_summary,
        artifacts,
        requirements,
    )

    with pytest.raises(EvidenceConflict) as caught:
        _finalize(first.attempt, conflicting_inputs, expected_version=attempt.version)

    assert caught.value.stored_root == first.evidence.root_digest
    assert caught.value.received_root == different_root
    assert first.attempt.evidence == first.evidence
    assert first.attempt.version == 4


def test_first_nonreplay_finalize_with_stale_cas_is_version_conflict() -> None:
    """A first finalize cannot write Evidence through a stale aggregate version."""
    from qarunner.domain import VersionConflict

    attempt = _uploading_attempt()

    with pytest.raises(VersionConflict) as caught:
        _finalize(
            attempt,
            _valid_finalize_inputs(attempt),
            expected_version=attempt.version - 1,
        )

    assert caught.value.current_version == 3
    assert caught.value.expected_version == 2
    assert attempt.evidence is None
    assert attempt.version == 3


@pytest.mark.parametrize(
    ("authority", "expected_error"),
    [
        ("new_fence", "StaleFence"),
        ("new_generation", "StaleGeneration"),
    ],
)
def test_exact_replay_is_rejected_after_authority_is_superseded(
    authority: str, expected_error: str
) -> None:
    """Stored Evidence does not let a retired Attempt identity bypass authority."""
    from qarunner.domain import StaleFence, StaleGeneration

    attempt = _uploading_attempt()
    inputs = _valid_finalize_inputs(attempt)
    first = _finalize(attempt, inputs)
    superseded = (
        _authority(first.attempt, fence=first.attempt.fence + 1)
        if authority == "new_fence"
        else _authority(first.attempt, generation=first.attempt.worker.generation + 1)
    )
    error_type = StaleFence if expected_error == "StaleFence" else StaleGeneration

    with pytest.raises(error_type):
        _finalize(
            first.attempt,
            inputs,
            authority=superseded,
            expected_version=attempt.version,
        )

    assert first.attempt.evidence == first.evidence
    assert first.attempt.version == 4


def test_valid_evidence_cannot_finalize_before_uploading() -> None:
    """Even a valid manifest cannot skip the RUNNING to UPLOADING boundary."""
    from qarunner.domain import AttemptState, InvalidTransition

    attempt = _running_attempt()

    with pytest.raises(InvalidTransition) as caught:
        _finalize(attempt, _valid_finalize_inputs(attempt))

    assert caught.value.current_state == AttemptState.RUNNING
    assert caught.value.requested_state == AttemptState.PASSED
    assert attempt.evidence is None
    assert attempt.version == 2
