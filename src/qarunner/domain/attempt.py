"""Attempt aggregate for the greenfield execution lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.authority import AttemptAuthority
from qarunner.domain.cancellation import TrustedCancellationStop
from qarunner.domain.digest import Digest
from qarunner.domain.errors import (
    AdjudicationConflict,
    AttemptEventRejected,
    DomainValidationError,
    EventConflict,
    EvidenceConflict,
    EvidenceDigestMismatch,
    EvidenceNotReady,
    InvalidTransition,
    StaleFence,
    StaleGeneration,
    UnknownObservationConflict,
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
from qarunner.domain.retry import RetryProvenance
from qarunner.domain.unknown import (
    UnknownAdjudication,
    UnknownAdjudicationDecision,
    UnknownObservation,
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
_EVIDENCE_TERMINAL_STATES = _TERMINAL_STATES - {AttemptState.ATTEMPT_UNKNOWN}
_ALLOWED_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.START_COMMITTED: frozenset({AttemptState.PROVISIONING}),
    AttemptState.PROVISIONING: frozenset({AttemptState.RUNNING, AttemptState.UPLOADING}),
    AttemptState.RUNNING: frozenset({AttemptState.UPLOADING}),
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
    unknown_observation: UnknownObservation | None
    adjudications: tuple[UnknownAdjudication, ...]
    state: AttemptState
    version: int
    retry_provenance: RetryProvenance | None = None

    def __post_init__(self) -> None:
        for field in ("id", "run_id", "assignment_id", "start_commit_key"):
            value = getattr(self, field)
            if not isinstance(value, str):
                _invalid_attempt(field, "not_string")
            if not value.strip():
                _invalid_attempt(field, "empty")
        for field in ("attempt_no", "fence"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                _invalid_attempt(field, "not_integer")
            if value < 1:
                _invalid_attempt(field, "not_positive")
        if not isinstance(self.worker, WorkerRef):
            _invalid_attempt("worker", "invalid_type")
        if not isinstance(self.spec_digest, Digest):
            _invalid_attempt("spec_digest", "not_digest")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            _invalid_attempt("version", "invalid")
        if not isinstance(self.events, tuple):
            _invalid_attempt("events", "not_tuple")
        if any(not isinstance(event, AttemptEvent) for event in self.events):
            _invalid_attempt("events", "invalid_type")
        if self.evidence is not None and not isinstance(self.evidence, EvidenceManifest):
            _invalid_attempt("evidence", "invalid_type")
        if not isinstance(self.state, AttemptState):
            raise DomainValidationError(
                entity_type="attempt",
                field="state",
                reason="unknown_state",
            )
        if self.state in _EVIDENCE_TERMINAL_STATES and self.evidence is None:
            _invalid_attempt("evidence", "required_for_terminal")
        if self.evidence is not None:
            if self.state not in _EVIDENCE_TERMINAL_STATES:
                _invalid_attempt("evidence", "forbidden_for_state")
            try:
                evidence_state = AttemptState(self.evidence.outcome.value)
            except (AttributeError, TypeError, ValueError):
                _invalid_attempt("evidence", "outcome_invalid")
            if self.state is not evidence_state:
                _invalid_attempt("evidence", "outcome_mismatch")
        if self.unknown_observation is not None and not isinstance(
            self.unknown_observation, UnknownObservation
        ):
            raise DomainValidationError(
                entity_type="attempt",
                field="unknown_observation",
                reason="invalid_type",
            )
        is_unknown = self.state is AttemptState.ATTEMPT_UNKNOWN
        has_observation = self.unknown_observation is not None
        if is_unknown and not has_observation:
            raise DomainValidationError(
                entity_type="attempt",
                field="unknown_observation",
                reason="required_for_unknown",
            )
        if not is_unknown and has_observation:
            raise DomainValidationError(
                entity_type="attempt",
                field="unknown_observation",
                reason="only_allowed_for_unknown",
            )
        if self.attempt_no > 1 and self.retry_provenance is None:
            raise DomainValidationError(
                entity_type="attempt",
                field="retry_provenance",
                reason="required_for_retry_attempt",
            )
        if self.retry_provenance is not None:
            if not isinstance(self.retry_provenance, RetryProvenance):
                raise DomainValidationError(
                    entity_type="attempt",
                    field="retry_provenance",
                    reason="invalid_type",
                )
            if self.attempt_no < 2:
                raise DomainValidationError(
                    entity_type="attempt",
                    field="retry_provenance",
                    reason="not_allowed_for_first_attempt",
                )
            if self.retry_provenance.source_attempt_id == self.id:
                raise DomainValidationError(
                    entity_type="attempt",
                    field="retry_provenance",
                    reason="source_is_self",
                )
        self._validate_adjudication_history()

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
        retry_provenance: RetryProvenance | None = None,
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
            unknown_observation=None,
            adjudications=(),
            state=AttemptState.START_COMMITTED,
            version=0,
            retry_provenance=retry_provenance,
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

    def mark_unknown(
        self,
        *,
        observation: UnknownObservation,
        authority: AttemptAuthority,
        expected_version: int,
    ) -> Attempt:
        """Attach trusted review facts and enter the absorbing unknown state."""
        self._ensure_authority(authority=authority, worker=self.worker, fence=self.fence)
        if not isinstance(observation, UnknownObservation):
            raise DomainValidationError(
                entity_type="attempt",
                field="unknown_observation",
                reason="invalid_type",
            )
        if self.unknown_observation is not None and self.unknown_observation.id == observation.id:
            if self.unknown_observation == observation:
                return self
            raise UnknownObservationConflict(
                attempt_id=self.id,
                observation_id=observation.id,
            )
        ensure_expected_version(
            entity_type="attempt",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        eligible_states = frozenset(
            {
                AttemptState.START_COMMITTED,
                AttemptState.PROVISIONING,
                AttemptState.RUNNING,
                AttemptState.UPLOADING,
            }
        )
        if self.state not in eligible_states:
            raise InvalidTransition(
                entity_type="attempt",
                entity_id=self.id,
                current_state=self.state,
                requested_state=AttemptState.ATTEMPT_UNKNOWN,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(
            self,
            state=AttemptState.ATTEMPT_UNKNOWN,
            unknown_observation=observation,
            version=self.version + 1,
        )

    def append_unknown_adjudication(
        self,
        *,
        adjudication: UnknownAdjudication,
        expected_version: int,
    ) -> Attempt:
        """Append a handling decision without rewriting the unknown Attempt."""
        if not isinstance(adjudication, UnknownAdjudication):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="invalid_type",
            )
        existing = next(
            (record for record in self.adjudications if record.id == adjudication.id),
            None,
        )
        if existing is not None:
            if existing == adjudication:
                return self
            raise AdjudicationConflict(
                attempt_id=self.id,
                adjudication_id=adjudication.id,
            )
        ensure_expected_version(
            entity_type="attempt",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        return replace(
            self,
            adjudications=(*self.adjudications, adjudication),
            version=self.version + 1,
        )

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
        if self.state in _TERMINAL_STATES:
            raise AttemptEventRejected(
                attempt_id=self.id,
                reason="attempt_terminal",
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
        cancellation_stop: TrustedCancellationStop | None = None,
    ) -> FinalizeEvidenceResult:
        """Finalize trusted Evidence only for the current Worker/fence."""
        if cancellation_stop is not None:
            raise EvidenceNotReady(reason="cancelled evidence requires Run-owned finalization")
        return self._finalize_evidence(
            proposal=proposal,
            trusted_exit=trusted_exit,
            case_summary=case_summary,
            artifacts=artifacts,
            requirements=requirements,
            authority=authority,
            worker=worker,
            fence=fence,
            expected_version=expected_version,
            cancellation_stop=None,
        )

    def _finalize_cancellation_evidence(
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
        cancellation_stop: TrustedCancellationStop,
    ) -> FinalizeEvidenceResult:
        """Finalize cancellation Evidence only through the owning Run aggregate."""
        return self._finalize_evidence(
            proposal=proposal,
            trusted_exit=trusted_exit,
            case_summary=case_summary,
            artifacts=artifacts,
            requirements=requirements,
            authority=authority,
            worker=worker,
            fence=fence,
            expected_version=expected_version,
            cancellation_stop=cancellation_stop,
        )

    def _finalize_evidence(
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
        cancellation_stop: TrustedCancellationStop | None,
    ) -> FinalizeEvidenceResult:
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
            cancellation_stop=cancellation_stop,
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

    def _validate_adjudication_history(self) -> None:
        if not isinstance(self.adjudications, tuple):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="not_tuple",
            )
        if any(not isinstance(record, UnknownAdjudication) for record in self.adjudications):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="invalid_type",
            )
        if self.adjudications and self.state is not AttemptState.ATTEMPT_UNKNOWN:
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="only_allowed_for_unknown",
            )
        if any(record.attempt_id != self.id for record in self.adjudications):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="attempt_mismatch",
            )
        observation_digest = (
            self.unknown_observation.digest if self.unknown_observation is not None else None
        )
        if any(
            record.unknown_observation_digest != observation_digest
            for record in self.adjudications
        ):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="observation_mismatch",
            )
        if self.unknown_observation is not None and any(
            record.occurred_at < self.unknown_observation.recorded_at
            for record in self.adjudications
        ):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="before_unknown",
            )
        ids = tuple(record.id for record in self.adjudications)
        if len(ids) != len(set(ids)):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="duplicate_id",
            )
        expected_superseded_id: str | None = None
        for record in self.adjudications:
            if record.supersedes_adjudication_id != expected_superseded_id:
                raise DomainValidationError(
                    entity_type="attempt",
                    field="adjudications",
                    reason="supersession_mismatch",
                )
            expected_superseded_id = record.id
        if any(
            self.adjudications[index].occurred_at < self.adjudications[index - 1].occurred_at
            for index in range(1, len(self.adjudications))
        ):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="occurred_at_not_monotonic",
            )
        if any(
            record.decision is UnknownAdjudicationDecision.MARK_COMPLETED_FROM_VERIFIED_EVIDENCE
            for record in self.adjudications
        ):
            raise DomainValidationError(
                entity_type="attempt",
                field="adjudications",
                reason="verified_evidence_adjudication_not_supported",
            )


def _invalid_attempt(field: str, reason: str) -> None:
    raise DomainValidationError(
        entity_type="attempt",
        field=field,
        reason=reason,
    )
