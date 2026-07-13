"""Immutable facts for control-plane unknown classifications."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest
from qarunner.domain.errors import DomainValidationError


class UnknownReason(enum.StrEnum):
    """Why the control plane cannot prove a committed Attempt stopped."""

    WORKER_LOST_AFTER_COMMIT = "worker_lost_after_commit"
    CANCEL_STOP_UNPROVEN = "cancel_stop_unproven"
    LEASE_STOP_UNPROVEN = "lease_stop_unproven"
    RECONCILE_AMBIGUOUS = "reconcile_ambiguous"
    EXECUTION_STOP_UNPROVEN = "execution_stop_unproven"


class UnknownSource(enum.StrEnum):
    """Trusted control-plane component that recorded the observation."""

    COORDINATOR = "coordinator"
    CANCEL_CONVERGENCE = "cancel_convergence"
    LEASE_WATCHDOG = "lease_watchdog"
    RECONCILER = "reconciler"


@dataclass(frozen=True, slots=True)
class UnknownObservation:
    """Frozen review input attached when an Attempt becomes unknown."""

    id: str
    reason: UnknownReason
    source: UnknownSource
    review_basis_digest: Digest
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.id, str):
            _invalid("id", "not_string")
        if not self.id.strip():
            _invalid("id", "empty")
        if not isinstance(self.reason, UnknownReason):
            _invalid("reason", "unknown")
        if not isinstance(self.source, UnknownSource):
            _invalid("source", "unknown")
        if not isinstance(self.review_basis_digest, Digest):
            _invalid("review_basis_digest", "not_digest")
        if not isinstance(self.recorded_at, datetime):
            _invalid("recorded_at", "not_datetime")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() != timedelta(0):
            _invalid("recorded_at", "not_utc")


def _invalid(field: str, reason: str) -> None:
    raise DomainValidationError(
        entity_type="unknown_observation",
        field=field,
        reason=reason,
    )
