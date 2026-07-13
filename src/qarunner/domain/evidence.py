"""Trusted Artifact and M0 Evidence value objects."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from qarunner.domain.digest import Digest
from qarunner.domain.errors import ArtifactValidationError, EvidenceNotReady
from qarunner.domain.worker import WorkerRef

EVIDENCE_SCHEMA_VERSION = "qep.m0-attempt-evidence.v1"
CLASSIFICATION_SCHEMA_VERSION = "qep.attempt-classification.v1"


class ArtifactClass(enum.StrEnum):
    """Content classes with distinct validation and retention policies."""

    STRUCTURED_RESULT = "structured_result"
    LOG = "log"
    SCREENSHOT = "screenshot"
    VIDEO = "video"
    TRACE = "trace"
    REPORT = "report"
    DIAGNOSTIC = "diagnostic"


class PlatformExitClass(enum.StrEnum):
    """Control-plane classification of how the test process stopped."""

    COMPLETED = "completed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ArtifactPath:
    """A safe, relative POSIX logical path within one Attempt."""

    value: str

    def __post_init__(self) -> None:
        try:
            self.value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ArtifactValidationError(field="path", reason="must be valid UTF-8") from error
        if not self.value:
            raise ArtifactValidationError(field="path", reason="must not be empty")
        if self.value.startswith("/"):
            raise ArtifactValidationError(field="path", reason="must be relative")
        if "\x00" in self.value:
            raise ArtifactValidationError(field="path", reason="must not contain NUL")
        if "\\" in self.value:
            raise ArtifactValidationError(field="path", reason="must use POSIX separators")
        if any(segment in {"", ".", ".."} for segment in self.value.split("/")):
            raise ArtifactValidationError(field="path", reason="contains an unsafe segment")


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    """Logical metadata already verified by the upload boundary."""

    path: ArtifactPath
    content_class: ArtifactClass
    size_bytes: int
    digest: Digest

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ArtifactValidationError(field="size_bytes", reason="must be non-negative")
        if not isinstance(self.digest, Digest):
            raise ArtifactValidationError(field="digest", reason="must be a Digest value object")


@dataclass(frozen=True, slots=True)
class TrustedExitFacts:
    """Platform-owned process facts that test content cannot override."""

    source_event_id: str
    pid: int | None
    exit_class: PlatformExitClass
    exit_code: int | None
    signal: int | None
    oom: bool
    timeout: bool


@dataclass(frozen=True, slots=True)
class EvidenceProposal:
    """Untrusted Worker proposal used only for root-digest comparison."""

    root_digest: Digest


@dataclass(frozen=True, slots=True)
class ValidatedCaseSummary:
    """Schema-checked case aggregate tied to a verified structured Artifact."""

    schema_version: str
    source_artifact_path: ArtifactPath
    source_artifact_digest: Digest
    expected: int
    passed: int
    failed: int
    skipped: int
    not_reported: int
    unexpected: int

    def __post_init__(self) -> None:
        counts = (
            self.expected,
            self.passed,
            self.failed,
            self.skipped,
            self.not_reported,
            self.unexpected,
        )
        if any(count < 0 for count in counts):
            raise EvidenceNotReady(reason="case summary counts must be non-negative")
        if self.expected != self.passed + self.failed + self.skipped + self.not_reported:
            raise EvidenceNotReady(reason="case summary does not account for every expected case")


@dataclass(frozen=True, slots=True)
class EvidenceRequirements:
    """Suite policy facts required before terminal classification."""

    required_artifact_paths: frozenset[ArtifactPath]


class EvidenceOutcome(enum.StrEnum):
    """Terminal outcome computed from trusted platform and case facts."""

    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    """Control-plane rebuilt immutable M0 evidence for one Attempt."""

    schema_version: str
    attempt_id: str
    run_id: str
    attempt_no: int
    assignment_id: str
    fence: int
    worker: WorkerRef
    execution_spec_digest: Digest
    platform_exit: TrustedExitFacts
    case_summary: ValidatedCaseSummary | None
    artifacts: tuple[VerifiedArtifact, ...]
    classification_version: str
    outcome: EvidenceOutcome
    root_digest: Digest


def build_evidence_manifest(
    *,
    attempt_id: str,
    run_id: str,
    attempt_no: int,
    assignment_id: str,
    fence: int,
    worker: WorkerRef,
    execution_spec_digest: Digest,
    trusted_exit: TrustedExitFacts | None,
    case_summary: ValidatedCaseSummary | None,
    artifacts: tuple[VerifiedArtifact, ...],
) -> EvidenceManifest:
    """Rebuild canonical M0 Evidence exclusively from control-plane facts."""
    if trusted_exit is None:
        raise EvidenceNotReady(reason="trusted platform exit facts are missing")
    ordered_artifacts = tuple(sorted(artifacts, key=lambda item: item.path.value.encode()))
    paths = tuple(artifact.path for artifact in ordered_artifacts)
    if len(set(paths)) != len(paths):
        raise ArtifactValidationError(field="path", reason="must be unique within an Attempt")
    _ensure_case_summary_source(case_summary=case_summary, artifacts=ordered_artifacts)
    outcome = _classify_outcome(trusted_exit=trusted_exit, case_summary=case_summary)
    payload = {
        "attempt": {
            "assignment_id": assignment_id,
            "attempt_id": attempt_id,
            "attempt_no": attempt_no,
            "fence": fence,
            "run_id": run_id,
        },
        "worker": {"worker_id": worker.worker_id, "generation": worker.generation},
        "input": {"execution_spec_digest": execution_spec_digest.value},
        "platform_exit": {
            "source_event_id": trusted_exit.source_event_id,
            "pid": trusted_exit.pid,
            "class": trusted_exit.exit_class.value,
            "exit_code": trusted_exit.exit_code,
            "signal": trusted_exit.signal,
            "oom": trusted_exit.oom,
            "timeout": trusted_exit.timeout,
        },
        "case_summary": _case_summary_payload(case_summary),
        "artifacts": [
            {
                "path": artifact.path.value,
                "class": artifact.content_class.value,
                "size_bytes": artifact.size_bytes,
                "digest": artifact.digest.value,
            }
            for artifact in ordered_artifacts
        ],
        "classification": {
            "schema_version": CLASSIFICATION_SCHEMA_VERSION,
            "outcome": outcome.value,
        },
    }
    from qarunner.domain.digest import canonical_digest

    root_digest = canonical_digest(schema_version=EVIDENCE_SCHEMA_VERSION, payload=payload)
    return EvidenceManifest(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        attempt_id=attempt_id,
        run_id=run_id,
        attempt_no=attempt_no,
        assignment_id=assignment_id,
        fence=fence,
        worker=worker,
        execution_spec_digest=execution_spec_digest,
        platform_exit=trusted_exit,
        case_summary=case_summary,
        artifacts=ordered_artifacts,
        classification_version=CLASSIFICATION_SCHEMA_VERSION,
        outcome=outcome,
        root_digest=root_digest,
    )


def _ensure_case_summary_source(
    *,
    case_summary: ValidatedCaseSummary | None,
    artifacts: tuple[VerifiedArtifact, ...],
) -> None:
    if case_summary is None:
        return
    matching = next(
        (artifact for artifact in artifacts if artifact.path == case_summary.source_artifact_path),
        None,
    )
    if matching is None:
        raise EvidenceNotReady(reason="case summary source artifact is missing")
    if matching.content_class is not ArtifactClass.STRUCTURED_RESULT:
        raise EvidenceNotReady(reason="case summary source is not a structured result")
    if matching.digest != case_summary.source_artifact_digest:
        raise EvidenceNotReady(reason="case summary source digest does not match the artifact")


def _classify_outcome(
    *, trusted_exit: TrustedExitFacts, case_summary: ValidatedCaseSummary | None
) -> EvidenceOutcome:
    if trusted_exit.exit_class is PlatformExitClass.INFRA_FAILED:
        return EvidenceOutcome.INFRA_FAILED
    if trusted_exit.exit_class is PlatformExitClass.CANCELLED:
        return EvidenceOutcome.CANCELLED
    if trusted_exit.pid is None or trusted_exit.pid <= 0:
        raise EvidenceNotReady(reason="completed process facts require a positive pid")
    if trusted_exit.signal is not None or trusted_exit.oom or trusted_exit.timeout:
        raise EvidenceNotReady(reason="completed process facts contradict a platform failure")
    if trusted_exit.exit_code is None:
        raise EvidenceNotReady(reason="completed process facts require an exit code")
    if case_summary is None:
        raise EvidenceNotReady(reason="completed process facts require a validated case summary")
    incomplete = case_summary.not_reported > 0 or case_summary.unexpected > 0
    if trusted_exit.exit_code == 0 and case_summary.failed == 0 and not incomplete:
        return EvidenceOutcome.PASSED
    if trusted_exit.exit_code != 0 and case_summary.failed > 0 and not incomplete:
        return EvidenceOutcome.TEST_FAILED
    raise EvidenceNotReady(reason="process exit and validated case summary are contradictory")


def _case_summary_payload(case_summary: ValidatedCaseSummary | None):
    if case_summary is None:
        return None
    return {
        "schema_version": case_summary.schema_version,
        "source_artifact_path": case_summary.source_artifact_path.value,
        "source_artifact_digest": case_summary.source_artifact_digest.value,
        "expected": case_summary.expected,
        "passed": case_summary.passed,
        "failed": case_summary.failed,
        "skipped": case_summary.skipped,
        "not_reported": case_summary.not_reported,
        "unexpected": case_summary.unexpected,
    }
