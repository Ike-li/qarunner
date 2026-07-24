"""Stable errors exposed by the greenfield execution domain."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qarunner.domain.digest import Digest
    from qarunner.domain.event import AttemptEvent
    from qarunner.domain.worker import WorkerRef


class ArtifactValidationError(ValueError):
    """Raised when verified Artifact metadata is outside the frozen domain."""

    code = "artifact_invalid"

    def __init__(self, *, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"invalid artifact {field}: {reason}")


class DomainValidationError(ValueError):
    """Raised when a domain value cannot enter a trusted aggregate."""

    code = "domain_validation_error"

    def __init__(self, *, entity_type: str, field: str, reason: str) -> None:
        self.entity_type = entity_type
        self.field = field
        self.reason = reason
        super().__init__(f"invalid {entity_type}.{field}: {reason}")


class WorkerGenerationConflict(ValueError):
    """Raised when a Worker command does not use the current generation."""

    code = "worker_generation_conflict"

    def __init__(
        self,
        *,
        current_worker_id: str,
        current_generation: int,
        received_worker_id: str,
        received_generation: int,
        reason: str,
    ) -> None:
        self.current_worker_id = current_worker_id
        self.current_generation = current_generation
        self.received_worker_id = received_worker_id
        self.received_generation = received_generation
        self.reason = reason
        super().__init__(
            f"worker generation conflict for {current_worker_id}: {reason}; "
            f"current={current_generation}, received={received_generation}"
        )


class WorkerNotClaimable(ValueError):
    """Raised when scheduling targets a generation that cannot accept work."""

    code = "worker_not_claimable"

    def __init__(self, *, worker_id: str, generation: int, reason: str) -> None:
        self.worker_id = worker_id
        self.generation = generation
        self.reason = reason
        super().__init__(f"worker {worker_id}/{generation} cannot claim: {reason}")


class EvidenceNotReady(ValueError):
    """Raised when trusted facts are incomplete or contradictory."""

    code = "evidence_not_ready"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"evidence is not ready: {reason}")


class EvidenceDigestMismatch(ValueError):
    """Raised when a Worker proposal differs from the rebuilt manifest."""

    code = "evidence_digest_mismatch"

    def __init__(self, *, claimed: Digest, computed: Digest) -> None:
        self.claimed = claimed
        self.computed = computed
        super().__init__(f"claimed evidence root {claimed.value} differs from {computed.value}")


class EvidenceConflict(ValueError):
    """Raised when finalized Evidence is resubmitted with different content."""

    code = "evidence_conflict"

    def __init__(self, *, stored_root: Digest, received_root: Digest) -> None:
        self.stored_root = stored_root
        self.received_root = received_root
        super().__init__(
            f"evidence root {stored_root.value} is already finalized; "
            f"received {received_root.value}"
        )


class UnknownObservationConflict(ValueError):
    """Raised when one unknown observation ID is reused with changed facts."""

    code = "unknown_observation_conflict"

    def __init__(self, *, attempt_id: str, observation_id: str) -> None:
        self.attempt_id = attempt_id
        self.observation_id = observation_id
        super().__init__(
            f"attempt {attempt_id} received conflicting unknown observation {observation_id}"
        )


class AttemptUnknownReviewRequired(ValueError):
    """Raised when automatic recovery tries to retry an unknown Attempt."""

    code = "attempt_unknown_review_required"

    def __init__(
        self,
        *,
        run_id: str,
        attempt_id: str,
        fence: int,
        reason: str,
    ) -> None:
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.fence = fence
        self.reason = reason
        super().__init__(
            f"attempt {attempt_id} on run {run_id} requires review before retry: {reason}"
        )


class AdjudicationConflict(ValueError):
    """Raised when one adjudication ID is reused with changed content."""

    code = "adjudication_conflict"

    def __init__(self, *, attempt_id: str, adjudication_id: str) -> None:
        self.attempt_id = attempt_id
        self.adjudication_id = adjudication_id
        super().__init__(
            f"attempt {attempt_id} received conflicting adjudication {adjudication_id}"
        )


class RetryNotAllowed(ValueError):
    """Raised when no current adjudication authorizes a requested retry."""

    code = "retry_not_allowed"

    def __init__(
        self,
        *,
        run_id: str,
        attempt_id: str,
        adjudication_id: str,
        reason: str,
    ) -> None:
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.adjudication_id = adjudication_id
        self.reason = reason
        super().__init__(
            f"retry for attempt {attempt_id} on run {run_id} is not allowed: {reason}"
        )


class CancellationConflict(ValueError):
    """Raised when a Run already owns a different immutable cancellation intent."""

    code = "cancellation_conflict"

    def __init__(self, *, run_id: str, stored_key: str, received_key: str) -> None:
        self.run_id = run_id
        self.stored_key = stored_key
        self.received_key = received_key
        super().__init__(
            f"run {run_id} already owns cancellation {stored_key}; received {received_key}"
        )


class BatchCancellationConflict(ValueError):
    """Raised when a Batch already owns a different cancellation intent."""

    code = "batch_cancellation_conflict"

    def __init__(self, *, batch_id: str, stored_key: str, received_key: str) -> None:
        self.batch_id = batch_id
        self.stored_key = stored_key
        self.received_key = received_key
        super().__init__(
            f"batch {batch_id} already owns cancellation {stored_key}; received {received_key}"
        )


class AttemptConflict(ValueError):
    """Raised when a new commit reuses an existing Attempt identity."""

    code = "attempt_conflict"

    def __init__(self, *, run_id: str, attempt_id: str, reason: str) -> None:
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.reason = reason
        super().__init__(f"attempt {attempt_id} conflicts with run {run_id}: {reason}")


class CanonicalizationError(ValueError):
    """Raised when a value is outside the frozen canonical JSON domain."""

    code = "canonicalization_error"

    def __init__(self, *, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"cannot canonicalize {path}: {reason}")


class AssignmentConflict(ValueError):
    """Raised when an Assignment command does not match the Run reservation."""

    code = "assignment_conflict"

    def __init__(self, *, run_id: str, assignment_id: str, reason: str) -> None:
        self.run_id = run_id
        self.assignment_id = assignment_id
        self.reason = reason
        super().__init__(f"assignment {assignment_id} conflicts with run {run_id}: {reason}")


class SuiteConflict(ValueError):
    """Raised when a Suite lifecycle command is illegal for the current aggregate."""

    code = "suite_conflict"

    def __init__(self, *, suite_id: str, reason: str) -> None:
        self.suite_id = suite_id
        self.reason = reason
        super().__init__(f"suite {suite_id} conflicts: {reason}")


class InvalidTransition(ValueError):
    """Raised when a domain aggregate rejects a requested state edge."""

    code = "invalid_transition"

    def __init__(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_state: enum.StrEnum,
        requested_state: enum.StrEnum,
        current_version: int,
        expected_version: int,
    ) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current_state = current_state
        self.requested_state = requested_state
        self.current_version = current_version
        self.expected_version = expected_version
        super().__init__(
            f"{entity_type} {entity_id} cannot transition "
            f"from {current_state} to {requested_state}"
        )


class IdempotencyConflict(ValueError):
    """Raised when a scoped idempotency key is reused with different content."""

    code = "idempotency_conflict"

    def __init__(
        self,
        *,
        scope: str,
        key: str,
        stored_digest: Digest,
        received_digest: Digest,
    ) -> None:
        self.scope = scope
        self.key = key
        self.stored_digest = stored_digest
        self.received_digest = received_digest
        super().__init__(f"idempotency key {scope}/{key} is already bound to another digest")


class EventConflict(ValueError):
    """Raised when an event ID or sequence is reused with different content."""

    code = "event_conflict"

    def __init__(
        self,
        *,
        attempt_id: str,
        stored_event: AttemptEvent,
        received_event: AttemptEvent,
    ) -> None:
        self.attempt_id = attempt_id
        self.stored_event = stored_event
        self.received_event = received_event
        super().__init__(f"attempt {attempt_id} received conflicting event identity")


class AttemptEventRejected(ValueError):
    """Raised when a terminal Attempt receives a new event fact."""

    code = "attempt_event_rejected"

    def __init__(self, *, attempt_id: str, reason: str) -> None:
        self.attempt_id = attempt_id
        self.reason = reason
        super().__init__(f"attempt {attempt_id} rejected a new event: {reason}")


class StaleFence(ValueError):
    """Raised when an Attempt command carries a non-current fence."""

    code = "stale_fence"

    def __init__(self, *, attempt_id: str, current_fence: int, received_fence: int) -> None:
        self.attempt_id = attempt_id
        self.current_fence = current_fence
        self.received_fence = received_fence
        super().__init__(
            f"attempt {attempt_id} has fence {current_fence}; received {received_fence}"
        )


class StaleGeneration(ValueError):
    """Raised when an Attempt command comes from a non-current Worker generation."""

    code = "stale_generation"

    def __init__(
        self,
        *,
        attempt_id: str,
        current_worker: WorkerRef,
        received_worker: WorkerRef,
    ) -> None:
        self.attempt_id = attempt_id
        self.current_worker = current_worker
        self.received_worker = received_worker
        super().__init__(
            f"attempt {attempt_id} belongs to {current_worker}; received {received_worker}"
        )


class VersionConflict(ValueError):
    """Raised when a command's expected aggregate version is stale."""

    code = "version_conflict"

    def __init__(
        self,
        *,
        entity_type: str,
        entity_id: str,
        current_version: int,
        expected_version: int,
    ) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current_version = current_version
        self.expected_version = expected_version
        super().__init__(
            f"{entity_type} {entity_id} has version {current_version}; expected {expected_version}"
        )


def ensure_expected_version(
    *, entity_type: str, entity_id: str, current_version: int, expected_version: int
) -> None:
    """Apply the shared CAS precondition for immutable domain commands."""
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise DomainValidationError(
            entity_type=entity_type,
            field="expected_version",
            reason="not_integer",
        )
    if expected_version < 0:
        raise DomainValidationError(
            entity_type=entity_type,
            field="expected_version",
            reason="negative",
        )
    if expected_version != current_version:
        raise VersionConflict(
            entity_type=entity_type,
            entity_id=entity_id,
            current_version=current_version,
            expected_version=expected_version,
        )
