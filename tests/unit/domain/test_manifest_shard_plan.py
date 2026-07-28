"""T-M0-MODEL-001A: immutable Manifest and ShardPlan integrity contracts."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-input.v1",
        payload={"label": label},
    )


def _inputs():
    from qarunner.domain import ManifestInputs

    return ManifestInputs(
        suite_revision_digest=_digest("suite-revision"),
        source_digest=_digest("source"),
        dependency_digest=_digest("dependencies"),
        config_digest=_digest("config"),
        runner_digest=_digest("runner"),
        collection_contract_version="qep.pytest-collection.v1",
    )


def _constraints(
    *,
    serial_group: str | None = None,
    environment_requirements: tuple[str, ...] = (),
    account_requirements: tuple[str, ...] = (),
    data_lease_requirements: tuple[str, ...] = (),
):
    from qarunner.domain import ManifestConstraints

    return ManifestConstraints(
        serial_group=serial_group,
        environment_requirements=environment_requirements,
        account_requirements=account_requirements,
        data_lease_requirements=data_lease_requirements,
    )


def _item(
    item_index: int,
    stable_case_id: str,
    *,
    atomic_group_id: str | None = None,
    resource_profile_id: str = "profile-default",
    duration_ms: int = 100,
    constraints=None,
):
    from qarunner.domain import (
        EstimateConfidence,
        FrameworkLocator,
        ManifestItem,
        WorkEstimate,
    )

    return ManifestItem(
        item_index=item_index,
        stable_case_id=stable_case_id,
        framework_locator=FrameworkLocator(
            schema_version="qep.pytest-locator.v1",
            kind="pytest_nodeid",
            parts=(("file", "tests/test_shop.py"), ("node", stable_case_id)),
        ),
        atomic_group_id=atomic_group_id or stable_case_id,
        resource_profile_id=resource_profile_id,
        constraints=_constraints() if constraints is None else constraints,
        estimate=WorkEstimate(
            duration_ms=duration_ms,
            confidence=EstimateConfidence.MEDIUM,
        ),
        tags=("regression",),
        selection_metadata_digest=_digest(f"selection:{stable_case_id}"),
    )


def _manifest(
    *items,
    manifest_id: str = "manifest-001",
    batch_id: str = "batch-001",
    inputs=None,
):
    from qarunner.domain import CaseManifest

    return CaseManifest.create(
        manifest_id=manifest_id,
        batch_id=batch_id,
        inputs=_inputs() if inputs is None else inputs,
        items=items,
    )


def _representative_manifest():
    return _manifest(
        _item(0, "case-login", atomic_group_id="group-auth"),
        _item(1, "case-logout", atomic_group_id="group-auth"),
        _item(2, "case-search"),
        _item(3, "case-checkout"),
    )


def _shard(
    shard_index: int,
    item_indices: tuple[int, ...],
    *,
    estimated_duration_ms: int,
    resource_profile_id: str = "profile-default",
):
    from qarunner.domain import PlannedShard, ShardRequirements

    return PlannedShard(
        shard_index=shard_index,
        manifest_item_indices=item_indices,
        resource_profile_id=resource_profile_id,
        estimated_duration_ms=estimated_duration_ms,
        requirements=ShardRequirements(
            serial_groups=(),
            environment_requirements=(),
            account_requirements=(),
            data_lease_requirements=(),
        ),
        flags=(),
    )


def _plan(
    manifest,
    *shards,
    plan_id: str = "plan-001",
    batch_id: str = "batch-001",
    algorithm_version: str = "qep.weighted-lpt.v1",
):
    from qarunner.domain import ShardPlan

    return ShardPlan.create(
        plan_id=plan_id,
        batch_id=batch_id,
        manifest=manifest,
        algorithm_version=algorithm_version,
        shards=shards,
    )


def test_manifest_normalizes_declared_index_order_and_excludes_persistence_identity() -> None:
    first = _item(0, "case-login")
    second = _item(1, "case-search")

    forward = _manifest(first, second)
    reverse_new_identity = _manifest(
        second,
        first,
        manifest_id="manifest-999",
        batch_id="batch-999",
    )

    assert forward.schema_version == "qep.case-manifest.v1"
    assert forward.items == (first, second)
    assert forward.item_count == 2
    assert forward.digest == reverse_new_identity.digest
    assert forward.id != reverse_new_identity.id


def test_manifest_digest_detects_adapter_item_index_drift() -> None:
    stable = _manifest(_item(0, "case-login"), _item(1, "case-search"))
    drifted = _manifest(_item(0, "case-search"), _item(1, "case-login"))

    assert drifted.digest != stable.digest


@pytest.mark.parametrize(
    ("items", "field", "reason"),
    [
        pytest.param((), "items", "empty", id="empty"),
        pytest.param(
            (_item(0, "case-login"), _item(0, "case-search")),
            "item_index",
            "duplicate",
            id="duplicate-index",
        ),
        pytest.param(
            (_item(0, "case-login"), _item(2, "case-search")),
            "item_index",
            "not_contiguous",
            id="non-contiguous-index",
        ),
        pytest.param(
            (_item(0, "case-login"), _item(1, "case-login")),
            "stable_case_id",
            "duplicate",
            id="duplicate-case",
        ),
    ],
)
def test_manifest_rejects_incomplete_or_ambiguous_identity(
    items: tuple[object, ...], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _manifest(*items)

    assert caught.value.entity_type == "case_manifest"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"item_index": -1}, "item_index", "negative", id="negative-index"),
        pytest.param({"item_index": True}, "item_index", "not_integer", id="bool-index"),
        pytest.param({"stable_case_id": 123}, "stable_case_id", "not_string", id="case-id-type"),
        pytest.param({"stable_case_id": ""}, "stable_case_id", "empty_id", id="case-id"),
        pytest.param({"atomic_group_id": ""}, "atomic_group_id", "empty_id", id="group-id"),
        pytest.param(
            {"resource_profile_id": ""}, "resource_profile_id", "empty_id", id="profile-id"
        ),
        pytest.param(
            {"selection_metadata_digest": "not-a-digest"},
            "selection_metadata_digest",
            "not_digest",
            id="metadata-digest",
        ),
    ],
)
def test_manifest_item_rejects_invalid_values(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_item(0, "case-login"), **changes)

    assert caught.value.entity_type == "manifest_item"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("factory", "field", "reason"),
    [
        pytest.param(
            lambda: __import__("qarunner.domain", fromlist=["FrameworkLocator"]).FrameworkLocator(
                schema_version="qep.locator.v1", kind="pytest", parts=()
            ),
            "parts",
            "empty",
            id="locator-empty",
        ),
        pytest.param(
            lambda: __import__("qarunner.domain", fromlist=["FrameworkLocator"]).FrameworkLocator(
                schema_version="qep.locator.v1",
                kind="pytest",
                parts=(("node", "a"), ("node", "b")),
            ),
            "parts",
            "duplicate_name",
            id="locator-duplicate-name",
        ),
        pytest.param(
            lambda: __import__(
                "qarunner.domain", fromlist=["ManifestConstraints"]
            ).ManifestConstraints(
                serial_group="",
                environment_requirements=(),
                account_requirements=(),
                data_lease_requirements=(),
            ),
            "serial_group",
            "empty_id",
            id="empty-serial-group",
        ),
        pytest.param(
            lambda: __import__("qarunner.domain", fromlist=["WorkEstimate"]).WorkEstimate(
                duration_ms=1, confidence="medium"
            ),
            "confidence",
            "unknown",
            id="estimate-confidence",
        ),
    ],
)
def test_manifest_nested_values_reject_invalid_contracts(factory, field: str, reason: str) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        factory()

    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("parts", "reason"),
    [
        pytest.param([("node", "case")], "not_tuple", id="mutable-outer-container"),
        pytest.param((["node", "case"],), "invalid_entry", id="mutable-entry"),
        pytest.param(("ab",), "invalid_entry", id="string-entry"),
        pytest.param((("node", "case", "extra"),), "invalid_entry", id="wrong-arity"),
    ],
)
def test_framework_locator_rejects_mutable_or_malformed_parts(parts, reason: str) -> None:
    from qarunner.domain import DomainValidationError, FrameworkLocator

    with pytest.raises(DomainValidationError) as caught:
        FrameworkLocator(
            schema_version="qep.locator.v1",
            kind="pytest",
            parts=parts,
        )

    assert caught.value.entity_type == "framework_locator"
    assert caught.value.field == "parts"
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        pytest.param("environment_requirements", ["linux"], "not_tuple", id="not-tuple"),
        pytest.param("account_requirements", ("",), "invalid_value", id="empty-value"),
        pytest.param("data_lease_requirements", ("db", "db"), "duplicate", id="duplicate"),
        pytest.param(
            "environment_requirements", ("linux", "browser"), "not_canonical", id="unsorted"
        ),
    ],
)
def test_manifest_constraint_sets_must_be_canonical(
    field: str, value: object, reason: str
) -> None:
    from qarunner.domain import DomainValidationError, ManifestConstraints

    values = {
        "serial_group": None,
        "environment_requirements": (),
        "account_requirements": (),
        "data_lease_requirements": (),
        field: value,
    }
    with pytest.raises(DomainValidationError) as caught:
        ManifestConstraints(**values)  # type: ignore[arg-type]

    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("constraint_kind", "first", "second"),
    [
        pytest.param(
            "serial_group",
            _constraints(serial_group="serial:shop"),
            _constraints(serial_group="serial:shop"),
            id="serial-group",
        ),
        pytest.param(
            "account_requirement",
            _constraints(account_requirements=("account:buyer",)),
            _constraints(account_requirements=("account:buyer",)),
            id="account",
        ),
        pytest.param(
            "data_lease_requirement",
            _constraints(data_lease_requirements=("dataset:orders",)),
            _constraints(data_lease_requirements=("dataset:orders",)),
            id="data-lease",
        ),
    ],
)
def test_manifest_rejects_hard_grouping_token_across_atomic_groups(
    constraint_kind: str, first, second
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _manifest(
            _item(0, "case-login", atomic_group_id="group-a", constraints=first),
            _item(1, "case-search", atomic_group_id="group-b", constraints=second),
        )

    assert caught.value.field == "atomic_group_id"
    assert caught.value.reason == f"constraint_split:{constraint_kind}"


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"framework_locator": "bad"}, "framework_locator", "invalid_type"),
        pytest.param({"constraints": "bad"}, "constraints", "invalid_type"),
        pytest.param({"estimate": "bad"}, "estimate", "invalid_type"),
    ],
)
def test_manifest_item_rejects_wrong_nested_types(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_item(0, "case-login"), **changes)

    assert caught.value.field == field
    assert caught.value.reason == reason


def test_manifest_digest_binds_every_input_and_item_semantic_family() -> None:
    baseline_item = _item(0, "case-login")
    baseline = _manifest(baseline_item)
    variants = (
        _manifest(
            replace(
                baseline_item,
                framework_locator=replace(baseline_item.framework_locator, kind="playwright"),
            )
        ),
        _manifest(replace(baseline_item, atomic_group_id="other-group")),
        _manifest(replace(baseline_item, resource_profile_id="profile-browser")),
        _manifest(
            replace(
                baseline_item,
                constraints=_constraints(environment_requirements=("browser",)),
            )
        ),
        _manifest(
            replace(
                baseline_item,
                estimate=replace(baseline_item.estimate, duration_ms=101),
            )
        ),
        _manifest(replace(baseline_item, tags=("smoke",))),
        _manifest(replace(baseline_item, selection_metadata_digest=_digest("selection:v2"))),
        _manifest(
            baseline_item,
            inputs=replace(baseline.inputs, suite_revision_digest=_digest("suite:v2")),
        ),
        _manifest(
            baseline_item,
            inputs=replace(baseline.inputs, source_digest=_digest("source:v2")),
        ),
        _manifest(
            baseline_item,
            inputs=replace(baseline.inputs, dependency_digest=_digest("dependency:v2")),
        ),
        _manifest(
            baseline_item,
            inputs=replace(baseline.inputs, config_digest=_digest("config:v2")),
        ),
        _manifest(
            baseline_item,
            inputs=replace(baseline.inputs, runner_digest=_digest("runner:v2")),
        ),
        _manifest(
            baseline_item,
            inputs=replace(
                baseline.inputs,
                collection_contract_version="qep.pytest-collection.v2",
            ),
        ),
    )

    assert all(variant.digest != baseline.digest for variant in variants)


def test_work_estimate_rejects_values_outside_canonical_integer_range() -> None:
    from qarunner.domain import DomainValidationError, EstimateConfidence, WorkEstimate

    with pytest.raises(DomainValidationError) as caught:
        WorkEstimate(
            duration_ms=9_007_199_254_740_992,
            confidence=EstimateConfidence.LOW,
        )

    assert caught.value.entity_type == "work_estimate"
    assert caught.value.field == "duration_ms"
    assert caught.value.reason == "exceeds_safe_integer"


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"schema_version": "qep.case-manifest.v2"}, "schema_version", "unsupported"),
        pytest.param({"inputs": "bad"}, "inputs", "invalid_type"),
        pytest.param({"digest": _digest("wrong")}, "digest", "mismatch"),
    ],
)
def test_manifest_rehydration_rejects_schema_type_or_digest_drift(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_representative_manifest(), **changes)

    assert caught.value.field == field
    assert caught.value.reason == reason


def test_manifest_rehydration_rejects_mutable_item_container() -> None:
    from qarunner.domain import DomainValidationError

    manifest = _representative_manifest()
    with pytest.raises(DomainValidationError) as caught:
        replace(manifest, items=list(manifest.items))

    assert caught.value.field == "items"
    assert caught.value.reason == "not_tuple"


def test_manifest_rehydration_rejects_wrong_item_type_within_tuple() -> None:
    from qarunner.domain import DomainValidationError

    manifest = _representative_manifest()
    with pytest.raises(DomainValidationError) as caught:
        replace(manifest, items=(*manifest.items, "bad"))  # type: ignore[arg-type]

    assert caught.value.field == "items"
    assert caught.value.reason == "invalid_type"


@pytest.mark.parametrize("case", ["inputs", "items"])
def test_manifest_factory_rejects_wrong_nested_types_stably(case: str) -> None:
    from qarunner.domain import CaseManifest, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        CaseManifest.create(
            manifest_id="manifest-001",
            batch_id="batch-001",
            inputs="bad" if case == "inputs" else _inputs(),  # type: ignore[arg-type]
            items=("bad",) if case == "items" else (_item(0, "case-login"),),  # type: ignore[arg-type]
        )

    assert caught.value.field == case
    assert caught.value.reason == "invalid_type"


def test_shard_plan_covers_every_item_once_and_excludes_persistence_identity() -> None:
    manifest = _representative_manifest()
    first = _shard(0, (0, 1), estimated_duration_ms=200)
    second = _shard(1, (2, 3), estimated_duration_ms=200)

    forward = _plan(manifest, first, second)
    other_manifest_identity = _manifest(
        *manifest.items,
        manifest_id="manifest-999",
        batch_id="batch-999",
    )
    reverse_new_identity = _plan(
        other_manifest_identity,
        second,
        first,
        plan_id="plan-999",
        batch_id="batch-999",
    )

    assert forward.schema_version == "qep.shard-plan.v1"
    assert forward.manifest_digest == manifest.digest
    assert forward.shards == (first, second)
    assert forward.run_count == 2
    assert forward.total_estimated_duration_ms == 400
    assert forward.digest == reverse_new_identity.digest


def test_manifest_and_plan_validate_thirty_thousand_items_exactly_once() -> None:
    item_count = 30_000
    manifest = _manifest(*(_item(index, f"case-{index:05d}") for index in range(item_count)))
    plan = _plan(
        manifest,
        _shard(
            0,
            tuple(range(item_count)),
            estimated_duration_ms=item_count * 100,
        ),
    )

    assert manifest.item_count == item_count
    assert plan.run_count == 1
    assert plan.shards[0].manifest_item_indices[0] == 0
    assert plan.shards[0].manifest_item_indices[-1] == item_count - 1


@pytest.mark.parametrize(
    ("shards", "field", "reason"),
    [
        pytest.param(
            (_shard(0, (0, 1, 2), estimated_duration_ms=300),),
            "manifest_item_indices",
            "missing:3",
            id="missing-item",
        ),
        pytest.param(
            (
                _shard(0, (0, 1, 2), estimated_duration_ms=300),
                _shard(1, (2, 3), estimated_duration_ms=200),
            ),
            "manifest_item_indices",
            "duplicate:2",
            id="duplicate-item",
        ),
        pytest.param(
            (_shard(0, (0, 1, 2, 3, 4), estimated_duration_ms=500),),
            "manifest_item_indices",
            "unknown:4",
            id="unknown-item",
        ),
        pytest.param(
            (
                _shard(0, (0, 2), estimated_duration_ms=200),
                _shard(1, (1, 3), estimated_duration_ms=200),
            ),
            "atomic_group_id",
            "split:group-auth",
            id="split-atomic-group",
        ),
        pytest.param(
            (
                _shard(0, (0, 1), estimated_duration_ms=200),
                _shard(0, (2, 3), estimated_duration_ms=200),
            ),
            "shard_index",
            "duplicate",
            id="duplicate-shard-index",
        ),
        pytest.param(
            (
                _shard(0, (0, 1), estimated_duration_ms=199),
                _shard(1, (2, 3), estimated_duration_ms=200),
            ),
            "estimated_duration_ms",
            "mismatch:0",
            id="estimate-mismatch",
        ),
        pytest.param((), "shards", "empty", id="empty-shards"),
        pytest.param(
            (
                _shard(0, (0, 1), estimated_duration_ms=200),
                _shard(2, (2, 3), estimated_duration_ms=200),
            ),
            "shard_index",
            "not_contiguous",
            id="non-contiguous-shards",
        ),
    ],
)
def test_shard_plan_rejects_invalid_candidate_ownership(
    shards: tuple[object, ...], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _plan(_representative_manifest(), *shards)

    assert caught.value.entity_type == "shard_plan"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_shard_plan_rejects_profile_incompatibility() -> None:
    from qarunner.domain import DomainValidationError

    manifest = _manifest(
        _item(0, "case-api", resource_profile_id="profile-api"),
        _item(1, "case-browser", resource_profile_id="profile-browser"),
    )

    with pytest.raises(DomainValidationError) as caught:
        _plan(
            manifest,
            _shard(0, (0, 1), estimated_duration_ms=200, resource_profile_id="profile-api"),
        )

    assert caught.value.field == "resource_profile_id"
    assert caught.value.reason == "mismatch:1"


def test_shard_plan_rejects_declared_requirements_drift() -> None:
    from qarunner.domain import DomainValidationError

    manifest = _manifest(
        _item(
            0,
            "case-api",
            constraints=_constraints(environment_requirements=("api",)),
        ),
        _item(
            1,
            "case-browser",
            constraints=_constraints(environment_requirements=("browser",)),
        ),
    )

    with pytest.raises(DomainValidationError) as caught:
        _plan(manifest, _shard(0, (0, 1), estimated_duration_ms=200))

    assert caught.value.field == "requirements"
    assert caught.value.reason == "mismatch:0"


def test_shard_plan_carries_union_of_compatible_item_requirements() -> None:
    from qarunner.domain import ShardRequirements

    manifest = _manifest(
        _item(
            0,
            "case-api",
            constraints=_constraints(
                serial_group="serial:api",
                environment_requirements=("api",),
                account_requirements=("account:api",),
            ),
        ),
        _item(
            1,
            "case-browser",
            constraints=_constraints(
                serial_group="serial:browser",
                environment_requirements=("browser",),
                data_lease_requirements=("dataset:browser",),
            ),
        ),
    )
    shard = replace(
        _shard(0, (0, 1), estimated_duration_ms=200),
        requirements=ShardRequirements(
            serial_groups=("serial:api", "serial:browser"),
            environment_requirements=("api", "browser"),
            account_requirements=("account:api",),
            data_lease_requirements=("dataset:browser",),
        ),
    )

    plan = _plan(manifest, shard)

    assert plan.shards[0].requirements == shard.requirements


def test_shard_plan_rejects_total_estimate_outside_canonical_integer_range() -> None:
    from qarunner.domain import DomainValidationError

    half_plus_one = 4_503_599_627_370_496
    manifest = _manifest(
        _item(0, "case-api", duration_ms=half_plus_one),
        _item(1, "case-browser", duration_ms=half_plus_one),
    )

    with pytest.raises(DomainValidationError) as caught:
        _plan(
            manifest,
            _shard(0, (0,), estimated_duration_ms=half_plus_one),
            _shard(1, (1,), estimated_duration_ms=half_plus_one),
        )

    assert caught.value.field == "total_estimated_duration_ms"
    assert caught.value.reason == "exceeds_safe_integer"


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"shard_index": -1}, "shard_index", "negative", id="shard-index"),
        pytest.param(
            {"manifest_item_indices": ()},
            "manifest_item_indices",
            "empty",
            id="empty-items",
        ),
        pytest.param(
            {"manifest_item_indices": (0, 0)},
            "manifest_item_indices",
            "duplicate",
            id="duplicate-items",
        ),
        pytest.param(
            {"manifest_item_indices": (1, 0)},
            "manifest_item_indices",
            "not_canonical",
            id="unsorted-items",
        ),
        pytest.param(
            {"estimated_duration_ms": -1},
            "estimated_duration_ms",
            "negative",
            id="estimate",
        ),
        pytest.param(
            {"requirements": "bad"},
            "requirements",
            "invalid_type",
            id="requirements",
        ),
        pytest.param({"resource_profile_id": ""}, "resource_profile_id", "empty_id", id="profile"),
    ],
)
def test_planned_shard_rejects_invalid_values(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_shard(0, (0,), estimated_duration_ms=100), **changes)

    assert caught.value.entity_type == "planned_shard"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"schema_version": "qep.shard-plan.v2"}, "schema_version", "unsupported"),
        pytest.param({"manifest": "bad"}, "manifest", "invalid_type"),
        pytest.param({"batch_id": "batch-999"}, "batch_id", "manifest_mismatch"),
        pytest.param({"digest": _digest("wrong")}, "digest", "mismatch"),
    ],
)
def test_plan_rehydration_rejects_schema_type_binding_or_digest_drift(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(plan, **changes)

    assert caught.value.field == field
    assert caught.value.reason == reason


def test_plan_rehydration_rejects_mutable_shard_container() -> None:
    from qarunner.domain import DomainValidationError

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(plan, shards=list(plan.shards))

    assert caught.value.field == "shards"
    assert caught.value.reason == "not_tuple"


def test_plan_rehydration_rejects_wrong_shard_type_within_tuple() -> None:
    from qarunner.domain import DomainValidationError

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(plan, shards=(*plan.shards, "bad"))  # type: ignore[arg-type]

    assert caught.value.field == "shards"
    assert caught.value.reason == "invalid_type"


@pytest.mark.parametrize("case", ["manifest", "shards"])
def test_plan_factory_rejects_wrong_nested_types_stably(case: str) -> None:
    from qarunner.domain import DomainValidationError, ShardPlan

    manifest = _representative_manifest()

    with pytest.raises(DomainValidationError) as caught:
        ShardPlan.create(
            plan_id="plan-001",
            batch_id="batch-001",
            manifest="bad" if case == "manifest" else manifest,  # type: ignore[arg-type]
            algorithm_version="qep.weighted-lpt.v1",
            shards=("bad",)
            if case == "shards"
            else (  # type: ignore[arg-type]
                _shard(0, (0, 1, 2, 3), estimated_duration_ms=400),
            ),
        )

    assert caught.value.field == case
    assert caught.value.reason == "invalid_type"


def test_plan_digest_binds_algorithm_manifest_and_ownership_but_not_run_ids() -> None:
    from qarunner.domain import BoundShardPlan, RunBinding

    manifest = _representative_manifest()
    shards = (
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )
    baseline = _plan(manifest, *shards)
    changed_algorithm = _plan(manifest, *shards, algorithm_version="qep.weighted-lpt.v2")
    changed_ownership = _plan(
        manifest,
        _shard(0, (0, 1, 2), estimated_duration_ms=300),
        _shard(1, (3,), estimated_duration_ms=100),
    )
    first_binding = BoundShardPlan.create(
        plan=baseline,
        bindings=(RunBinding(run_id="run-001", shard_index=0), RunBinding("run-002", 1)),
    )
    second_binding = BoundShardPlan.create(
        plan=baseline,
        bindings=(RunBinding(run_id="run-999", shard_index=0), RunBinding("run-998", 1)),
    )

    assert changed_algorithm.digest != baseline.digest
    assert changed_ownership.digest != baseline.digest
    assert first_binding.plan.digest == second_binding.plan.digest == baseline.digest


@pytest.mark.parametrize(
    ("run_id", "shard_index", "field", "reason"),
    [
        pytest.param("", 0, "run_id", "empty_id", id="empty-run"),
        pytest.param("run-001", -1, "shard_index", "negative", id="negative-index"),
    ],
)
def test_run_binding_rejects_invalid_values(
    run_id: str, shard_index: int, field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError, RunBinding

    with pytest.raises(DomainValidationError) as caught:
        RunBinding(run_id=run_id, shard_index=shard_index)

    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("bindings", "field", "reason"),
    [
        pytest.param((("run-001", 0),), "shard_index", "missing:1", id="missing"),
        pytest.param(
            (("run-001", 0), ("run-002", 0), ("run-003", 1)),
            "shard_index",
            "duplicate:0",
            id="duplicate-shard",
        ),
        pytest.param(
            (("run-001", 0), ("run-002", 1), ("run-003", 2)),
            "shard_index",
            "unknown:2",
            id="unknown-shard",
        ),
        pytest.param(
            (("run-001", 0), ("run-001", 1)),
            "run_id",
            "duplicate",
            id="duplicate-run",
        ),
    ],
)
def test_run_bindings_cover_each_planned_shard_once(
    bindings: tuple[tuple[str, int], ...], field: str, reason: str
) -> None:
    from qarunner.domain import BoundShardPlan, DomainValidationError, RunBinding

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as caught:
        BoundShardPlan.create(
            plan=plan,
            bindings=tuple(RunBinding(run_id, shard_index) for run_id, shard_index in bindings),
        )

    assert caught.value.entity_type == "bound_shard_plan"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_bound_plan_rehydration_rejects_wrong_type_or_noncanonical_order() -> None:
    from qarunner.domain import BoundShardPlan, DomainValidationError, RunBinding

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as wrong_plan:
        BoundShardPlan(plan="bad", bindings=())  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError) as order:
        BoundShardPlan(
            plan=plan,
            bindings=(RunBinding("run-002", 1), RunBinding("run-001", 0)),
        )

    assert wrong_plan.value.field == "plan"
    assert order.value.field == "bindings"
    assert order.value.reason == "not_canonical"


def test_bound_plan_rehydration_rejects_bad_binding_without_leaking_attribute_error() -> None:
    from qarunner.domain import BoundShardPlan, DomainValidationError

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as caught:
        BoundShardPlan(plan=plan, bindings=("bad",))  # type: ignore[arg-type]

    assert caught.value.field == "bindings"
    assert caught.value.reason == "invalid_type"


def test_bound_plan_rehydration_rejects_mutable_binding_container() -> None:
    from qarunner.domain import BoundShardPlan, DomainValidationError, RunBinding

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as caught:
        BoundShardPlan(
            plan=plan,
            bindings=[RunBinding("run-001", 0), RunBinding("run-002", 1)],  # type: ignore[arg-type]
        )

    assert caught.value.field == "bindings"
    assert caught.value.reason == "not_tuple"


@pytest.mark.parametrize("case", ["plan", "bindings"])
def test_bound_plan_factory_rejects_wrong_nested_types_stably(case: str) -> None:
    from qarunner.domain import BoundShardPlan, DomainValidationError, RunBinding

    manifest = _representative_manifest()
    plan = _plan(
        manifest,
        _shard(0, (0, 1), estimated_duration_ms=200),
        _shard(1, (2, 3), estimated_duration_ms=200),
    )

    with pytest.raises(DomainValidationError) as caught:
        BoundShardPlan.create(
            plan="bad" if case == "plan" else plan,  # type: ignore[arg-type]
            bindings=("bad",)
            if case == "bindings"
            else (  # type: ignore[arg-type]
                RunBinding("run-001", 0),
                RunBinding("run-002", 1),
            ),
        )

    assert caught.value.field == case
    assert caught.value.reason == "invalid_type"


SINGLE_SHARD_ALGORITHM = "qep.single-shard.v1"


def test_plan_single_shard_maps_full_manifest_to_one_run() -> None:
    """T-M2-SHARD-001: MVP first mode maps complete Manifest to exactly one Run."""
    from qarunner.domain import plan_single_shard

    manifest = _representative_manifest()
    plan = plan_single_shard(plan_id="plan-single-001", manifest=manifest)

    assert plan.algorithm_version == SINGLE_SHARD_ALGORITHM
    assert plan.batch_id == manifest.batch_id
    assert plan.run_count == 1
    assert plan.shards[0].shard_index == 0
    assert plan.shards[0].manifest_item_indices == (0, 1, 2, 3)
    assert plan.shards[0].resource_profile_id == "profile-default"
    assert plan.shards[0].estimated_duration_ms == 400
    assert plan.total_estimated_duration_ms == 400
    assert plan.manifest_digest == manifest.digest


def test_plan_single_shard_is_deterministic_for_same_manifest() -> None:
    from qarunner.domain import plan_single_shard

    manifest = _representative_manifest()
    first = plan_single_shard(plan_id="plan-a", manifest=manifest)
    second = plan_single_shard(plan_id="plan-b", manifest=manifest)
    # plan id is persistence identity; content digest must match
    assert first.digest == second.digest
    assert first.shards == second.shards


def test_plan_single_shard_rejects_mixed_resource_profiles() -> None:
    from qarunner.domain import DomainValidationError, plan_single_shard

    mixed = _manifest(
        _item(0, "case-a", resource_profile_id="profile-default"),
        _item(1, "case-b", resource_profile_id="profile-browser"),
    )
    with pytest.raises(DomainValidationError) as caught:
        plan_single_shard(plan_id="plan-mixed", manifest=mixed)
    assert caught.value.field == "resource_profile_id"
    assert "mismatch" in caught.value.reason or caught.value.reason.startswith("mixed")


def test_manifest_reconciliation_summary_is_bounded_and_complete() -> None:
    """T-M2-MANIFEST-002: large-set recon exposes counts/digest, not full item dump."""
    from qarunner.domain import ManifestReconciliationSummary

    item_count = 1_000
    manifest = _manifest(*(_item(index, f"case-{index:05d}") for index in range(item_count)))
    summary = ManifestReconciliationSummary.from_manifest(manifest)

    assert summary.item_count == item_count
    assert summary.manifest_digest == manifest.digest
    assert summary.batch_id == manifest.batch_id
    assert summary.first_item_index == 0
    assert summary.last_item_index == item_count - 1
    assert summary.missing_count == 0
    assert summary.duplicate_count == 0
    # Bounded: summary fields are the public surface (no items payload).
    assert set(summary.__dataclass_fields__) == {
        "batch_id",
        "manifest_id",
        "item_count",
        "manifest_digest",
        "first_item_index",
        "last_item_index",
        "missing_count",
        "duplicate_count",
    }


def test_manifest_reconciliation_detects_same_inputs_same_digest() -> None:
    """T-M2-MANIFEST-001: identical frozen inputs/items → identical digest."""
    from qarunner.domain import CaseManifest

    items = (
        _item(0, "case-login", atomic_group_id="group-auth"),
        _item(1, "case-logout", atomic_group_id="group-auth"),
    )
    first = CaseManifest.create(
        manifest_id="manifest-a",
        batch_id="batch-a",
        inputs=_inputs(),
        items=items,
    )
    second = CaseManifest.create(
        manifest_id="manifest-b",
        batch_id="batch-b",
        inputs=_inputs(),
        items=items,
    )
    assert first.digest == second.digest


def test_plan_single_shard_thirty_thousand_items() -> None:
    """T-M2-MANIFEST-002 + T-M2-SHARD-001: single-shard owns all 30k indices once."""
    from qarunner.domain import plan_single_shard

    item_count = 30_000
    manifest = _manifest(*(_item(index, f"case-{index:05d}") for index in range(item_count)))
    plan = plan_single_shard(plan_id="plan-30k", manifest=manifest)
    assert plan.run_count == 1
    assert plan.shards[0].manifest_item_indices[0] == 0
    assert plan.shards[0].manifest_item_indices[-1] == item_count - 1
    assert len(plan.shards[0].manifest_item_indices) == item_count
    summary = manifest.reconciliation_summary()
    assert summary.item_count == item_count
    assert summary.missing_count == 0
    assert summary.duplicate_count == 0
    assert summary.manifest_digest == manifest.digest


def test_reconciliation_summary_rejects_empty_item_count() -> None:
    from qarunner.domain import (
        DomainValidationError,
        ManifestReconciliationSummary,
        canonical_digest,
    )

    with pytest.raises(DomainValidationError) as caught:
        ManifestReconciliationSummary(
            batch_id="batch-001",
            manifest_id="manifest-001",
            item_count=0,
            manifest_digest=canonical_digest(
                schema_version="qep.test-input.v1", payload={"label": "x"}
            ),
            first_item_index=0,
            last_item_index=0,
            missing_count=0,
            duplicate_count=0,
        )
    assert caught.value.field == "item_count"
    assert caught.value.reason == "empty"


def test_plan_single_shard_rejects_non_manifest() -> None:
    from qarunner.domain import DomainValidationError, plan_single_shard

    with pytest.raises(DomainValidationError) as caught:
        plan_single_shard(plan_id="plan-x", manifest=object())  # type: ignore[arg-type]
    assert caught.value.field == "manifest"
    assert caught.value.reason == "invalid_type"


def test_reconciliation_summary_rejects_non_manifest() -> None:
    from qarunner.domain import DomainValidationError, ManifestReconciliationSummary

    with pytest.raises(DomainValidationError) as caught:
        ManifestReconciliationSummary.from_manifest(object())  # type: ignore[arg-type]
    assert caught.value.field == "manifest"
    assert caught.value.reason == "invalid_type"
