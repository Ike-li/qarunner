"""Immutable cancellation intent owned by a Run."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.worker import WorkerRef


class CancellationSource(enum.StrEnum):
    """Why the control plane recorded cancellation intent."""

    USER_REQUEST = "user_request"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    POLICY_ENFORCEMENT = "policy_enforcement"


class BatchCancellationScopeKind(enum.StrEnum):
    """Authoritative Batch scope frozen when cancellation intent is accepted."""

    PRE_PLAN = "pre_plan"
    FROZEN_PLAN = "frozen_plan"


@dataclass(frozen=True, slots=True)
class BatchCancellationScope:
    """Server-derived pre-plan or frozen-plan cancellation scope."""

    kind: BatchCancellationScopeKind
    preplan_scope_digest: Digest | None
    manifest_digest: Digest | None
    shard_plan_version: int | None
    shard_plan_digest: Digest | None
    canonical_run_set_digest: Digest | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BatchCancellationScopeKind):
            _invalid("batch_cancellation_scope", "kind", "unknown")
        if self.kind is BatchCancellationScopeKind.PRE_PLAN:
            if not isinstance(self.preplan_scope_digest, Digest):
                _invalid(
                    "batch_cancellation_scope",
                    "preplan_scope_digest",
                    "required_for_kind",
                )
            for field in (
                "manifest_digest",
                "shard_plan_version",
                "shard_plan_digest",
                "canonical_run_set_digest",
            ):
                if getattr(self, field) is not None:
                    _invalid("batch_cancellation_scope", field, "forbidden_for_kind")
            return
        if self.preplan_scope_digest is not None:
            _invalid(
                "batch_cancellation_scope",
                "preplan_scope_digest",
                "forbidden_for_kind",
            )
        for field in ("manifest_digest", "shard_plan_digest", "canonical_run_set_digest"):
            if not isinstance(getattr(self, field), Digest):
                _invalid("batch_cancellation_scope", field, "required_for_kind")
        _require_nonnegative_version(
            "batch_cancellation_scope",
            "shard_plan_version",
            self.shard_plan_version,
        )


@dataclass(frozen=True, slots=True)
class BatchCancellationIntent:
    """Immutable `qep.batch-cancellation-intent.v1` fact."""

    batch_id: str
    project_id: str
    suite_revision_id: str
    source_batch_version: int
    idempotency_key: str
    source: CancellationSource
    actor_id: str
    reason: str
    authorization_digest: Digest
    scope: BatchCancellationScope
    recorded_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "batch_id",
            "project_id",
            "suite_revision_id",
            "idempotency_key",
            "actor_id",
            "reason",
        ):
            _require_nonempty_string("batch_cancellation_intent", field, getattr(self, field))
        _require_nonnegative_version(
            "batch_cancellation_intent",
            "source_batch_version",
            self.source_batch_version,
        )
        if not isinstance(self.source, CancellationSource):
            _invalid("batch_cancellation_intent", "source", "unknown")
        if not isinstance(self.authorization_digest, Digest):
            _invalid("batch_cancellation_intent", "authorization_digest", "not_digest")
        if not isinstance(self.scope, BatchCancellationScope):
            _invalid("batch_cancellation_intent", "scope", "invalid_type")
        _require_utc("batch_cancellation_intent", "recorded_at", self.recorded_at)

    @property
    def request_digest(self) -> Digest:
        """Bind caller-controlled identity while excluding server-derived metadata."""
        return canonical_digest(
            schema_version="qep.batch-cancellation-request.v1",
            payload={
                "batch_id": self.batch_id,
                "source_batch_version": self.source_batch_version,
                "idempotency_key": self.idempotency_key,
                "source": self.source.value,
                "actor_id": self.actor_id,
                "reason": self.reason,
            },
        )

    @property
    def digest(self) -> Digest:
        """Bind request, authority, authoritative scope, and server record time."""
        return canonical_digest(
            schema_version="qep.batch-cancellation-intent.v1",
            payload={
                "batch_id": self.batch_id,
                "project_id": self.project_id,
                "suite_revision_id": self.suite_revision_id,
                "source_batch_version": self.source_batch_version,
                "request_digest": self.request_digest.value,
                "authorization_digest": self.authorization_digest.value,
                "scope_kind": self.scope.kind.value,
                "preplan_scope_digest": _optional_digest_value(self.scope.preplan_scope_digest),
                "manifest_digest": _optional_digest_value(self.scope.manifest_digest),
                "shard_plan_version": self.scope.shard_plan_version,
                "shard_plan_digest": _optional_digest_value(self.scope.shard_plan_digest),
                "canonical_run_set_digest": _optional_digest_value(
                    self.scope.canonical_run_set_digest
                ),
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class CancellationIntent:
    """A request to stop work, distinct from a proven cancellation outcome."""

    run_id: str
    idempotency_key: str
    source: CancellationSource
    actor_id: str
    reason: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        for field in ("run_id", "idempotency_key", "actor_id", "reason"):
            _require_nonempty_string("cancellation_intent", field, getattr(self, field))
        if not isinstance(self.source, CancellationSource):
            _invalid("cancellation_intent", "source", "unknown")
        _require_utc("cancellation_intent", "recorded_at", self.recorded_at)

    @property
    def request_digest(self) -> Digest:
        """Bind replay identity to caller-controlled cancellation content."""
        return canonical_digest(
            schema_version="qep.cancellation-request.v1",
            payload={
                "run_id": self.run_id,
                "idempotency_key": self.idempotency_key,
                "source": self.source.value,
                "actor_id": self.actor_id,
                "reason": self.reason,
            },
        )

    @property
    def digest(self) -> Digest:
        """Digest the immutable cancellation fact including server record time."""
        return canonical_digest(
            schema_version="qep.cancellation-intent.v1",
            payload={
                "request_digest": self.request_digest.value,
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class TrustedCancellationStop:
    """Control-plane proof that execution and approved SUT access both stopped."""

    run_id: str
    attempt_id: str
    fence: int
    worker: WorkerRef
    cancellation_intent_digest: Digest
    source_event_id: str
    process_stopped_at: datetime
    sut_access_stopped_at: datetime
    recorded_at: datetime

    def __post_init__(self) -> None:
        for field in ("run_id", "attempt_id", "source_event_id"):
            _require_nonempty_string("trusted_cancellation_stop", field, getattr(self, field))
        if isinstance(self.fence, bool) or not isinstance(self.fence, int):
            _invalid("trusted_cancellation_stop", "fence", "not_integer")
        if self.fence < 1:
            _invalid("trusted_cancellation_stop", "fence", "not_positive")
        if not isinstance(self.worker, WorkerRef):
            _invalid("trusted_cancellation_stop", "worker", "invalid_type")
        if not isinstance(self.cancellation_intent_digest, Digest):
            _invalid(
                "trusted_cancellation_stop",
                "cancellation_intent_digest",
                "not_digest",
            )
        for field in ("process_stopped_at", "sut_access_stopped_at", "recorded_at"):
            _require_utc("trusted_cancellation_stop", field, getattr(self, field))
        if self.recorded_at < max(self.process_stopped_at, self.sut_access_stopped_at):
            _invalid(
                "trusted_cancellation_stop",
                "recorded_at",
                "before_stop_facts",
            )

    @property
    def digest(self) -> Digest:
        """Bind the complete cancellation convergence proof."""
        return canonical_digest(
            schema_version="qep.trusted-cancellation-stop.v1",
            payload={
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "fence": self.fence,
                "worker": {
                    "worker_id": self.worker.worker_id,
                    "generation": self.worker.generation,
                },
                "cancellation_intent_digest": self.cancellation_intent_digest.value,
                "source_event_id": self.source_event_id,
                "process_stopped_at": self.process_stopped_at.isoformat().replace("+00:00", "Z"),
                "sut_access_stopped_at": self.sut_access_stopped_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty")


def _require_utc(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid(entity_type, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid(entity_type, field, "not_utc")


def _require_nonnegative_version(entity_type: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(entity_type, field, "not_integer")
    if value < 0:
        _invalid(entity_type, field, "negative")


def _invalid(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity_type, field=field, reason=reason)


def _optional_digest_value(digest: Digest | None) -> str | None:
    return None if digest is None else digest.value
