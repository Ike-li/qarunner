"""T-M0-PORT-001 trusted Evidence input and immutable index contracts."""

from dataclasses import replace

import pytest
from tests.fakes.greenfield.evidence import (
    InMemoryEvidenceManifestIndex,
    PresetEvidenceVerifier,
)

from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.evidence import (
    EvidenceManifestIndex,
    EvidenceVerificationRequest,
    EvidenceVerifier,
    VerifiedEvidenceInputs,
)
from qarunner.domain import (
    ArtifactClass,
    ArtifactPath,
    AttemptAuthority,
    EvidenceConflict,
    PlatformExitClass,
    TrustedExitFacts,
    ValidatedCaseSummary,
    VerifiedArtifact,
    WorkerRef,
    build_evidence_manifest,
    canonical_digest,
)


def test_evidence_verification_request_rejects_an_empty_attempt_id() -> None:
    with pytest.raises(PortContractError) as captured:
        EvidenceVerificationRequest(
            attempt_id="   ",
            authority=AttemptAuthority(
                current_fence=1,
                current_worker=WorkerRef(worker_id="worker-1", generation=1),
            ),
        )

    assert captured.value.resource == "evidence_verification_request"
    assert captured.value.field == "attempt_id"
    assert captured.value.reason == "empty"


def test_evidence_verification_request_requires_a_string_attempt_id() -> None:
    with pytest.raises(PortContractError) as captured:
        EvidenceVerificationRequest(
            attempt_id=7,  # type: ignore[arg-type]
            authority=AttemptAuthority(
                current_fence=1,
                current_worker=WorkerRef(worker_id="worker-1", generation=1),
            ),
        )

    assert captured.value.resource == "evidence_verification_request"
    assert captured.value.field == "attempt_id"
    assert captured.value.reason == "not_string"


def test_evidence_verification_request_requires_attempt_authority() -> None:
    with pytest.raises(PortContractError) as captured:
        EvidenceVerificationRequest(
            attempt_id="attempt-1",
            authority=None,  # type: ignore[arg-type]
        )

    assert captured.value.resource == "evidence_verification_request"
    assert captured.value.field == "authority"
    assert captured.value.reason == "not_attempt_authority"


def test_verified_evidence_inputs_require_trusted_exit_facts() -> None:
    with pytest.raises(PortContractError) as captured:
        VerifiedEvidenceInputs(
            trusted_exit=None,  # type: ignore[arg-type]
            case_summary=None,
            artifacts=(),
        )

    assert captured.value.resource == "verified_evidence_inputs"
    assert captured.value.field == "trusted_exit"
    assert captured.value.reason == "not_trusted_exit_facts"


def test_verified_evidence_inputs_require_a_validated_case_summary() -> None:
    manifest = _passing_manifest()

    with pytest.raises(PortContractError) as captured:
        VerifiedEvidenceInputs(
            trusted_exit=manifest.platform_exit,
            case_summary=object(),  # type: ignore[arg-type]
            artifacts=manifest.artifacts,
        )

    assert captured.value.resource == "verified_evidence_inputs"
    assert captured.value.field == "case_summary"
    assert captured.value.reason == "not_validated_case_summary"


def test_verified_evidence_inputs_require_an_immutable_artifact_tuple() -> None:
    manifest = _passing_manifest()

    with pytest.raises(PortContractError) as captured:
        VerifiedEvidenceInputs(
            trusted_exit=manifest.platform_exit,
            case_summary=manifest.case_summary,
            artifacts=list(manifest.artifacts),  # type: ignore[arg-type]
        )

    assert captured.value.resource == "verified_evidence_inputs"
    assert captured.value.field == "artifacts"
    assert captured.value.reason == "not_tuple"


def test_verified_evidence_inputs_require_verified_artifacts() -> None:
    manifest = _passing_manifest()

    with pytest.raises(PortContractError) as captured:
        VerifiedEvidenceInputs(
            trusted_exit=manifest.platform_exit,
            case_summary=manifest.case_summary,
            artifacts=(object(),),  # type: ignore[arg-type]
        )

    assert captured.value.resource == "verified_evidence_inputs"
    assert captured.value.field == "artifacts"
    assert captured.value.reason == "contains_unverified_artifact"


def test_verified_evidence_inputs_require_a_trusted_cancellation_stop() -> None:
    manifest = _passing_manifest()

    with pytest.raises(PortContractError) as captured:
        VerifiedEvidenceInputs(
            trusted_exit=manifest.platform_exit,
            case_summary=manifest.case_summary,
            artifacts=manifest.artifacts,
            cancellation_stop=object(),  # type: ignore[arg-type]
        )

    assert captured.value.resource == "verified_evidence_inputs"
    assert captured.value.field == "cancellation_stop"
    assert captured.value.reason == "not_trusted_cancellation_stop"


async def test_evidence_verifier_fails_closed_without_preset_trusted_inputs() -> None:
    request = EvidenceVerificationRequest(
        attempt_id="attempt-1",
        authority=AttemptAuthority(
            current_fence=1,
            current_worker=WorkerRef(worker_id="worker-1", generation=1),
        ),
    )
    verifier = PresetEvidenceVerifier(())

    with pytest.raises(PortContractError) as captured:
        await verifier.verify(request)

    assert captured.value.resource == "evidence_verifier"
    assert captured.value.field == "request"
    assert captured.value.reason == "not_verified"


