"""T-M6-SHARD-002 / T-M6-ADMIT-001 foundation: Resource Profile + planning budget.

Shard count, Profile requests, and framework-internal concurrency share one host
budget. Zero-request oversell and multi-dimension capacity breaches fail closed
with stable reasons — pure domain, no queue/schedule surface.
"""

from __future__ import annotations

import pytest

from qarunner.domain import (
    DomainValidationError,
    ResourceProfileSpec,
    ResourceVector,
    ShardBudgetDecisionKind,
    ShardPlanningBudget,
    admit_concurrent_shards,
    plan_shard_count_under_budget,
)


def _vector(**changes) -> ResourceVector:
    values = {
        "cpu_millis": 500,
        "memory_bytes": 512 * 1024 * 1024,
        "pid_slots": 128,
        "ephemeral_storage_bytes": 256 * 1024 * 1024,
        "browser_slots": 0,
    }
    values.update(changes)
    return ResourceVector(**values)


def _profile(**changes) -> ResourceProfileSpec:
    requests = changes.pop("requests", _vector())
    limits = changes.pop(
        "limits",
        _vector(
            cpu_millis=1000,
            memory_bytes=1024 * 1024 * 1024,
            pid_slots=256,
            ephemeral_storage_bytes=512 * 1024 * 1024,
            browser_slots=0,
        ),
    )
    values = {
        "profile_id": "profile-api-small",
        "name": "api-small",
        "profile_version": 1,
        "framework": "pytest",
        "requests": requests,
        "limits": limits,
        "internal_workers": 1,
        "security_profile_id": "security-untrusted-1",
    }
    values.update(changes)
    return ResourceProfileSpec(**values)


def _browser_profile(**changes) -> ResourceProfileSpec:
    return _profile(
        profile_id="profile-browser-small",
        name="browser-small",
        framework="playwright",
        requests=_vector(
            cpu_millis=2000,
            memory_bytes=3 * 1024 * 1024 * 1024,
            pid_slots=256,
            ephemeral_storage_bytes=512 * 1024 * 1024,
            browser_slots=1,
        ),
        limits=_vector(
            cpu_millis=3000,
            memory_bytes=4 * 1024 * 1024 * 1024,
            pid_slots=512,
            ephemeral_storage_bytes=1024 * 1024 * 1024,
            browser_slots=1,
        ),
        **changes,
    )


def _budget(
    *,
    profiles: tuple[ResourceProfileSpec, ...] | None = None,
    **changes,
) -> ShardPlanningBudget:
    values = {
        "target_shard_duration_ms": 60_000,
        "min_shards": 1,
        "max_shards": 8,
        "host_capacity": _vector(
            cpu_millis=4000,
            memory_bytes=8 * 1024 * 1024 * 1024,
            pid_slots=1024,
            ephemeral_storage_bytes=4 * 1024 * 1024 * 1024,
            browser_slots=2,
        ),
        "profiles": profiles if profiles is not None else (_profile(),),
    }
    values.update(changes)
    return ShardPlanningBudget(**values)


# ── ResourceVector / ResourceProfileSpec construction ─────────────────────────


def test_resource_profile_spec_digest_is_stable_and_content_addressed() -> None:
    first = _profile()
    second = _profile()
    different = _profile(internal_workers=2)

    assert first.digest.value.startswith("sha256:")
    assert first.digest == second.digest
    assert first.digest != different.digest


def test_resource_profile_rejects_zero_request_oversell() -> None:
    """DES §10.1: cannot set only limits then schedule as zero-request."""
    with pytest.raises(DomainValidationError) as caught:
        _profile(
            requests=_vector(
                cpu_millis=0,
                memory_bytes=0,
                pid_slots=0,
                ephemeral_storage_bytes=0,
                browser_slots=0,
            )
        )
    assert caught.value.entity_type == "resource_profile_spec"
    assert caught.value.field == "requests"
    assert caught.value.reason == "zero_request_oversell"


def test_resource_profile_rejects_limits_below_requests() -> None:
    with pytest.raises(DomainValidationError) as caught:
        _profile(
            requests=_vector(cpu_millis=2000),
            limits=_vector(cpu_millis=1000),
        )
    assert caught.value.entity_type == "resource_profile_spec"
    assert caught.value.field == "limits"
    assert caught.value.reason == "below_requests:cpu_millis"


