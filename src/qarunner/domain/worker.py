"""Worker identity and generation lifecycle."""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from qarunner.domain.digest import Digest
from qarunner.domain.errors import (
    DomainValidationError,
    InvalidTransition,
    WorkerGenerationConflict,
    WorkerNotClaimable,
    ensure_expected_version,
)

if TYPE_CHECKING:
    from qarunner.domain.authority import WorkerAuthority


class WorkerState(enum.StrEnum):
    """Control-plane states of one immutable Worker generation."""

    REGISTERING = "registering"
    READY = "ready"
    BUSY = "busy"
    DRAINING = "draining"
    QUARANTINED = "quarantined"
    OFFLINE = "offline"
    RETIRED = "retired"


WORKER_TRANSITIONS: Final[Mapping[WorkerState, frozenset[WorkerState]]] = MappingProxyType(
    {
        WorkerState.REGISTERING: frozenset({WorkerState.READY, WorkerState.QUARANTINED}),
        WorkerState.READY: frozenset(
            {
                WorkerState.DRAINING,
                WorkerState.OFFLINE,
                WorkerState.QUARANTINED,
            }
        ),
        WorkerState.BUSY: frozenset(
            {
                WorkerState.DRAINING,
                WorkerState.OFFLINE,
                WorkerState.QUARANTINED,
            }
        ),
        WorkerState.DRAINING: frozenset(
            {WorkerState.RETIRED, WorkerState.OFFLINE, WorkerState.QUARANTINED}
        ),
        WorkerState.OFFLINE: frozenset({WorkerState.QUARANTINED}),
        WorkerState.QUARANTINED: frozenset({WorkerState.RETIRED}),
        WorkerState.RETIRED: frozenset(),
    }
)
WORKER_TERMINAL_STATES: Final = frozenset({WorkerState.RETIRED})


@dataclass(frozen=True, slots=True)
class WorkerRef:
    """Identity of one immutable Worker generation."""

    worker_id: str
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str):
            raise DomainValidationError(
                entity_type="worker_ref",
                field="worker_id",
                reason="not_string",
            )
        if not self.worker_id.strip():
            raise DomainValidationError(
                entity_type="worker_ref",
                field="worker_id",
                reason="empty_id",
            )
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise DomainValidationError(
                entity_type="worker_ref",
                field="generation",
                reason="not_integer",
            )
        if self.generation <= 0:
            raise DomainValidationError(
                entity_type="worker_ref",
                field="generation",
                reason="not_positive",
            )


@dataclass(frozen=True, slots=True)
class ReconcileWorkerFacts:
    """Trusted facts used to derive state during Worker reconcile."""

    active_assignments: int
    drain_requested: bool

    def __post_init__(self) -> None:
        if isinstance(self.active_assignments, bool) or not isinstance(
            self.active_assignments, int
        ):
            raise DomainValidationError(
                entity_type="reconcile_worker_facts",
                field="active_assignments",
                reason="not_integer",
            )
        if self.active_assignments < 0:
            raise DomainValidationError(
                entity_type="reconcile_worker_facts",
                field="active_assignments",
                reason="negative",
            )
        if not isinstance(self.drain_requested, bool):
            raise DomainValidationError(
                entity_type="reconcile_worker_facts",
                field="drain_requested",
                reason="not_boolean",
            )


