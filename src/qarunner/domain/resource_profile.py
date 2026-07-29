"""Resource Profile + planning budget (T-M6-SHARD-002 / T-M6-ADMIT-001).

Pure domain: requests/limits vectors, internal_workers amplification,
concurrent reservation, budget clamping, stable oversell reasons.
No adapters, no queue, no Schedule surface.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Sequence
from dataclasses import dataclass

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError

RESOURCE_PROFILE_SCHEMA_VERSION = "qep.resource-profile.v1"
_DIMENSIONS = (
    "cpu_millis",
    "memory_bytes",
    "pid_slots",
    "ephemeral_storage_bytes",
    "browser_slots",
)


class ShardBudgetDecisionKind(enum.StrEnum):
    """Outcome of planning/admission against a host budget."""

    ADMITTED = "admitted"
    CLAMPED = "clamped"
    INFEASIBLE = "infeasible"


@dataclass(frozen=True, slots=True)
class ResourceVector:
    """Multi-dimensional resource units for requests / limits / capacity.

    Aligns with DES §10.1 quantitative dimensions used by MVP admission.
    """

    cpu_millis: int
    memory_bytes: int
    pid_slots: int
    ephemeral_storage_bytes: int
    browser_slots: int

    def __post_init__(self) -> None:
        entity = "resource_vector"
        for name in _DIMENSIONS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DomainValidationError(
                    entity_type=entity, field=name, reason="not_nonnegative"
                )

    def scaled_by(self, factor: int) -> ResourceVector:
        """Amplify reservation by ``internal_workers`` (or other positive factor)."""
        if isinstance(factor, bool) or not isinstance(factor, int) or factor < 1:
            raise DomainValidationError(
                entity_type="resource_vector", field="factor", reason="not_positive"
            )
        return ResourceVector(
            cpu_millis=self.cpu_millis * factor,
            memory_bytes=self.memory_bytes * factor,
            pid_slots=self.pid_slots * factor,
            ephemeral_storage_bytes=self.ephemeral_storage_bytes * factor,
            browser_slots=self.browser_slots * factor,
        )

    def added(self, other: ResourceVector) -> ResourceVector:
        if not isinstance(other, ResourceVector):
            raise DomainValidationError(
                entity_type="resource_vector", field="other", reason="invalid_type"
            )
        return ResourceVector(
            cpu_millis=self.cpu_millis + other.cpu_millis,
            memory_bytes=self.memory_bytes + other.memory_bytes,
            pid_slots=self.pid_slots + other.pid_slots,
            ephemeral_storage_bytes=self.ephemeral_storage_bytes + other.ephemeral_storage_bytes,
            browser_slots=self.browser_slots + other.browser_slots,
        )

    def is_zero(self) -> bool:
        return all(getattr(self, name) == 0 for name in _DIMENSIONS)

    def fits_within(self, capacity: ResourceVector) -> bool:
        return all(getattr(self, name) <= getattr(capacity, name) for name in _DIMENSIONS)

    def first_exceeding_dimension(self, capacity: ResourceVector) -> str | None:
        """Stable first dimension where self exceeds capacity (DES order)."""
        for name in _DIMENSIONS:
            if getattr(self, name) > getattr(capacity, name):
                return name
        return None

    def max_concurrent_within(self, capacity: ResourceVector) -> tuple[int, str | None]:
        """Largest N such that ``self.scaled_by(N)`` fits capacity.

        Returns ``(n, limiting_dimension)``. Zero-cost dimensions are ignored;
        all-zero cost is treated as unlimited on capacity (caller must reject).
        """
        limits: list[tuple[int, str]] = []
        for name in _DIMENSIONS:
            unit = getattr(self, name)
            if unit <= 0:
                continue
            limits.append((getattr(capacity, name) // unit, name))
        if not limits:
            return (math.inf, None)  # type: ignore[return-value]
        n, dim = min(limits, key=lambda item: item[0])
        return n, dim

    def to_payload(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in _DIMENSIONS}


@dataclass(frozen=True, slots=True)
class ResourceProfileSpec:
    """Approved Resource Profile vocabulary (DES §10 / ``qep_resource_profiles``).

    ``requests`` is the scheduling reservation; ``limits`` is the hard ceiling.
    ``internal_workers`` amplifies concurrent cost — it is not free capacity.
    """

    profile_id: str
    name: str
    profile_version: int
    framework: str
    requests: ResourceVector
    limits: ResourceVector
    internal_workers: int
    security_profile_id: str

    def __post_init__(self) -> None:
        entity = "resource_profile_spec"
        for name in ("profile_id", "name", "framework", "security_profile_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(entity_type=entity, field=name, reason="invalid")
        if (
            isinstance(self.profile_version, bool)
            or not isinstance(self.profile_version, int)
            or self.profile_version < 1
        ):
            raise DomainValidationError(
                entity_type=entity, field="profile_version", reason="not_positive"
            )
        if not isinstance(self.requests, ResourceVector):
            raise DomainValidationError(
                entity_type=entity, field="requests", reason="invalid_type"
            )
        if not isinstance(self.limits, ResourceVector):
            raise DomainValidationError(entity_type=entity, field="limits", reason="invalid_type")
        if (
            isinstance(self.internal_workers, bool)
            or not isinstance(self.internal_workers, int)
            or self.internal_workers < 1
        ):
            raise DomainValidationError(
                entity_type=entity, field="internal_workers", reason="not_positive"
            )
        # DES §10.1: cannot schedule with zero requests after setting limits.
        if self.requests.is_zero():
            raise DomainValidationError(
                entity_type=entity, field="requests", reason="zero_request_oversell"
            )
        exceeding = self.requests.first_exceeding_dimension(self.limits)
        if exceeding is not None:
            raise DomainValidationError(
                entity_type=entity, field="limits", reason=f"below_requests:{exceeding}"
            )

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version=RESOURCE_PROFILE_SCHEMA_VERSION,
            payload={
                "framework": self.framework,
                "internal_workers": self.internal_workers,
                "limits": self.limits.to_payload(),
                "name": self.name,
                "profile_id": self.profile_id,
                "profile_version": self.profile_version,
                "requests": self.requests.to_payload(),
                "security_profile_id": self.security_profile_id,
            },
        )

    def concurrent_reservation(self) -> ResourceVector:
        """Per-shard concurrent cost = requests × internal_workers."""
        return self.requests.scaled_by(self.internal_workers)


@dataclass(frozen=True, slots=True)
class ShardPlanningBudget:
    """Host budget + profile map shared by shard count planning (T-M6-SHARD-002)."""

    target_shard_duration_ms: int
    min_shards: int
    max_shards: int
    host_capacity: ResourceVector
    profiles: tuple[ResourceProfileSpec, ...]

    def __post_init__(self) -> None:
        entity = "shard_planning_budget"
        if (
            isinstance(self.target_shard_duration_ms, bool)
            or not isinstance(self.target_shard_duration_ms, int)
            or self.target_shard_duration_ms < 1
        ):
            raise DomainValidationError(
                entity_type=entity, field="target_shard_duration_ms", reason="not_positive"
            )
        if (
            isinstance(self.min_shards, bool)
            or not isinstance(self.min_shards, int)
            or self.min_shards < 1
        ):
            raise DomainValidationError(
                entity_type=entity, field="min_shards", reason="not_positive"
            )
        if (
            isinstance(self.max_shards, bool)
            or not isinstance(self.max_shards, int)
            or self.max_shards < self.min_shards
        ):
            raise DomainValidationError(
                entity_type=entity, field="max_shards", reason="below_min_shards"
            )
        if not isinstance(self.host_capacity, ResourceVector):
            raise DomainValidationError(
                entity_type=entity, field="host_capacity", reason="invalid_type"
            )
        if not isinstance(self.profiles, tuple) or not self.profiles:
            raise DomainValidationError(entity_type=entity, field="profiles", reason="empty")
        if any(not isinstance(profile, ResourceProfileSpec) for profile in self.profiles):
            raise DomainValidationError(
                entity_type=entity, field="profiles", reason="invalid_type"
            )
        ids = tuple(profile.profile_id for profile in self.profiles)
        if len(ids) != len(set(ids)):
            raise DomainValidationError(
                entity_type=entity, field="profiles", reason="duplicate_profile_id"
            )

    def profile(self, profile_id: str) -> ResourceProfileSpec:
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        raise DomainValidationError(
            entity_type="shard_planning_budget",
            field="resource_profile_id",
            reason="unknown_profile",
        )


@dataclass(frozen=True, slots=True)
class ShardBudgetDecision:
    """Planning/admission outcome under a shared host budget."""

    kind: ShardBudgetDecisionKind
    desired_shard_count: int
    allowed_shard_count: int
    limiting_dimension: str | None
    reason: str | None

    def __post_init__(self) -> None:
        entity = "shard_budget_decision"
        if not isinstance(self.kind, ShardBudgetDecisionKind):
            raise DomainValidationError(entity_type=entity, field="kind", reason="invalid")
        for name in ("desired_shard_count", "allowed_shard_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DomainValidationError(
                    entity_type=entity, field=name, reason="not_nonnegative"
                )
        if self.limiting_dimension is not None and (
            not isinstance(self.limiting_dimension, str) or not self.limiting_dimension.strip()
        ):
            raise DomainValidationError(
                entity_type=entity, field="limiting_dimension", reason="invalid"
            )
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise DomainValidationError(entity_type=entity, field="reason", reason="invalid")


def _desired_shard_count(
    *,
    total_estimated_duration_ms: int,
    target_shard_duration_ms: int,
    min_shards: int,
    max_shards: int,
) -> int:
    if (
        isinstance(total_estimated_duration_ms, bool)
        or not isinstance(total_estimated_duration_ms, int)
        or total_estimated_duration_ms < 0
    ):
        raise DomainValidationError(
            entity_type="shard_planning_budget",
            field="total_estimated_duration_ms",
            reason="not_nonnegative",
        )
    if total_estimated_duration_ms == 0:
        base = min_shards
    else:
        base = math.ceil(total_estimated_duration_ms / target_shard_duration_ms)
    return max(min_shards, min(max_shards, base))


def plan_shard_count_under_budget(
    *,
    total_estimated_duration_ms: int,
    resource_profile_id: str,
    budget: ShardPlanningBudget,
) -> ShardBudgetDecision:
    """Clamp desired shard count by host capacity for one profile (T-M6-SHARD-002).

    Concurrent cost per shard = profile.requests × internal_workers. Shard count,
    Profile requests, and framework-internal concurrency share the same budget.
    """
    if not isinstance(budget, ShardPlanningBudget):
        raise DomainValidationError(
            entity_type="shard_planning_budget", field="budget", reason="invalid_type"
        )
    profile = budget.profile(resource_profile_id)
    desired = _desired_shard_count(
        total_estimated_duration_ms=total_estimated_duration_ms,
        target_shard_duration_ms=budget.target_shard_duration_ms,
        min_shards=budget.min_shards,
        max_shards=budget.max_shards,
    )
    unit_cost = profile.concurrent_reservation()
    max_by_budget, limiting = unit_cost.max_concurrent_within(budget.host_capacity)
    if isinstance(max_by_budget, float):  # all-zero cost — should not happen after validation
        max_by_budget = 0
        limiting = "cpu_millis"

    if max_by_budget < 1:
        return ShardBudgetDecision(
            kind=ShardBudgetDecisionKind.INFEASIBLE,
            desired_shard_count=desired,
            allowed_shard_count=0,
            limiting_dimension=limiting
            or unit_cost.first_exceeding_dimension(budget.host_capacity),
            reason="oversell",
        )

    allowed = min(desired, max_by_budget)
    if allowed < desired:
        return ShardBudgetDecision(
            kind=ShardBudgetDecisionKind.CLAMPED,
            desired_shard_count=desired,
            allowed_shard_count=allowed,
            limiting_dimension=limiting,
            reason="host_budget",
        )
    return ShardBudgetDecision(
        kind=ShardBudgetDecisionKind.ADMITTED,
        desired_shard_count=desired,
        allowed_shard_count=allowed,
        limiting_dimension=None,
        reason=None,
    )


def admit_concurrent_shards(
    *,
    shard_profile_ids: Sequence[str],
    budget: ShardPlanningBudget,
) -> ShardBudgetDecision:
    """Fail-closed concurrent admission for a proposed shard set (T-M6-ADMIT-001).

    All-or-nothing: either every listed shard's concurrent reservation fits the
    host capacity together, or the set is infeasible with a stable dimension.
    """
    if not isinstance(budget, ShardPlanningBudget):
        raise DomainValidationError(
            entity_type="shard_planning_budget", field="budget", reason="invalid_type"
        )
    if not isinstance(shard_profile_ids, (tuple, list)):
        raise DomainValidationError(
            entity_type="shard_planning_budget",
            field="shard_profile_ids",
            reason="invalid_type",
        )
    if len(shard_profile_ids) == 0:
        raise DomainValidationError(
            entity_type="shard_planning_budget",
            field="shard_profile_ids",
            reason="empty",
        )
    if any(
        not isinstance(profile_id, str) or not profile_id.strip()
        for profile_id in shard_profile_ids
    ):
        raise DomainValidationError(
            entity_type="shard_planning_budget",
            field="shard_profile_ids",
            reason="invalid_entry",
        )

    total = ResourceVector(
        cpu_millis=0,
        memory_bytes=0,
        pid_slots=0,
        ephemeral_storage_bytes=0,
        browser_slots=0,
    )
    for profile_id in shard_profile_ids:
        profile = budget.profile(profile_id)
        total = total.added(profile.concurrent_reservation())

    desired = len(shard_profile_ids)
    if total.fits_within(budget.host_capacity):
        return ShardBudgetDecision(
            kind=ShardBudgetDecisionKind.ADMITTED,
            desired_shard_count=desired,
            allowed_shard_count=desired,
            limiting_dimension=None,
            reason=None,
        )
    return ShardBudgetDecision(
        kind=ShardBudgetDecisionKind.INFEASIBLE,
        desired_shard_count=desired,
        allowed_shard_count=0,
        limiting_dimension=total.first_exceeding_dimension(budget.host_capacity),
        reason="oversell",
    )
