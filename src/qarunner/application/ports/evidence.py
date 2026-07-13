"""Trusted Evidence verification and immutable manifest-index ports."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain.authority import AttemptAuthority
from qarunner.domain.cancellation import TrustedCancellationStop
from qarunner.domain.evidence import (
    EvidenceManifest,
    TrustedExitFacts,
    ValidatedCaseSummary,
    VerifiedArtifact,
)


@dataclass(frozen=True, slots=True)
class EvidenceVerificationRequest:
    """Attempt authority whose stored platform facts must be verified."""

    attempt_id: str
    authority: AttemptAuthority

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str):
            raise PortContractError(
                resource="evidence_verification_request",
                field="attempt_id",
                reason="not_string",
            )
        if not self.attempt_id.strip():
            raise PortContractError(
                resource="evidence_verification_request",
                field="attempt_id",
                reason="empty",
            )
        if not isinstance(self.authority, AttemptAuthority):
            raise PortContractError(
                resource="evidence_verification_request",
                field="authority",
                reason="not_attempt_authority",
            )


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceInputs:
    """Trusted inputs that the domain may use to rebuild a manifest."""

    trusted_exit: TrustedExitFacts
    case_summary: ValidatedCaseSummary | None
    artifacts: tuple[VerifiedArtifact, ...]
    cancellation_stop: TrustedCancellationStop | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.trusted_exit, TrustedExitFacts):
            raise PortContractError(
                resource="verified_evidence_inputs",
                field="trusted_exit",
                reason="not_trusted_exit_facts",
            )
        if self.case_summary is not None and not isinstance(
            self.case_summary, ValidatedCaseSummary
        ):
            raise PortContractError(
                resource="verified_evidence_inputs",
                field="case_summary",
                reason="not_validated_case_summary",
            )
        if not isinstance(self.artifacts, tuple):
            raise PortContractError(
                resource="verified_evidence_inputs",
                field="artifacts",
                reason="not_tuple",
            )
        if any(not isinstance(artifact, VerifiedArtifact) for artifact in self.artifacts):
            raise PortContractError(
                resource="verified_evidence_inputs",
                field="artifacts",
                reason="contains_unverified_artifact",
            )
        if self.cancellation_stop is not None and not isinstance(
            self.cancellation_stop, TrustedCancellationStop
        ):
            raise PortContractError(
                resource="verified_evidence_inputs",
                field="cancellation_stop",
                reason="not_trusted_cancellation_stop",
            )


@runtime_checkable
class EvidenceVerifier(Protocol):
    """Return trusted inputs only after adapter-owned verification."""

    async def verify(self, request: EvidenceVerificationRequest) -> VerifiedEvidenceInputs: ...


@runtime_checkable
class EvidenceManifestIndex(Protocol):
    """Finalize each Attempt's manifest once and expose it as immutable Evidence."""

    async def get(self, attempt_id: str) -> EvidenceManifest | None: ...

    async def finalize(self, manifest: EvidenceManifest) -> ReplayResult[EvidenceManifest]: ...
