"""Immutable Case Manifest, candidate Shard Plan, and Run bindings."""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from qarunner.domain.digest import Digest, JsonValue, canonical_digest
from qarunner.domain.errors import DomainValidationError

if TYPE_CHECKING:
    from qarunner.domain.resource_profile import ShardPlanningBudget

CASE_MANIFEST_SCHEMA_VERSION = "qep.case-manifest.v1"
SHARD_PLAN_SCHEMA_VERSION = "qep.shard-plan.v1"
SINGLE_SHARD_ALGORITHM_VERSION = "qep.single-shard.v1"
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


class EstimateConfidence(enum.StrEnum):
    """Confidence of a planning work estimate."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class FrameworkLocator:
    """Versioned, adapter-owned stable case locator."""

    schema_version: str
    kind: str
    parts: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_nonempty_string("framework_locator", "schema_version", self.schema_version)
        _require_nonempty_string("framework_locator", "kind", self.kind)
        if not isinstance(self.parts, tuple):
            _invalid("framework_locator", "parts", "not_tuple")
        if not self.parts:
            _invalid("framework_locator", "parts", "empty")
        names: list[str] = []
        for part in self.parts:
            if not isinstance(part, tuple) or len(part) != 2:
                _invalid("framework_locator", "parts", "invalid_entry")
            name, value = part
            _require_nonempty_string("framework_locator", "parts", name)
            _require_nonempty_string("framework_locator", "parts", value)
            names.append(name)
        if len(names) != len(set(names)):
            _invalid("framework_locator", "parts", "duplicate_name")


@dataclass(frozen=True, slots=True)
class ManifestConstraints:
    """Already-derived hard grouping and target-resource requirements."""

    serial_group: str | None
    environment_requirements: tuple[str, ...]
    account_requirements: tuple[str, ...]
    data_lease_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.serial_group is not None:
            _require_nonempty_string("manifest_constraints", "serial_group", self.serial_group)
        for field, values in (
            ("environment_requirements", self.environment_requirements),
            ("account_requirements", self.account_requirements),
            ("data_lease_requirements", self.data_lease_requirements),
        ):
            _require_canonical_string_set("manifest_constraints", field, values)


@dataclass(frozen=True, slots=True)
class ShardRequirements:
    """Canonical union of the hard requirements carried by a planned shard."""

    serial_groups: tuple[str, ...]
    environment_requirements: tuple[str, ...]
    account_requirements: tuple[str, ...]
    data_lease_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        for field, values in (
            ("serial_groups", self.serial_groups),
            ("environment_requirements", self.environment_requirements),
            ("account_requirements", self.account_requirements),
            ("data_lease_requirements", self.data_lease_requirements),
        ):
            _require_canonical_string_set("shard_requirements", field, values)


@dataclass(frozen=True, slots=True)
class WorkEstimate:
    """Version-independent minimum work estimate consumed by a planner."""

    duration_ms: int
    confidence: EstimateConfidence

    def __post_init__(self) -> None:
        _require_nonnegative_int("work_estimate", "duration_ms", self.duration_ms)
        if not isinstance(self.confidence, EstimateConfidence):
            _invalid("work_estimate", "confidence", "unknown")


@dataclass(frozen=True, slots=True)
class ManifestInputs:
    """Frozen inputs that make collection output reproducible."""

    suite_revision_digest: Digest
    source_digest: Digest
    dependency_digest: Digest
    config_digest: Digest
    runner_digest: Digest
    collection_contract_version: str

    def __post_init__(self) -> None:
        for field, value in (
            ("suite_revision_digest", self.suite_revision_digest),
            ("source_digest", self.source_digest),
            ("dependency_digest", self.dependency_digest),
            ("config_digest", self.config_digest),
            ("runner_digest", self.runner_digest),
        ):
            _require_digest("manifest_inputs", field, value)
        _require_nonempty_string(
            "manifest_inputs",
            "collection_contract_version",
            self.collection_contract_version,
        )


@dataclass(frozen=True, slots=True)
class ManifestItem:
    """One canonical collected case and its already-derived hard constraints."""

    item_index: int
    stable_case_id: str
    framework_locator: FrameworkLocator
    atomic_group_id: str
    resource_profile_id: str
    constraints: ManifestConstraints
    estimate: WorkEstimate
    tags: tuple[str, ...]
    selection_metadata_digest: Digest

    def __post_init__(self) -> None:
        _require_nonnegative_int("manifest_item", "item_index", self.item_index)
        _require_nonempty_string("manifest_item", "stable_case_id", self.stable_case_id)
        if not isinstance(self.framework_locator, FrameworkLocator):
            _invalid("manifest_item", "framework_locator", "invalid_type")
        _require_nonempty_string("manifest_item", "atomic_group_id", self.atomic_group_id)
        _require_nonempty_string("manifest_item", "resource_profile_id", self.resource_profile_id)
        if not isinstance(self.constraints, ManifestConstraints):
            _invalid("manifest_item", "constraints", "invalid_type")
        if not isinstance(self.estimate, WorkEstimate):
            _invalid("manifest_item", "estimate", "invalid_type")
        _require_canonical_string_set("manifest_item", "tags", self.tags)
        _require_digest(
            "manifest_item",
            "selection_metadata_digest",
            self.selection_metadata_digest,
        )


@dataclass(frozen=True, slots=True)
class CaseManifest:
    """Canonical collection output; persistence identity is outside its digest."""

    id: str
    batch_id: str
    schema_version: str
    inputs: ManifestInputs
    items: tuple[ManifestItem, ...]
    digest: Digest

    def __post_init__(self) -> None:
        _require_nonempty_string("case_manifest", "id", self.id)
        _require_nonempty_string("case_manifest", "batch_id", self.batch_id)
        if self.schema_version != CASE_MANIFEST_SCHEMA_VERSION:
            _invalid("case_manifest", "schema_version", "unsupported")
        if not isinstance(self.inputs, ManifestInputs):
            _invalid("case_manifest", "inputs", "invalid_type")
        _validate_manifest_items(self.items)
        _require_digest("case_manifest", "digest", self.digest)
        computed = canonical_digest(
            schema_version=self.schema_version,
            payload=_manifest_payload(inputs=self.inputs, items=self.items),
        )
        if self.digest != computed:
            _invalid("case_manifest", "digest", "mismatch")

    @classmethod
    def create(
        cls,
        *,
        manifest_id: str,
        batch_id: str,
        inputs: ManifestInputs,
        items: tuple[ManifestItem, ...],
    ) -> CaseManifest:
        """Normalize by stable item index and compute the content digest."""
        if not isinstance(inputs, ManifestInputs):
            _invalid("case_manifest", "inputs", "invalid_type")
        if any(not isinstance(item, ManifestItem) for item in items):
            _invalid("case_manifest", "items", "invalid_type")
        ordered = tuple(sorted(items, key=lambda item: item.item_index))
        _validate_manifest_items(ordered)
        digest = canonical_digest(
            schema_version=CASE_MANIFEST_SCHEMA_VERSION,
            payload=_manifest_payload(inputs=inputs, items=ordered),
        )
        return cls(
            id=manifest_id,
            batch_id=batch_id,
            schema_version=CASE_MANIFEST_SCHEMA_VERSION,
            inputs=inputs,
            items=ordered,
            digest=digest,
        )

    @property
    def item_count(self) -> int:
        return len(self.items)

    def reconciliation_summary(self) -> ManifestReconciliationSummary:
        """Bounded recon view for large Manifests (T-M2-MANIFEST-002)."""
        return ManifestReconciliationSummary.from_manifest(self)


@dataclass(frozen=True, slots=True)
class ManifestReconciliationSummary:
    """Bounded completeness view — never carries the full item list.

    Proves T-M2-MANIFEST-001/002 domain half: digest identity + zero-miss/
    zero-dup counts without unbounded response material.
    """

    batch_id: str
    manifest_id: str
    item_count: int
    manifest_digest: Digest
    first_item_index: int
    last_item_index: int
    missing_count: int
    duplicate_count: int

    def __post_init__(self) -> None:
        _require_nonempty_string("manifest_reconciliation_summary", "batch_id", self.batch_id)
        _require_nonempty_string(
            "manifest_reconciliation_summary", "manifest_id", self.manifest_id
        )
        _require_nonnegative_int("manifest_reconciliation_summary", "item_count", self.item_count)
        if self.item_count < 1:
            _invalid("manifest_reconciliation_summary", "item_count", "empty")
        _require_digest("manifest_reconciliation_summary", "manifest_digest", self.manifest_digest)
        _require_nonnegative_int(
            "manifest_reconciliation_summary",
            "first_item_index",
            self.first_item_index,
        )
        _require_nonnegative_int(
            "manifest_reconciliation_summary",
            "last_item_index",
            self.last_item_index,
        )
        _require_nonnegative_int(
            "manifest_reconciliation_summary", "missing_count", self.missing_count
        )
        _require_nonnegative_int(
            "manifest_reconciliation_summary", "duplicate_count", self.duplicate_count
        )

    @classmethod
    def from_manifest(cls, manifest: CaseManifest) -> ManifestReconciliationSummary:
        if not isinstance(manifest, CaseManifest):
            _invalid("manifest_reconciliation_summary", "manifest", "invalid_type")
        indices = tuple(item.item_index for item in manifest.items)
        counts = Counter(indices)
        duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
        # Contiguous 0..n-1 is required by CaseManifest validation; missing is 0
        # for a valid aggregate. Surface the counters so recon APIs stay stable.
        expected = set(range(len(manifest.items)))
        missing_count = len(expected - set(indices))
        return cls(
            batch_id=manifest.batch_id,
            manifest_id=manifest.id,
            item_count=manifest.item_count,
            manifest_digest=manifest.digest,
            first_item_index=indices[0],
            last_item_index=indices[-1],
            missing_count=missing_count,
            duplicate_count=duplicate_count,
        )


@dataclass(frozen=True, slots=True)
class PlannedShard:
    """Stable plan content identified by shard index, not a random Run ID."""

    shard_index: int
    manifest_item_indices: tuple[int, ...]
    resource_profile_id: str
    estimated_duration_ms: int
    requirements: ShardRequirements
    flags: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonnegative_int("planned_shard", "shard_index", self.shard_index)
        if not self.manifest_item_indices:
            _invalid("planned_shard", "manifest_item_indices", "empty")
        for item_index in self.manifest_item_indices:
            _require_nonnegative_int("planned_shard", "manifest_item_indices", item_index)
        if tuple(sorted(self.manifest_item_indices)) != self.manifest_item_indices:
            _invalid("planned_shard", "manifest_item_indices", "not_canonical")
        if len(set(self.manifest_item_indices)) != len(self.manifest_item_indices):
            _invalid("planned_shard", "manifest_item_indices", "duplicate")
        _require_nonempty_string("planned_shard", "resource_profile_id", self.resource_profile_id)
        _require_nonnegative_int(
            "planned_shard", "estimated_duration_ms", self.estimated_duration_ms
        )
        if not isinstance(self.requirements, ShardRequirements):
            _invalid("planned_shard", "requirements", "invalid_type")
        _require_canonical_string_set("planned_shard", "flags", self.flags)


@dataclass(frozen=True, slots=True)
class ShardPlan:
    """Validated candidate plan with exact Manifest ownership."""

    id: str
    batch_id: str
    schema_version: str
    manifest: CaseManifest
    algorithm_version: str
    shards: tuple[PlannedShard, ...]
    digest: Digest

    def __post_init__(self) -> None:
        _require_nonempty_string("shard_plan", "id", self.id)
        _require_nonempty_string("shard_plan", "batch_id", self.batch_id)
        if self.schema_version != SHARD_PLAN_SCHEMA_VERSION:
            _invalid("shard_plan", "schema_version", "unsupported")
        if not isinstance(self.manifest, CaseManifest):
            _invalid("shard_plan", "manifest", "invalid_type")
        if self.batch_id != self.manifest.batch_id:
            _invalid("shard_plan", "batch_id", "manifest_mismatch")
        _require_nonempty_string("shard_plan", "algorithm_version", self.algorithm_version)
        _validate_plan_candidate(manifest=self.manifest, shards=self.shards)
        _require_digest("shard_plan", "digest", self.digest)
        computed = canonical_digest(
            schema_version=self.schema_version,
            payload=_plan_payload(
                manifest_digest=self.manifest.digest,
                algorithm_version=self.algorithm_version,
                shards=self.shards,
            ),
        )
        if self.digest != computed:
            _invalid("shard_plan", "digest", "mismatch")

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        batch_id: str,
        manifest: CaseManifest,
        algorithm_version: str,
        shards: tuple[PlannedShard, ...],
    ) -> ShardPlan:
        """Validate exact ownership and compute a stable candidate-plan digest."""
        if not isinstance(manifest, CaseManifest):
            _invalid("shard_plan", "manifest", "invalid_type")
        if any(not isinstance(shard, PlannedShard) for shard in shards):
            _invalid("shard_plan", "shards", "invalid_type")
        ordered = tuple(sorted(shards, key=lambda shard: shard.shard_index))
        _validate_plan_candidate(manifest=manifest, shards=ordered)
        digest = canonical_digest(
            schema_version=SHARD_PLAN_SCHEMA_VERSION,
            payload=_plan_payload(
                manifest_digest=manifest.digest,
                algorithm_version=algorithm_version,
                shards=ordered,
            ),
        )
        return cls(
            id=plan_id,
            batch_id=batch_id,
            schema_version=SHARD_PLAN_SCHEMA_VERSION,
            manifest=manifest,
            algorithm_version=algorithm_version,
            shards=ordered,
            digest=digest,
        )

    @property
    def manifest_digest(self) -> Digest:
        return self.manifest.digest

    @property
    def run_count(self) -> int:
        return len(self.shards)

    @property
    def total_estimated_duration_ms(self) -> int:
        return sum(shard.estimated_duration_ms for shard in self.shards)


def plan_single_shard(
    *,
    plan_id: str,
    manifest: CaseManifest,
    algorithm_version: str = SINGLE_SHARD_ALGORITHM_VERSION,
) -> ShardPlan:
    """MVP first planner: map the full Manifest to exactly one Run (T-M2-SHARD-001).

    Requires a uniform resource_profile_id across all items. Mixed profiles are
    rejected rather than silently split — multi-shard planning is M6.
    """
    if not isinstance(manifest, CaseManifest):
        _invalid("shard_plan", "manifest", "invalid_type")
    profiles = {item.resource_profile_id for item in manifest.items}
    if len(profiles) != 1:
        _invalid("shard_plan", "resource_profile_id", "mixed")
    resource_profile_id = next(iter(profiles))
    indices = tuple(item.item_index for item in manifest.items)
    estimated_duration_ms = sum(item.estimate.duration_ms for item in manifest.items)
    requirements = _aggregate_shard_requirements(manifest.items)
    shard = PlannedShard(
        shard_index=0,
        manifest_item_indices=indices,
        resource_profile_id=resource_profile_id,
        estimated_duration_ms=estimated_duration_ms,
        requirements=requirements,
        flags=(),
    )
    return ShardPlan.create(
        plan_id=plan_id,
        batch_id=manifest.batch_id,
        manifest=manifest,
        algorithm_version=algorithm_version,
        shards=(shard,),
    )


MULTI_SHARD_ALGORITHM_VERSION = "qep.multi-shard.v1"


def plan_multi_shard(
    *,
    plan_id: str,
    manifest: CaseManifest,
    algorithm_version: str = MULTI_SHARD_ALGORITHM_VERSION,
    budget: ShardPlanningBudget | None = None,
) -> ShardPlan:
    """M6 deterministic multi-shard planner (T-M6-SHARD-001 / T-M6-ADMIT-001).

    Groups items by resource_profile_id. Without *budget*, produces one shard per
    profile (M6-SHARD-001 first cut). With *budget*, each profile group's shard
    count is clamped by ``plan_shard_count_under_budget`` (T-M6-ADMIT-001): if
    the budget allows >1 concurrent shard for a profile, items are further split
    by LPT over atomic groups (longest-duration group first, assigned to the
    least-loaded shard); atomic groups never split across shards.

    ``ShardPlan.create`` re-validates every invariant (zero miss/dup, atomic
    group integrity, profile consistency, deterministic digest) after planning.
    """
    if not isinstance(manifest, CaseManifest):
        _invalid("shard_plan", "manifest", "invalid_type")

    groups: dict[str, list[int]] = {}
    for item in manifest.items:
        groups.setdefault(item.resource_profile_id, []).append(item.item_index)

    items_by_index = {item.item_index: item for item in manifest.items}
    shards: list[PlannedShard] = []
    # Lexicographic profile order makes shard indices deterministic.
    for resource_profile_id, indices in sorted(groups.items()):
        group_items = tuple(items_by_index[i] for i in sorted(indices))
        allowed = _allowed_shard_count_for_profile(
            resource_profile_id=resource_profile_id,
            group_items=group_items,
            budget=budget,
        )
        if allowed < 1:
            _invalid(
                "shard_plan",
                "resource_profile_id",
                "infeasible",
            )
        if allowed == 1 or len(group_items) <= 1:
            # Single shard for this profile group (or trivially one item).
            shards.extend(
                _single_shard_for_group(
                    items=group_items,
                    resource_profile_id=resource_profile_id,
                    shard_index_offset=len(shards),
                )
            )
        else:
            shards.extend(
                _lpt_split_group(
                    items=group_items,
                    resource_profile_id=resource_profile_id,
                    allowed_shards=allowed,
                    shard_index_offset=len(shards),
                )
            )

    return ShardPlan.create(
        plan_id=plan_id,
        batch_id=manifest.batch_id,
        manifest=manifest,
        algorithm_version=algorithm_version,
        shards=tuple(shards),
    )


def _allowed_shard_count_for_profile(
    *,
    resource_profile_id: str,
    group_items: tuple[ManifestItem, ...],
    budget: ShardPlanningBudget | None,
) -> int:
    """Return the number of concurrent shards the budget allows for this profile.

    Returns ``1`` when no budget is provided (default one-shard-per-profile
    behavior). Returns 0 when the profile is infeasible under the budget.
    """
    if budget is None:
        return 1
    from qarunner.domain.resource_profile import (
        ShardBudgetDecisionKind,
        plan_shard_count_under_budget,
    )

    total_duration = sum(item.estimate.duration_ms for item in group_items)
    decision = plan_shard_count_under_budget(
        total_estimated_duration_ms=total_duration,
        resource_profile_id=resource_profile_id,
        budget=budget,
    )
    if decision.kind is ShardBudgetDecisionKind.INFEASIBLE:
        return 0
    return decision.allowed_shard_count


def _single_shard_for_group(
    *,
    items: tuple[ManifestItem, ...],
    resource_profile_id: str,
    shard_index_offset: int,
) -> tuple[PlannedShard, ...]:
    """One shard covering all items in a profile group."""
    indices = tuple(item.item_index for item in items)
    return (
        PlannedShard(
            shard_index=shard_index_offset,
            manifest_item_indices=indices,
            resource_profile_id=resource_profile_id,
            estimated_duration_ms=sum(item.estimate.duration_ms for item in items),
            requirements=_aggregate_shard_requirements(items),
            flags=(),
        ),
    )


def _lpt_split_group(
    *,
    items: tuple[ManifestItem, ...],
    resource_profile_id: str,
    allowed_shards: int,
    shard_index_offset: int,
) -> tuple[PlannedShard, ...]:
    """LPT (Longest Processing Time) split of a profile group across shards.

    Atomic groups are indivisible work units. Groups are sorted by total
    estimated duration descending (tie-break: atomic_group_id ascending), then
    each group is assigned to the shard with the least accumulated duration
    (tie-break: shard_index ascending). This produces deterministic,
    near-optimal makespan without splitting atomic groups.
    """
    # 1. Build atomic-group work units.
    group_durations: dict[str, int] = {}
    group_indices: dict[str, list[int]] = {}
    for item in items:
        gid = item.atomic_group_id
        group_durations[gid] = group_durations.get(gid, 0) + item.estimate.duration_ms
        group_indices.setdefault(gid, []).append(item.item_index)

    # 2. Sort work units: longest duration first, then lexicographic group id.
    sorted_groups = sorted(
        group_durations.keys(),
        key=lambda gid: (-group_durations[gid], gid),
    )

    # 3. Cap effective shard count to number of atomic groups (can't have
    #    more shards than groups — each group is indivisible).
    effective = min(allowed_shards, len(sorted_groups))

    # 4. LPT assignment: track per-shard load and item indices.
    shard_loads = [0] * effective
    shard_indices: list[list[int]] = [[] for _ in range(effective)]

    for gid in sorted_groups:
        # Pick the shard with the least accumulated load (tie-break: lowest index).
        target = min(range(effective), key=lambda s: (shard_loads[s], s))
        shard_loads[target] += group_durations[gid]
        shard_indices[target].extend(group_indices[gid])

    # 5. Build PlannedShards (all non-empty after capping).
    result: list[PlannedShard] = []
    for s in range(effective):
        ordered = tuple(sorted(shard_indices[s]))
        shard_items = tuple(item for item in items if item.item_index in set(ordered))
        result.append(
            PlannedShard(
                shard_index=shard_index_offset + len(result),
                manifest_item_indices=ordered,
                resource_profile_id=resource_profile_id,
                estimated_duration_ms=sum(item.estimate.duration_ms for item in shard_items),
                requirements=_aggregate_shard_requirements(shard_items),
                flags=(),
            )
        )

    return tuple(result)


@dataclass(frozen=True, slots=True)
class RunBinding:
    """Persistence identity assigned to one stable planned shard."""

    run_id: str
    shard_index: int

    def __post_init__(self) -> None:
        _require_nonempty_string("run_binding", "run_id", self.run_id)
        _require_nonnegative_int("run_binding", "shard_index", self.shard_index)


@dataclass(frozen=True, slots=True)
class BoundShardPlan:
    """Run identities covering every planned shard exactly once."""

    plan: ShardPlan
    bindings: tuple[RunBinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ShardPlan):
            _invalid("bound_shard_plan", "plan", "invalid_type")
        if not isinstance(self.bindings, tuple):
            _invalid("bound_shard_plan", "bindings", "not_tuple")
        if any(not isinstance(binding, RunBinding) for binding in self.bindings):
            _invalid("bound_shard_plan", "bindings", "invalid_type")
        ordered = tuple(sorted(self.bindings, key=lambda binding: binding.shard_index))
        if self.bindings != ordered:
            _invalid("bound_shard_plan", "bindings", "not_canonical")
        _validate_run_bindings(plan=self.plan, bindings=self.bindings)

    @classmethod
    def create(cls, *, plan: ShardPlan, bindings: tuple[RunBinding, ...]) -> BoundShardPlan:
        if not isinstance(plan, ShardPlan):
            _invalid("bound_shard_plan", "plan", "invalid_type")
        if any(not isinstance(binding, RunBinding) for binding in bindings):
            _invalid("bound_shard_plan", "bindings", "invalid_type")
        return cls(
            plan=plan,
            bindings=tuple(sorted(bindings, key=lambda binding: binding.shard_index)),
        )


def _validate_manifest_items(items: tuple[ManifestItem, ...]) -> None:
    if not isinstance(items, tuple):
        _invalid("case_manifest", "items", "not_tuple")
    if not items:
        _invalid("case_manifest", "items", "empty")
    if any(not isinstance(item, ManifestItem) for item in items):
        _invalid("case_manifest", "items", "invalid_type")
    indices = tuple(item.item_index for item in items)
    if len(indices) != len(set(indices)):
        _invalid("case_manifest", "item_index", "duplicate")
    if indices != tuple(range(len(items))):
        _invalid("case_manifest", "item_index", "not_contiguous")
    case_ids = tuple(item.stable_case_id for item in items)
    if len(case_ids) != len(set(case_ids)):
        _invalid("case_manifest", "stable_case_id", "duplicate")
    _validate_hard_grouping_constraints(items)


def _validate_hard_grouping_constraints(items: tuple[ManifestItem, ...]) -> None:
    groups_by_token: dict[tuple[str, str], str] = {}
    for item in items:
        constraint_tokens: list[tuple[str, str]] = []
        if item.constraints.serial_group is not None:
            constraint_tokens.append(("serial_group", item.constraints.serial_group))
        constraint_tokens.extend(
            ("account_requirement", token) for token in item.constraints.account_requirements
        )
        constraint_tokens.extend(
            ("data_lease_requirement", token) for token in item.constraints.data_lease_requirements
        )
        for kind, token in constraint_tokens:
            existing_group = groups_by_token.setdefault((kind, token), item.atomic_group_id)
            if existing_group != item.atomic_group_id:
                _invalid(
                    "case_manifest",
                    "atomic_group_id",
                    f"constraint_split:{kind}",
                )


def _validate_plan_candidate(*, manifest: CaseManifest, shards: tuple[PlannedShard, ...]) -> None:
    if not isinstance(shards, tuple):
        _invalid("shard_plan", "shards", "not_tuple")
    if not shards:
        _invalid("shard_plan", "shards", "empty")
    if any(not isinstance(shard, PlannedShard) for shard in shards):
        _invalid("shard_plan", "shards", "invalid_type")
    shard_indices = tuple(shard.shard_index for shard in shards)
    if len(shard_indices) != len(set(shard_indices)):
        _invalid("shard_plan", "shard_index", "duplicate")
    if shard_indices != tuple(range(len(shards))):
        _invalid("shard_plan", "shard_index", "not_contiguous")

    expected = {item.item_index for item in manifest.items}
    actual = [index for shard in shards for index in shard.manifest_item_indices]
    unknown = sorted(set(actual) - expected)
    if unknown:
        _invalid("shard_plan", "manifest_item_indices", f"unknown:{unknown[0]}")
    counts = Counter(actual)
    duplicates = sorted(index for index, count in counts.items() if count > 1)
    if duplicates:
        _invalid("shard_plan", "manifest_item_indices", f"duplicate:{duplicates[0]}")
    missing = sorted(expected - set(actual))
    if missing:
        _invalid("shard_plan", "manifest_item_indices", f"missing:{missing[0]}")

    shard_by_item = {
        item_index: shard for shard in shards for item_index in shard.manifest_item_indices
    }
    items_by_index = {item.item_index: item for item in manifest.items}
    group_shards: dict[str, set[int]] = {}
    for item in manifest.items:
        group_shards.setdefault(item.atomic_group_id, set()).add(
            shard_by_item[item.item_index].shard_index
        )
    split_groups = sorted(group for group, indexes in group_shards.items() if len(indexes) > 1)
    if split_groups:
        _invalid("shard_plan", "atomic_group_id", f"split:{split_groups[0]}")

    for shard in shards:
        shard_items = tuple(items_by_index[index] for index in shard.manifest_item_indices)
        expected_requirements = _aggregate_shard_requirements(shard_items)
        if shard.requirements != expected_requirements:
            _invalid("shard_plan", "requirements", f"mismatch:{shard.shard_index}")
        for item_index in shard.manifest_item_indices:
            item = items_by_index[item_index]
            if item.resource_profile_id != shard.resource_profile_id:
                _invalid("shard_plan", "resource_profile_id", f"mismatch:{item_index}")
        expected_duration = sum(
            items_by_index[index].estimate.duration_ms for index in shard.manifest_item_indices
        )
        if shard.estimated_duration_ms != expected_duration:
            _invalid(
                "shard_plan",
                "estimated_duration_ms",
                f"mismatch:{shard.shard_index}",
            )
    total_estimated_duration_ms = sum(shard.estimated_duration_ms for shard in shards)
    if total_estimated_duration_ms > _MAX_SAFE_INTEGER:
        _invalid(
            "shard_plan",
            "total_estimated_duration_ms",
            "exceeds_safe_integer",
        )


def _aggregate_shard_requirements(items: tuple[ManifestItem, ...]) -> ShardRequirements:
    return ShardRequirements(
        serial_groups=tuple(
            sorted(
                {
                    item.constraints.serial_group
                    for item in items
                    if item.constraints.serial_group is not None
                }
            )
        ),
        environment_requirements=tuple(
            sorted(
                {
                    requirement
                    for item in items
                    for requirement in item.constraints.environment_requirements
                }
            )
        ),
        account_requirements=tuple(
            sorted(
                {
                    requirement
                    for item in items
                    for requirement in item.constraints.account_requirements
                }
            )
        ),
        data_lease_requirements=tuple(
            sorted(
                {
                    requirement
                    for item in items
                    for requirement in item.constraints.data_lease_requirements
                }
            )
        ),
    )


def _validate_run_bindings(*, plan: ShardPlan, bindings: tuple[RunBinding, ...]) -> None:
    # Unreachable via this module's only caller, BoundShardPlan.__post_init__,
    # which already rejects a non-RunBinding entry before ever calling here
    # (and .create() does too) — kept as defense-in-depth for a future caller.
    if any(not isinstance(binding, RunBinding) for binding in bindings):  # pragma: no cover
        _invalid("bound_shard_plan", "bindings", "invalid_type")
    run_ids = tuple(binding.run_id for binding in bindings)
    if len(run_ids) != len(set(run_ids)):
        _invalid("bound_shard_plan", "run_id", "duplicate")
    expected = {shard.shard_index for shard in plan.shards}
    actual = [binding.shard_index for binding in bindings]
    unknown = sorted(set(actual) - expected)
    if unknown:
        _invalid("bound_shard_plan", "shard_index", f"unknown:{unknown[0]}")
    counts = Counter(actual)
    duplicates = sorted(index for index, count in counts.items() if count > 1)
    if duplicates:
        _invalid("bound_shard_plan", "shard_index", f"duplicate:{duplicates[0]}")
    missing = sorted(expected - set(actual))
    if missing:
        _invalid("bound_shard_plan", "shard_index", f"missing:{missing[0]}")


def _manifest_payload(*, inputs: ManifestInputs, items: tuple[ManifestItem, ...]) -> JsonValue:
    return {
        "suite_revision_digest": inputs.suite_revision_digest.value,
        "input_digests": {
            "source": inputs.source_digest.value,
            "dependency": inputs.dependency_digest.value,
            "config": inputs.config_digest.value,
            "runner": inputs.runner_digest.value,
        },
        "collection_contract_version": inputs.collection_contract_version,
        "items": [_manifest_item_payload(item) for item in items],
    }


def _manifest_item_payload(item: ManifestItem) -> JsonValue:
    return {
        "item_index": item.item_index,
        "stable_case_id": item.stable_case_id,
        "framework_locator": {
            "schema_version": item.framework_locator.schema_version,
            "kind": item.framework_locator.kind,
            "parts": [list(part) for part in item.framework_locator.parts],
        },
        "atomic_group_id": item.atomic_group_id,
        "resource_profile_id": item.resource_profile_id,
        "constraints": {
            "serial_group": item.constraints.serial_group,
            "environment_requirements": list(item.constraints.environment_requirements),
            "account_requirements": list(item.constraints.account_requirements),
            "data_lease_requirements": list(item.constraints.data_lease_requirements),
        },
        "estimate": {
            "duration_ms": item.estimate.duration_ms,
            "confidence": item.estimate.confidence.value,
        },
        "tags": list(item.tags),
        "selection_metadata_digest": item.selection_metadata_digest.value,
    }


def _plan_payload(
    *,
    manifest_digest: Digest,
    algorithm_version: str,
    shards: tuple[PlannedShard, ...],
) -> JsonValue:
    return {
        "manifest_digest": manifest_digest.value,
        "algorithm_version": algorithm_version,
        "run_count": len(shards),
        "total_estimated_duration_ms": sum(shard.estimated_duration_ms for shard in shards),
        "shards": [
            {
                "shard_index": shard.shard_index,
                "manifest_item_indices": list(shard.manifest_item_indices),
                "resource_profile_id": shard.resource_profile_id,
                "estimated_duration_ms": shard.estimated_duration_ms,
                "requirements": {
                    "serial_groups": list(shard.requirements.serial_groups),
                    "environment_requirements": list(shard.requirements.environment_requirements),
                    "account_requirements": list(shard.requirements.account_requirements),
                    "data_lease_requirements": list(shard.requirements.data_lease_requirements),
                },
                "flags": list(shard.flags),
            }
            for shard in shards
        ],
    }


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty_id")


def _require_nonnegative_int(entity_type: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(entity_type, field, "not_integer")
    if value < 0:
        _invalid(entity_type, field, "negative")
    if value > _MAX_SAFE_INTEGER:
        _invalid(entity_type, field, "exceeds_safe_integer")


def _require_digest(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity_type, field, "not_digest")


def _require_canonical_string_set(entity_type: str, field: str, values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        _invalid(entity_type, field, "not_tuple")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        _invalid(entity_type, field, "invalid_value")
    if len(values) != len(set(values)):
        _invalid(entity_type, field, "duplicate")
    if values != tuple(sorted(values)):
        _invalid(entity_type, field, "not_canonical")


def _invalid(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(
        entity_type=entity_type,
        field=field,
        reason=reason,
    )