def test_resource_profile_rejects_non_positive_internal_workers() -> None:
    with pytest.raises(DomainValidationError) as caught:
        _profile(internal_workers=0)
    assert caught.value.field == "internal_workers"
    assert caught.value.reason == "not_positive"


def test_concurrent_reservation_scales_requests_by_internal_workers() -> None:
    """internal_workers is an amplification factor, not free capacity."""
    profile = _profile(
        requests=_vector(cpu_millis=500, memory_bytes=100, pid_slots=10, browser_slots=0),
        internal_workers=4,
    )
    cost = profile.concurrent_reservation()
    assert cost.cpu_millis == 2000
    assert cost.memory_bytes == 400
    assert cost.pid_slots == 40
    assert cost.browser_slots == 0


# ── ShardPlanningBudget construction ─────────────────────────────────────────


def test_shard_planning_budget_rejects_max_below_min() -> None:
    with pytest.raises(DomainValidationError) as caught:
        _budget(min_shards=4, max_shards=2)
    assert caught.value.entity_type == "shard_planning_budget"
    assert caught.value.field == "max_shards"
    assert caught.value.reason == "below_min_shards"


def test_shard_planning_budget_rejects_duplicate_profile_ids() -> None:
    with pytest.raises(DomainValidationError) as caught:
        _budget(profiles=(_profile(), _profile(name="other-name")))
    assert caught.value.field == "profiles"
    assert caught.value.reason == "duplicate_profile_id"


# ── T-M6-SHARD-002: shared budget for shard count × profile × workers ────────


def test_plan_shard_count_clamps_to_host_budget_with_internal_workers() -> None:
    """T-M6-SHARD-002: desired shards share one budget with requests×workers."""
    # Host has 4000m CPU. Profile requests 500m × 4 workers = 2000m per concurrent shard
    # → at most 2 concurrent shards even if work duration wants more.
    profile = _profile(
        requests=_vector(cpu_millis=500, memory_bytes=100 * 1024 * 1024, pid_slots=32),
        limits=_vector(cpu_millis=2000, memory_bytes=512 * 1024 * 1024, pid_slots=128),
        internal_workers=4,
    )
    budget = _budget(
        target_shard_duration_ms=60_000,
        min_shards=1,
        max_shards=16,
        host_capacity=_vector(
            cpu_millis=4000,
            memory_bytes=8 * 1024 * 1024 * 1024,
            pid_slots=1024,
            ephemeral_storage_bytes=4 * 1024 * 1024 * 1024,
            browser_slots=0,
        ),
        profiles=(profile,),
    )

    # 10 minutes of work / 1 minute target → desired 10, clamped by max_shards=16 still 10,
    # then host CPU allows only 2.
    decision = plan_shard_count_under_budget(
        total_estimated_duration_ms=600_000,
        resource_profile_id=profile.profile_id,
        budget=budget,
    )

    assert decision.kind is ShardBudgetDecisionKind.CLAMPED
    assert decision.desired_shard_count == 10
    assert decision.allowed_shard_count == 2
    assert decision.limiting_dimension == "cpu_millis"
    assert decision.reason == "host_budget"


def test_plan_shard_count_admits_when_work_and_budget_both_fit() -> None:
    profile = _profile(internal_workers=1)
    budget = _budget(
        target_shard_duration_ms=60_000,
        min_shards=1,
        max_shards=8,
        profiles=(profile,),
    )
    # 90s work → ceil(90/60)=2; host easily fits 2×500m.
    decision = plan_shard_count_under_budget(
        total_estimated_duration_ms=90_000,
        resource_profile_id=profile.profile_id,
        budget=budget,
    )
    assert decision.kind is ShardBudgetDecisionKind.ADMITTED
    assert decision.desired_shard_count == 2
    assert decision.allowed_shard_count == 2
    assert decision.limiting_dimension is None
    assert decision.reason is None


