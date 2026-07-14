"""T-M0-STATE-001F: neutral planned-unmaterialized scope-item facts."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution-scope-item.v1",
        payload={"label": label},
    )


def _scope_item(
    manifest_item_key: str = "case-001",
    *,
    terminal_name: str = "REJECTION",
    **overrides: object,
):
    from qarunner.domain import (
        BatchPreexecutionScopeItem,
        BatchPreexecutionTerminalKind,
    )

    rejection_digest = _digest("rejection-001")
    cancellation_digest = _digest("cancel-001")
    values: dict[str, object] = {
        "batch_id": "batch-001",
        "source_batch_version": 3,
        "terminal_kind": BatchPreexecutionTerminalKind[terminal_name],
        "rejection_fact_digest": (rejection_digest if terminal_name == "REJECTION" else None),
        "batch_cancellation_intent_digest": (
            cancellation_digest if terminal_name == "PRESTART_CANCEL" else None
        ),
        "manifest_id": "manifest-001",
        "manifest_digest": _digest("manifest-001"),
        "manifest_item_key": manifest_item_key,
        "shard_plan_id": "plan-001",
        "shard_plan_version": 2,
        "shard_plan_digest": _digest("plan-001-v2"),
        "materialized_run_absence_digest": _digest("no-materialized-runs"),
        "resolution": "not_started",
    }
    values.update(overrides)
    return BatchPreexecutionScopeItem(**values)


@pytest.mark.parametrize(
    "terminal_name",
    [
        pytest.param("REJECTION", id="rejection"),
        pytest.param("PRESTART_CANCEL", id="prestart-cancel"),
    ],
)
def test_scope_item_digest_binds_every_signed_v1_field(terminal_name: str) -> None:
    """The neutral item fact is replayable without importing 001H semantics."""
    from qarunner.domain import canonical_digest

    item = _scope_item(terminal_name=terminal_name)

    assert item.resolution == "not_started"
    assert item.digest == canonical_digest(
        schema_version="qep.batch-preexecution-scope-item.v1",
        payload={
            "batch_id": item.batch_id,
            "source_batch_version": item.source_batch_version,
            "terminal_kind": item.terminal_kind.value,
            "rejection_fact_digest": (
                None if item.rejection_fact_digest is None else item.rejection_fact_digest.value
            ),
            "batch_cancellation_intent_digest": (
                None
                if item.batch_cancellation_intent_digest is None
                else item.batch_cancellation_intent_digest.value
            ),
            "manifest_id": item.manifest_id,
            "manifest_digest": item.manifest_digest.value,
            "manifest_item_key": item.manifest_item_key,
            "shard_plan_id": item.shard_plan_id,
            "shard_plan_version": item.shard_plan_version,
            "shard_plan_digest": item.shard_plan_digest.value,
            "materialized_run_absence_digest": (item.materialized_run_absence_digest.value),
            "resolution": "not_started",
        },
    )


@pytest.mark.parametrize(
    ("terminal_name", "rejection_digest", "cancellation_digest"),
    [
        pytest.param("REJECTION", None, None, id="rejection-missing-command"),
        pytest.param(
            "REJECTION",
            _digest("rejection-001"),
            _digest("cancel-001"),
            id="rejection-with-both-commands",
        ),
        pytest.param("PRESTART_CANCEL", None, None, id="cancel-missing-command"),
        pytest.param(
            "PRESTART_CANCEL",
            _digest("rejection-001"),
            _digest("cancel-001"),
            id="cancel-with-both-commands",
        ),
        pytest.param(
            "REJECTION",
            None,
            _digest("cancel-001"),
            id="rejection-bound-to-cancel-only",
        ),
        pytest.param(
            "PRESTART_CANCEL",
            _digest("rejection-001"),
            None,
            id="cancel-bound-to-rejection-only",
        ),
    ],
)
def test_terminal_kind_requires_exactly_its_matching_command_digest(
    terminal_name: str,
    rejection_digest,
    cancellation_digest,
) -> None:
    """Rejection and prestart-cancel facts cannot be cross-bound or unbound."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError):
        _scope_item(
            terminal_name=terminal_name,
            rejection_fact_digest=rejection_digest,
            batch_cancellation_intent_digest=cancellation_digest,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("batch_id", "", id="empty-batch-id"),
        pytest.param("batch_id", "   ", id="blank-batch-id"),
        pytest.param("batch_id", 7, id="non-string-batch-id"),
        pytest.param("manifest_id", "", id="empty-manifest-id"),
        pytest.param("manifest_id", 7, id="non-string-manifest-id"),
        pytest.param("manifest_item_key", "", id="empty-item-key"),
        pytest.param("manifest_item_key", 7, id="non-string-item-key"),
        pytest.param("shard_plan_id", "", id="empty-plan-id"),
        pytest.param("shard_plan_id", 7, id="non-string-plan-id"),
    ],
)
def test_scope_item_rejects_invalid_identity_fields(field: str, value: object) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _scope_item(**{field: value})

    assert caught.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("source_batch_version", True, id="bool-source-version"),
        pytest.param("source_batch_version", -1, id="negative-source-version"),
        pytest.param("source_batch_version", "3", id="string-source-version"),
        pytest.param("shard_plan_version", True, id="bool-plan-version"),
        pytest.param("shard_plan_version", -1, id="negative-plan-version"),
        pytest.param("shard_plan_version", "2", id="string-plan-version"),
    ],
)
def test_scope_item_rejects_invalid_versions(field: str, value: object) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _scope_item(**{field: value})

    assert caught.value.field == field


