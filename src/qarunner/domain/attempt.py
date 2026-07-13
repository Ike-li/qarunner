"""Attempt aggregate for the greenfield execution lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.authority import AttemptAuthority
from qarunner.domain.digest import Digest
from qarunner.domain.errors import (
    EventConflict,
    EvidenceConflict,
    EvidenceDigestMismatch,
    EvidenceNotReady,
    InvalidTransition,
    StaleFence,
    StaleGeneration,
    ensure_expected_version,
)
from qarunner.domain.event import AttemptEvent
from qarunner.domain.evidence import (
    EvidenceManifest,
    EvidenceProposal,
    EvidenceRequirements,
    TrustedExitFacts,
    ValidatedCaseSummary,
    VerifiedArtifact,
    build_evidence_manifest,
)
from qarunner.domain.worker import WorkerRef


class AttemptState(enum.StrEnum):
    """States of one real execution Attempt."""

    START_COMMITTED = "start_committed"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    UPLOADING = "uploading"
    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"
    ATTEMPT_UNKNOWN = "attempt_unknown"


@dataclass(frozen=True, slots=True)
class FinalizeEvidenceResult:
    """Result of first Evidence finalize or an exact replay."""

    attempt: Attempt
    evidence: EvidenceManifest
    replayed: bool


_TERMINAL_STATES = frozenset(
    {
        AttemptState.PASSED,
        AttemptState.TEST_FAILED,
        AttemptState.INFRA_FAILED,
        AttemptState.CANCELLED,
        AttemptState.ATTEMPT_UNKNOWN,
    }
)
_ALLOWED_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.START_COMMITTED: frozenset(
        {AttemptState.PROVISIONING, AttemptState.ATTEMPT_UNKNOWN}
    ),
    AttemptState.PROVISIONING: frozenset(
        {AttemptState.RUNNING, AttemptState.UPLOADING, AttemptState.ATTEMPT_UNKNOWN}
    ),
    AttemptState.RUNNING: frozenset({AttemptState.UPLOADING, AttemptState.ATTEMPT_UNKNOWN}),
    # Terminal classification is deliberately not exposed through transition().
    # A later M0 slice adds Evidence finalize with trusted exit facts.
    AttemptState.UPLOADING: frozenset(),
    **{state: frozenset() for state in _TERMINAL_STATES},
}


@dataclass(frozen=True, slots=True)
class Attempt:
    """Immutable record of one committed execution attempt."""

    id: str
    run_id: str
    attempt_no: int
    fence: int
    assignment_id: str
    worker: WorkerRef
    spec_digest: Digest
    start_commit_key: str
    events: tuple[AttemptEvent, ...]
    evidence: EvidenceManifest | None
    state: AttemptState
    version: int

    @classmethod
    def create(
        cls,
        *,
        attempt_id: str,
        run_id: str,
        attempt_no: int,
        fence: int,
        assignment_id: str,
        worker: WorkerRef,
        spec_digest: Digest,
        start_commit_key: str,
    ) -> Attempt:
        """Create the Attempt only after start commit is durable."""
        return cls(
            id=attempt_id,
            run_id=run_id,
            attempt_no=attempt_no,
            fence=fence,
            assignment_id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
            start_commit_key=start_commit_key,
            events=(),
            evidence=None,
            state=AttemptState.START_COMMITTED,
            version=0,
        )

    def transition(self, target: AttemptState, *, expected_version: int) -> Attempt:
        """Reject stale commands or transitions that skip execution phases."""
        ensure_expected_version(
            entity_type="attempt",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(
                entity_type="attempt",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(self, state=target, version=self.version + 1)

    def record_event(
        self,
        event: AttemptEvent,
        *,
        authority: AttemptAuthority,
        worker: WorkerRef,
        fence: int,
        expected_version: int,
    ) -> Attempt:
        """Append a Worker event only for the Attempt's current fence."""
        self._ensure_authority(authority=authority, worker=worker, fence=fence)
        existing = next(
            (
                candidate
                for candidate in self.events
                if candidate.event_id == event.event_id or candidate.event_seq == event.event_seq
            ),
            None,
        )
        if existing is not None:
            if existing != event:
                raise EventConflict(
                    attempt_id=self.id,
                    stored_event=existing,
                    received_event=event,
                )
            return self
        ensure_expected_version(
            entity_type="attempt",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        return replace(self, events=(*self.events, event), version=self.version + 1)

    def finalize_evidence(
        self,
        *,
        proposal: EvidenceProposal,
        trusted_exit: TrustedExitFacts | None,
        case_summary: ValidatedCaseSummary | None,
        artifacts: tuple[VerifiedArtifact, ...],
        requirements: EvidenceRequirements,
        authority: AttemptAuthority,
        worker: WorkerRef,
        fence: int,
        expected_version: int,
    ) -> FinalizeEvidenceResult:
        """Finalize trusted Evidence only for the current Worker/fence."""
        self._ensure_authority(authority=authority, worker=worker, fence=fence)
        candidate = build_evidence_manifest(
            attempt_id=self.id,
            run_id=self.run_id,
            attempt_no=self.attempt_no,
            assignment_id=self.assignment_id,
            fence=self.fence,
            worker=self.worker,
            execution_spec_digest=self.spec_digest,
            trusted_exit=trusted_exit,
            case_summary=case_summary,
            artifacts=artifacts,
        )
        if self.evidence is not None:
            if self.evidence == candidate and proposal.root_digest == candidate.root_digest:
                return FinalizeEvidenceResult(
                    attempt=self,
                    evidence=self.evidence,
                    replayed=True,
                )
            received_root = (
                proposal.root_digest if self.evidence == candidate else candidate.root_digest
            )
            raise EvidenceConflict(
                stored_root=self.evidence.root_digest,
                received_root=received_root,
            )
        ensure_expected_version(
            entity_type="attempt",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        target_state = AttemptState(candidate.outcome.value)
        if self.state is not AttemptState.UPLOADING:
            raise InvalidTransition(
                entity_type="attempt",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target_state,
                current_version=self.version,
                expected_version=expected_version,
            )
        available_paths = frozenset(artifact.path for artifact in candidate.artifacts)
        missing_paths = requirements.required_artifact_paths - available_paths
        if missing_paths:
            missing = ", ".join(sorted(path.value for path in missing_paths))
            raise EvidenceNotReady(reason=f"required artifacts are missing: {missing}")
        if proposal.root_digest != candidate.root_digest:
            raise EvidenceDigestMismatch(
                claimed=proposal.root_digest,
                computed=candidate.root_digest,
            )
        finalized = replace(
            self,
            evidence=candidate,
            state=target_state,
            version=self.version + 1,
        )
        return FinalizeEvidenceResult(
            attempt=finalized,
            evidence=candidate,
            replayed=False,
        )

    def _ensure_authority(
        self, *, authority: AttemptAuthority, worker: WorkerRef, fence: int
    ) -> None:
        """Reject stale Attempt identity using control-plane authoritative state."""
        if self.fence != authority.current_fence:
            raise StaleFence(
                attempt_id=self.id,
                current_fence=authority.current_fence,
                received_fence=self.fence,
            )
        if fence != authority.current_fence:
            raise StaleFence(
                attempt_id=self.id,
                current_fence=authority.current_fence,
                received_fence=fence,
            )
        if self.worker != authority.current_worker:
            raise StaleGeneration(
                attempt_id=self.id,
                current_worker=authority.current_worker,
                received_worker=self.worker,
            )
        if worker != authority.current_worker:
            raise StaleGeneration(
                attempt_id=self.id,
                current_worker=authority.current_worker,
                received_worker=worker,
            )
