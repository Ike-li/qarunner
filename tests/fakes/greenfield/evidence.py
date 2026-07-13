"""Deterministic Evidence-port Fakes."""

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.ports.evidence import (
    EvidenceVerificationRequest,
    VerifiedEvidenceInputs,
)
from qarunner.domain.authority import WorkerRef
from qarunner.domain.cancellation import TrustedCancellationStop
from qarunner.domain.digest import Digest
from qarunner.domain.errors import EvidenceConflict
from qarunner.domain.evidence import (
    ArtifactClass,
    ArtifactPath,
    EvidenceManifest,
    PlatformExitClass,
    TrustedExitFacts,
    ValidatedCaseSummary,
    VerifiedArtifact,
    build_evidence_manifest,
)


class PresetEvidenceVerifier:
    """Return only explicitly seeded trusted Evidence inputs."""

    def __init__(
        self,
        entries: tuple[tuple[EvidenceVerificationRequest, VerifiedEvidenceInputs], ...],
    ) -> None:
        self.__entries = dict(entries)

    async def verify(self, request: EvidenceVerificationRequest) -> VerifiedEvidenceInputs:
        verified = self.__entries.get(request)
        if verified is None:
            raise PortContractError(
                resource="evidence_verifier",
                field="request",
                reason="not_verified",
            )
        return verified


class InMemoryEvidenceManifestIndex:
    """Keep first-finalized Evidence manifests in memory."""

    def __init__(self) -> None:
        self.__manifests: dict[str, EvidenceManifest] = {}

    async def get(self, attempt_id: str) -> EvidenceManifest | None:
        return self.__manifests.get(attempt_id)

    async def finalize(self, manifest: EvidenceManifest) -> ReplayResult[EvidenceManifest]:
        _ensure_domain_built(manifest)
        stored = self.__manifests.get(manifest.attempt_id)
        if stored == manifest:
            return ReplayResult(value=stored, replayed=True)
        if stored is not None:
            raise EvidenceConflict(
                stored_root=stored.root_digest,
                received_root=manifest.root_digest,
            )
        self.__manifests[manifest.attempt_id] = manifest
        return ReplayResult(value=manifest, replayed=False)


def _ensure_domain_built(manifest: EvidenceManifest) -> None:
    has_valid_nested_values = (
        isinstance(manifest, EvidenceManifest)
        and _is_nonempty_string(manifest.attempt_id)
        and _is_nonempty_string(manifest.run_id)
        and _is_positive_integer(manifest.attempt_no)
        and _is_nonempty_string(manifest.assignment_id)
        and _is_positive_integer(manifest.fence)
        and isinstance(manifest.worker, WorkerRef)
        and isinstance(manifest.execution_spec_digest, Digest)
        and _has_valid_exit_shape(manifest.platform_exit)
        and _has_valid_case_summary_shape(manifest.case_summary)
        and isinstance(manifest.artifacts, tuple)
        and all(_has_valid_artifact_shape(artifact) for artifact in manifest.artifacts)
        and (
            manifest.cancellation_stop is None
            or isinstance(manifest.cancellation_stop, TrustedCancellationStop)
        )
    )
    if not has_valid_nested_values:
        raise PortContractError(
            resource="evidence_index",
            field="manifest",
            reason="not_domain_built",
        )
    try:
        rebuilt = build_evidence_manifest(
            attempt_id=manifest.attempt_id,
            run_id=manifest.run_id,
            attempt_no=manifest.attempt_no,
            assignment_id=manifest.assignment_id,
            fence=manifest.fence,
            worker=manifest.worker,
            execution_spec_digest=manifest.execution_spec_digest,
            trusted_exit=manifest.platform_exit,
            case_summary=manifest.case_summary,
            artifacts=manifest.artifacts,
            cancellation_stop=manifest.cancellation_stop,
        )
    except ValueError as error:
        raise PortContractError(
            resource="evidence_index",
            field="manifest",
            reason="not_domain_built",
        ) from error
    if rebuilt != manifest:
        raise PortContractError(
            resource="evidence_index",
            field="manifest",
            reason="not_domain_built",
        )


def _has_valid_exit_shape(value: object) -> bool:
    return (
        isinstance(value, TrustedExitFacts)
        and _is_nonempty_string(value.source_event_id)
        and _is_optional_integer(value.pid)
        and isinstance(value.exit_class, PlatformExitClass)
        and _is_optional_integer(value.exit_code)
        and _is_optional_integer(value.signal)
        and isinstance(value.oom, bool)
        and isinstance(value.timeout, bool)
    )


def _has_valid_case_summary_shape(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, ValidatedCaseSummary):
        return False
    counts = (
        value.expected,
        value.passed,
        value.failed,
        value.skipped,
        value.not_reported,
        value.unexpected,
    )
    return (
        _is_nonempty_string(value.schema_version)
        and _has_valid_artifact_path(value.source_artifact_path)
        and isinstance(value.source_artifact_digest, Digest)
        and all(_is_nonnegative_integer(count) for count in counts)
    )


def _has_valid_artifact_shape(value: object) -> bool:
    return (
        isinstance(value, VerifiedArtifact)
        and _has_valid_artifact_path(value.path)
        and isinstance(value.content_class, ArtifactClass)
        and _is_nonnegative_integer(value.size_bytes)
        and isinstance(value.digest, Digest)
    )


def _has_valid_artifact_path(value: object) -> bool:
    return isinstance(value, ArtifactPath) and _is_nonempty_string(value.value)


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_integer(value: object) -> bool:
    return _is_integer(value) and value > 0


def _is_nonnegative_integer(value: object) -> bool:
    return _is_integer(value) and value >= 0


def _is_optional_integer(value: object) -> bool:
    return value is None or _is_integer(value)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