def test_plan_shard_count_infeasible_when_single_shard_exceeds_host() -> None:
    """T-M6-ADMIT-001 / SHARD-002: even one concurrent shard must not oversell."""
    profile = _browser_profile(internal_workers=2)  # 2000m × 2 = 4000m, 1×2 browser slots
    budget = _budget(
        host_capacity=_vector(
            cpu_millis=3000,  # less than 4000m reservation
            memory_bytes=16 * 1024 * 1024 * 1024,
            pid_slots=2048,
            ephemeral_storage_bytes=8 * 1024 * 1024 * 1024,
            browser_slots=4,
        ),
        profiles=(profile,),
    )
    decision = plan_shard_count_under_budget(
        total_estimated_duration_ms=60_000,
        resource_profile_id=profile.profile_id,
        budget=budget,
    )
    assert decision.kind is ShardBudgetDecisionKind.INFEASIBLE
    assert decision.allowed_shard_count == 0
    assert decision.limiting_dimension == "cpu_millis"
    assert decision.reason == "oversell"


def test_plan_shard_count_rejects_unknown_profile() -> None:
    with pytest.raises(DomainValidationError) as caught:
        plan_shard_count_under_budget(
            total_estimated_duration_ms=60_000,
            resource_profile_id="missing-profile",
            budget=_budget(),
        )
    assert caught.value.entity_type == "shard_planning_budget"
    assert caught.value.field == "resource_profile_id"
    assert caught.value.reason == "unknown_profile"


def test_plan_shard_count_respects_min_max_before_budget() -> None:
    profile = _profile(internal_workers=1)
    budget = _budget(
        target_shard_duration_ms=60_000,
        min_shards=3,
        max_shards=4,
        # Plenty of host capacity so clamp is from max_shards, not host.
        host_capacity=_vector(
            cpu_millis=100_000,
            memory_bytes=64 * 1024 * 1024 * 1024,
            pid_slots=10_000,
            ephemeral_storage_bytes=64 * 1024 * 1024 * 1024,
            browser_slots=0,
        ),
        profiles=(profile,),
    )
    # Tiny work would want 1 shard; min_shards lifts to 3.
    low = plan_shard_count_under_budget(
        total_estimated_duration_ms=1_000,
        resource_profile_id=profile.profile_id,
        budget=budget,
    )
    assert low.desired_shard_count == 3
    assert low.allowed_shard_count == 3
    assert low.kind is ShardBudgetDecisionKind.ADMITTED

    # Huge work would want many; max_shards caps at 4.
    high = plan_shard_count_under_budget(
        total_estimated_duration_ms=3_600_000,
        resource_profile_id=profile.profile_id,
        budget=budget,
    )
    assert high.desired_shard_count == 4
    assert high.allowed_shard_count == 4


# ── T-M6-ADMIT-001: multi-dimension concurrent admission ──────────────────────


def test_admit_concurrent_shards_rejects_browser_oversell() -> None:
    """T-M6-ADMIT-001: browser slots insufficient → stable oversell reason."""
    browser = _browser_profile(internal_workers=1)
    budget = _budget(
        host_capacity=_vector(
            cpu_millis=20_000,
            memory_bytes=32 * 1024 * 1024 * 1024,
            pid_slots=4096,
            ephemeral_storage_bytes=16 * 1024 * 1024 * 1024,
            browser_slots=1,  # only one browser slot
        ),
        profiles=(browser,),
    )
    decision = admit_concurrent_shards(
        shard_profile_ids=(browser.profile_id, browser.profile_id),
        budget=budget,
    )
    assert decision.kind is ShardBudgetDecisionKind.INFEASIBLE
    assert decision.allowed_shard_count == 0
    assert decision.limiting_dimension == "browser_slots"
    assert decision.reason == "oversell"


def test_admit_concurrent_shards_allows_mixed_profiles_under_capacity() -> None:
    api = _profile(internal_workers=1)
    browser = _browser_profile(internal_workers=1)
    budget = _budget(
        host_capacity=_vector(
            cpu_millis=8000,
            memory_bytes=16 * 1024 * 1024 * 1024,
            pid_slots=2048,
            ephemeral_storage_bytes=8 * 1024 * 1024 * 1024,
            browser_slots=2,
        ),
        profiles=(api, browser),
    )
    decision = admit_concurrent_shards(
        shard_profile_ids=(api.profile_id, browser.profile_id),
        budget=budget,
    )
    assert decision.kind is ShardBudgetDecisionKind.ADMITTED
    assert decision.allowed_shard_count == 2
    assert decision.reason is None


def test_admit_concurrent_shards_rejects_empty_set() -> None:
    with pytest.raises(DomainValidationError) as caught:
        admit_concurrent_shards(shard_profile_ids=(), budget=_budget())
    assert caught.value.field == "shard_profile_ids"
    assert caught.value.reason == "empty"