async def test_evidence_verifier_returns_only_preset_inputs_for_domain_rebuild() -> None:
    manifest = _passing_manifest()
    request = EvidenceVerificationRequest(
        attempt_id=manifest.attempt_id,
        authority=AttemptAuthority(
            current_fence=manifest.fence,
            current_worker=manifest.worker,
        ),
    )
    inputs = VerifiedEvidenceInputs(
        trusted_exit=manifest.platform_exit,
        case_summary=manifest.case_summary,
        artifacts=manifest.artifacts,
        cancellation_stop=manifest.cancellation_stop,
    )
    verifier = PresetEvidenceVerifier(((request, inputs),))

    verified = await verifier.verify(request)
    rebuilt = build_evidence_manifest(
        attempt_id=manifest.attempt_id,
        run_id=manifest.run_id,
        attempt_no=manifest.attempt_no,
        assignment_id=manifest.assignment_id,
        fence=manifest.fence,
        worker=manifest.worker,
        execution_spec_digest=manifest.execution_spec_digest,
        trusted_exit=verified.trusted_exit,
        case_summary=verified.case_summary,
        artifacts=verified.artifacts,
        cancellation_stop=verified.cancellation_stop,
    )

    assert isinstance(verifier, EvidenceVerifier)
    assert verified == inputs
    assert rebuilt == manifest


async def test_evidence_index_finalizes_and_reads_one_immutable_manifest() -> None:
    manifest = _passing_manifest()
    index = InMemoryEvidenceManifestIndex()

    result = await index.finalize(manifest)

    assert isinstance(index, EvidenceManifestIndex)
    assert result.value == manifest
    assert result.replayed is False
    assert await index.get(manifest.attempt_id) == manifest


async def test_evidence_index_replays_the_first_exact_manifest() -> None:
    manifest = _passing_manifest()
    index = InMemoryEvidenceManifestIndex()
    await index.finalize(manifest)

    replay = await index.finalize(replace(manifest))

    assert replay.value == manifest
    assert replay.replayed is True
    assert await index.get(manifest.attempt_id) == manifest


async def test_evidence_index_rejects_a_different_root_without_overwriting() -> None:
    manifest = _passing_manifest()
    conflict = _passing_manifest(run_id="run-2")
    index = InMemoryEvidenceManifestIndex()
    await index.finalize(manifest)

    with pytest.raises(EvidenceConflict) as captured:
        await index.finalize(conflict)

    assert captured.value.stored_root == manifest.root_digest
    assert captured.value.received_root == conflict.root_digest
    assert await index.get(manifest.attempt_id) == manifest


async def test_evidence_index_rejects_a_first_manifest_not_built_by_domain() -> None:
    manifest = _passing_manifest()
    forged = replace(
        manifest,
        root_digest=canonical_digest(
            schema_version="qep.test-forged-evidence.v1",
            payload={"attempt_id": manifest.attempt_id},
        ),
    )
    index = InMemoryEvidenceManifestIndex()

    with pytest.raises(PortContractError) as captured:
        await index.finalize(forged)

    assert captured.value.resource == "evidence_index"
    assert captured.value.field == "manifest"
    assert captured.value.reason == "not_domain_built"
    assert await index.get(manifest.attempt_id) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"worker": None},
        {"execution_spec_digest": None},
        {"platform_exit": None},
        {"case_summary": object()},
        {"artifacts": []},
        {"artifacts": (object(),)},
        {"cancellation_stop": object()},
    ],
)
async def test_evidence_index_fails_closed_for_malformed_manifest_values(
    changes: dict[str, object],
) -> None:
    manifest = _passing_manifest()
    malformed = replace(manifest, **changes)
    index = InMemoryEvidenceManifestIndex()

    with pytest.raises(PortContractError) as captured:
        await index.finalize(malformed)

    assert captured.value.resource == "evidence_index"
    assert captured.value.field == "manifest"
    assert captured.value.reason == "not_domain_built"
    assert await index.get(manifest.attempt_id) is None


@pytest.mark.parametrize("nested_field", ["artifact_path", "exit_class"])
async def test_evidence_index_fails_closed_for_malformed_nested_values(
    nested_field: str,
) -> None:
    manifest = _passing_manifest()
    if nested_field == "artifact_path":
        malformed = replace(
            manifest,
            artifacts=(replace(manifest.artifacts[0], path=object()),),
        )
    else:
        malformed = replace(
            manifest,
            platform_exit=replace(manifest.platform_exit, exit_class=object()),
        )
    index = InMemoryEvidenceManifestIndex()

    with pytest.raises(PortContractError) as captured:
        await index.finalize(malformed)

    assert captured.value.resource == "evidence_index"
    assert captured.value.field == "manifest"
    assert captured.value.reason == "not_domain_built"
    assert await index.get(manifest.attempt_id) is None


def _passing_manifest(*, run_id: str = "run-1"):
    artifact = VerifiedArtifact(
        path=ArtifactPath("case-results.json"),
        content_class=ArtifactClass.STRUCTURED_RESULT,
        size_bytes=128,
        digest=canonical_digest(
            schema_version="qep.artifact-content.v1",
            payload={"case": "case-1", "outcome": "passed"},
        ),
    )
    case_summary = ValidatedCaseSummary(
        schema_version="qep.case-summary.v1",
        source_artifact_path=artifact.path,
        source_artifact_digest=artifact.digest,
        expected=1,
        passed=1,
        failed=0,
        skipped=0,
        not_reported=0,
        unexpected=0,
    )
    return build_evidence_manifest(
        attempt_id="attempt-1",
        run_id=run_id,
        attempt_no=1,
        assignment_id="assignment-1",
        fence=1,
        worker=WorkerRef(worker_id="worker-1", generation=1),
        execution_spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"run_id": run_id},
        ),
        trusted_exit=TrustedExitFacts(
            source_event_id="event-exit-1",
            pid=731,
            exit_class=PlatformExitClass.COMPLETED,
            exit_code=0,
            signal=None,
            oom=False,
            timeout=False,
        ),
        case_summary=case_summary,
        artifacts=(artifact,),
    )