def test_scope_item_rejects_raw_terminal_kind() -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _scope_item(terminal_kind="rejection")

    assert caught.value.field == "terminal_kind"


@pytest.mark.parametrize(
    ("terminal_name", "field"),
    [
        pytest.param("REJECTION", "rejection_fact_digest", id="rejection"),
        pytest.param(
            "PRESTART_CANCEL",
            "batch_cancellation_intent_digest",
            id="cancellation",
        ),
        pytest.param("REJECTION", "manifest_digest", id="manifest"),
        pytest.param("REJECTION", "shard_plan_digest", id="plan"),
        pytest.param(
            "REJECTION",
            "materialized_run_absence_digest",
            id="run-absence",
        ),
    ],
)
def test_scope_item_rejects_non_digest_bindings(terminal_name: str, field: str) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _scope_item(
            terminal_name=terminal_name,
            **{field: "sha256:not-a-typed-digest"},
        )

    assert caught.value.field == field


def test_scope_item_resolution_is_fixed_to_not_started() -> None:
    """The 001F neutral fact cannot claim a different item resolution."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _scope_item(resolution="started")

    assert caught.value.field == "resolution"


def _planned_snapshot(*scope_items, **overrides: object):
    from qarunner.domain import BatchPreexecutionScopeKind, BatchPreexecutionSnapshot

    values: dict[str, object] = {
        "batch_id": "batch-001",
        "source_batch_version": 3,
        "scope_kind": BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED,
        "submission_digest": _digest("submission"),
        "preplan_scope_digest": None,
        "manifest_digest": _digest("manifest-001"),
        "shard_plan_version": 2,
        "shard_plan_digest": _digest("plan-001-v2"),
        "canonical_run_set_digest": _digest("canonical-empty-run-set"),
        "materialized_run_absence_digest": _digest("no-materialized-runs"),
        "execution_absence_snapshot_digest": _digest("no-execution"),
        "task_stop_fact_digests": (),
        "scope_items": scope_items,
        "item_coverage_proof_digest": _digest("item-coverage"),
    }
    values.update(overrides)
    return BatchPreexecutionSnapshot(**values)


def test_planned_unmaterialized_snapshot_accepts_canonical_typed_scope_items() -> None:
    first = _scope_item("case-001")
    second = _scope_item("case-002")

    snapshot = _planned_snapshot(first, second)

    assert snapshot.scope_items == (first, second)
    assert tuple(item.digest for item in snapshot.scope_items) == (
        first.digest,
        second.digest,
    )
    assert snapshot.item_coverage_proof_digest == _digest("item-coverage")


@pytest.mark.parametrize(
    "scope_items",
    [
        pytest.param([], id="mutable-list"),
        pytest.param((_digest("raw-item-digest"),), id="raw-digest"),
    ],
)
def test_planned_snapshot_requires_an_immutable_tuple_of_typed_scope_items(
    scope_items,
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _planned_snapshot(scope_items=scope_items)

    assert caught.value.field == "scope_items"


def test_planned_snapshot_rejects_scope_items_out_of_manifest_key_order() -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _planned_snapshot(_scope_item("case-002"), _scope_item("case-001"))

    assert caught.value.field == "scope_items"


def test_planned_snapshot_rejects_duplicate_manifest_item_keys() -> None:
    from qarunner.domain import DomainValidationError

    item = _scope_item("case-001")

    with pytest.raises(DomainValidationError) as caught:
        _planned_snapshot(item, item)

    assert caught.value.field == "scope_items"


@pytest.mark.parametrize(
    ("scope_items", "overrides"),
    [
        pytest.param((), {}, id="empty-items"),
        pytest.param(
            (_scope_item("case-001"),),
            {"item_coverage_proof_digest": None},
            id="missing-coverage-proof",
        ),
        pytest.param(
            (_scope_item("case-001"),),
            {"canonical_run_set_digest": None},
            id="missing-empty-run-set",
        ),
        pytest.param(
            (_scope_item("case-001"),),
            {"manifest_digest": None},
            id="missing-manifest",
        ),
        pytest.param(
            (_scope_item("case-001"),),
            {"shard_plan_version": None},
            id="missing-plan-version",
        ),
        pytest.param(
            (_scope_item("case-001"),),
            {"shard_plan_digest": None},
            id="missing-plan-digest",
        ),
    ],
)
def test_planned_snapshot_requires_items_scope_coverage_and_empty_run_set(
    scope_items,
    overrides,
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError):
        _planned_snapshot(*scope_items, **overrides)


@pytest.mark.parametrize(
    "changed_item",
    [
        pytest.param(_scope_item(batch_id="batch-999"), id="batch"),
        pytest.param(_scope_item(source_batch_version=4), id="source-version"),
        pytest.param(
            _scope_item(manifest_id="manifest-999"),
            id="manifest-identity",
        ),
        pytest.param(
            _scope_item(manifest_digest=_digest("manifest-999")),
            id="manifest-digest",
        ),
        pytest.param(_scope_item(shard_plan_id="plan-999"), id="plan-identity"),
        pytest.param(_scope_item(shard_plan_version=3), id="plan-version"),
        pytest.param(
            _scope_item(shard_plan_digest=_digest("plan-999")),
            id="plan-digest",
        ),
        pytest.param(
            _scope_item(materialized_run_absence_digest=_digest("different-run-absence")),
            id="run-absence",
        ),
        pytest.param(
            _scope_item(terminal_name="PRESTART_CANCEL"),
            id="terminal-command",
        ),
        pytest.param(
            _scope_item(rejection_fact_digest=_digest("rejection-999")),
            id="command-digest",
        ),
    ],
)
def test_planned_snapshot_rejects_scope_item_binding_drift(changed_item) -> None:
    """All planned items belong to one command, Batch, Manifest, Plan, and no-Run proof."""
    from qarunner.domain import DomainValidationError

    canonical = _scope_item("case-001")
    drifted = replace(changed_item, manifest_item_key="case-002")

    with pytest.raises(DomainValidationError) as caught:
        _planned_snapshot(canonical, drifted)

    assert caught.value.field == "scope_items"


def test_preplan_snapshot_rejects_planned_scope_item_facts() -> None:
    from qarunner.domain import (
        BatchPreexecutionScopeKind,
        BatchPreexecutionSnapshot,
        DomainValidationError,
    )

    with pytest.raises(DomainValidationError) as caught:
        BatchPreexecutionSnapshot(
            batch_id="batch-001",
            source_batch_version=3,
            scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
            submission_digest=_digest("submission"),
            preplan_scope_digest=_digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
            materialized_run_absence_digest=_digest("no-materialized-runs"),
            execution_absence_snapshot_digest=_digest("no-execution"),
            task_stop_fact_digests=(),
            scope_items=(_scope_item("case-001"),),
            item_coverage_proof_digest=None,
        )

    assert caught.value.field == "scope_items"