@dataclass(frozen=True, slots=True)
class WorkerGeneration:
    """Immutable control-plane facts for one Worker rebuild generation."""

    ref: WorkerRef
    host_id: str
    pool_id: str
    cert_serial: str
    agent_version: str
    capabilities_digest: Digest
    state: WorkerState
    registered_at: datetime
    state_changed_at: datetime
    last_seen_at: datetime | None
    retired_at: datetime | None
    version: int

    def __post_init__(self) -> None:
        for field, value in (
            ("host_id", self.host_id),
            ("pool_id", self.pool_id),
            ("cert_serial", self.cert_serial),
            ("agent_version", self.agent_version),
        ):
            if not isinstance(value, str):
                raise DomainValidationError(
                    entity_type="worker_generation",
                    field=field,
                    reason="not_string",
                )
            if not value.strip():
                raise DomainValidationError(
                    entity_type="worker_generation",
                    field=field,
                    reason="empty_value",
                )
        if not isinstance(self.capabilities_digest, Digest):
            raise DomainValidationError(
                entity_type="worker_generation",
                field="capabilities_digest",
                reason="not_digest",
            )
        if not isinstance(self.state, WorkerState):
            raise DomainValidationError(
                entity_type="worker_generation",
                field="state",
                reason="unknown",
            )
        if self.version < 0:
            raise DomainValidationError(
                entity_type="worker_generation",
                field="version",
                reason="negative",
            )
        _ensure_utc(field="registered_at", value=self.registered_at)
        _ensure_utc(field="state_changed_at", value=self.state_changed_at)
        if self.state_changed_at < self.registered_at:
            raise DomainValidationError(
                entity_type="worker_generation",
                field="state_changed_at",
                reason="before_registration",
            )
        if self.last_seen_at is not None:
            _ensure_utc(field="last_seen_at", value=self.last_seen_at)
            if self.last_seen_at < self.registered_at:
                raise DomainValidationError(
                    entity_type="worker_generation",
                    field="last_seen_at",
                    reason="before_registration",
                )
        if self.retired_at is not None:
            _ensure_utc(field="retired_at", value=self.retired_at)
            if self.retired_at != self.state_changed_at:
                raise DomainValidationError(
                    entity_type="worker_generation",
                    field="retired_at",
                    reason="must_equal_state_changed_at",
                )
        if (self.state is WorkerState.RETIRED) != (self.retired_at is not None):
            raise DomainValidationError(
                entity_type="worker_generation",
                field="retired_at",
                reason="must_match_retired_state",
            )

    @classmethod
    def register(
        cls,
        *,
        ref: WorkerRef,
        host_id: str,
        pool_id: str,
        cert_serial: str,
        agent_version: str,
        capabilities_digest: Digest,
        registered_at: datetime,
    ) -> WorkerGeneration:
        """Create a generation that must pass registration before claiming."""
        return cls(
            ref=ref,
            host_id=host_id,
            pool_id=pool_id,
            cert_serial=cert_serial,
            agent_version=agent_version,
            capabilities_digest=capabilities_digest,
            state=WorkerState.REGISTERING,
            registered_at=registered_at,
            state_changed_at=registered_at,
            last_seen_at=None,
            retired_at=None,
            version=0,
        )

    def transition(
        self,
        target: WorkerState,
        *,
        authority: WorkerAuthority,
        expected_version: int,
        occurred_at: datetime,
    ) -> WorkerGeneration:
        """Apply a declared state edge; offline recovery requires reconcile()."""
        self._ensure_authority(authority)
        ensure_expected_version(
            entity_type="worker_generation",
            entity_id=self.ref.worker_id,
            current_version=self.version,
            expected_version=expected_version,
        )
        self._ensure_event_time(field="occurred_at", value=occurred_at)
        if target not in WORKER_TRANSITIONS[self.state]:
            raise InvalidTransition(
                entity_type="worker_generation",
                entity_id=self.ref.worker_id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(
            self,
            state=target,
            state_changed_at=occurred_at,
            last_seen_at=occurred_at if target is WorkerState.READY else self.last_seen_at,
            retired_at=occurred_at if target is WorkerState.RETIRED else None,
            version=self.version + 1,
        )

    def reconcile(
        self,
        *,
        authority: WorkerAuthority,
        authenticated_ref: WorkerRef,
        facts: ReconcileWorkerFacts,
        expected_version: int,
        observed_at: datetime,
    ) -> WorkerGeneration:
        """Recover an offline current generation after authenticated reconcile."""
        self._ensure_authority(authority)
        if authenticated_ref != self.ref:
            raise WorkerGenerationConflict(
                current_worker_id=self.ref.worker_id,
                current_generation=self.ref.generation,
                received_worker_id=authenticated_ref.worker_id,
                received_generation=authenticated_ref.generation,
                reason="generation_not_current",
            )
        ensure_expected_version(
            entity_type="worker_generation",
            entity_id=self.ref.worker_id,
            current_version=self.version,
            expected_version=expected_version,
        )
        self._ensure_observation_time(field="observed_at", value=observed_at)
        target = _worker_state_from_facts(facts)
        if self.state is not WorkerState.OFFLINE:
            raise InvalidTransition(
                entity_type="worker_generation",
                entity_id=self.ref.worker_id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(
            self,
            state=target,
            state_changed_at=observed_at,
            last_seen_at=observed_at,
            version=self.version + 1,
        )

    def observe_assignments(
        self,
        *,
        authority: WorkerAuthority,
        active_assignments: int,
        expected_version: int,
        observed_at: datetime,
    ) -> WorkerGeneration:
        """Derive READY/BUSY from trusted active Assignment facts."""
        self._ensure_authority(authority)
        facts = ReconcileWorkerFacts(
            active_assignments=active_assignments,
            drain_requested=False,
        )
        ensure_expected_version(
            entity_type="worker_generation",
            entity_id=self.ref.worker_id,
            current_version=self.version,
            expected_version=expected_version,
        )
        self._ensure_observation_time(field="observed_at", value=observed_at)
        target = _worker_state_from_facts(facts)
        allowed_states = frozenset({WorkerState.READY, WorkerState.BUSY})
        if self.state not in allowed_states:
            raise InvalidTransition(
                entity_type="worker_generation",
                entity_id=self.ref.worker_id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(
            self,
            state=target,
            state_changed_at=observed_at,
            last_seen_at=observed_at,
            version=self.version + 1,
        )

    def claimable_ref(self, *, authority: WorkerAuthority) -> WorkerRef:
        """Return the current identity only when the generation may claim work."""
        self._ensure_authority(authority)
        if self.state not in {WorkerState.READY, WorkerState.BUSY}:
            raise WorkerNotClaimable(
                worker_id=self.ref.worker_id,
                generation=self.ref.generation,
                reason=self.state.value,
            )
        return self.ref

    def _ensure_authority(self, authority: WorkerAuthority) -> None:
        if self.ref != authority.current_ref:
            raise WorkerGenerationConflict(
                current_worker_id=authority.current_ref.worker_id,
                current_generation=authority.current_ref.generation,
                received_worker_id=self.ref.worker_id,
                received_generation=self.ref.generation,
                reason="generation_not_current",
            )

    def _ensure_event_time(self, *, field: str, value: datetime) -> None:
        _ensure_utc(field=field, value=value)
        if value < self.state_changed_at:
            raise DomainValidationError(
                entity_type="worker_generation",
                field=field,
                reason="before_current_state",
            )

    def _ensure_observation_time(self, *, field: str, value: datetime) -> None:
        self._ensure_event_time(field=field, value=value)
        if self.last_seen_at is not None and value < self.last_seen_at:
            raise DomainValidationError(
                entity_type="worker_generation",
                field=field,
                reason="before_last_seen",
            )


def _ensure_utc(*, field: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise DomainValidationError(
            entity_type="worker_generation",
            field=field,
            reason="not_datetime",
        )
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DomainValidationError(
            entity_type="worker_generation",
            field=field,
            reason="not_utc",
        )


def _worker_state_from_facts(facts: ReconcileWorkerFacts) -> WorkerState:
    if facts.drain_requested:
        return WorkerState.DRAINING
    if facts.active_assignments > 0:
        return WorkerState.BUSY
    return WorkerState.READY
