"""T-M0-STATE-001H H3: immutable cancellation scope-item facts."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-cancellation-scope-item.v1",
        payload={"label": label},
    )


def _scope_item(index: int = 0, *, kind: str = "NOT_EXECUTED", **changes: object):
    from qarunner.domain import (
        BatchCancellationResolutionKind,
        BatchCancellationScopeItem,
        RunItemKey,
    )

    values: dict[str, object] = {
        "batch_id": "batch-1",
        "batch_cancellation_intent_digest": _digest("intent"),
        "manifest_id": "manifest-1",
        "manifest_digest": _digest("manifest"),
        "manifest_item_key": RunItemKey("manifest-1", index),
        "shard_plan_version": 2,
        "shard_plan_digest": _digest("plan"),
        "resolution_kind": BatchCancellationResolutionKind[kind],
        "run_id": None if kind == "NOT_EXECUTED" else f"run-{index}",
        "source_run_version": None if kind == "NOT_EXECUTED" else 3,
        "recorded_at": datetime(2026, 7, 16, 18, tzinfo=UTC),
    }
    values.update(changes)
    return BatchCancellationScopeItem(**values)


def _canonicalize(*, expected_item_keys, items, **changes):
    from qarunner.domain import canonicalize_batch_cancellation_scope_items

    authority = {
        "batch_id": "batch-1",
        "batch_cancellation_intent_digest": _digest("intent"),
        "manifest_id": "manifest-1",
        "manifest_digest": _digest("manifest"),
        "shard_plan_version": 2,
        "shard_plan_digest": _digest("plan"),
    }
    authority.update(changes)
    return canonicalize_batch_cancellation_scope_items(
        expected_item_keys=expected_item_keys,
        items=items,
        **authority,
    )


def test_not_executed_scope_item_is_a_distinct_canonical_001h_fact() -> None:
    from qarunner.domain import (
        BatchCancellationResolutionKind,
        BatchCancellationScopeItem,
        RunItemKey,
        canonical_digest,
    )

    item = BatchCancellationScopeItem(
        batch_id="batch-1",
        batch_cancellation_intent_digest=_digest("intent"),
        manifest_id="manifest-1",
        manifest_digest=_digest("manifest"),
        manifest_item_key=RunItemKey("manifest-1", 0),
        shard_plan_version=2,
        shard_plan_digest=_digest("plan"),
        resolution_kind=BatchCancellationResolutionKind.NOT_EXECUTED,
        run_id=None,
        source_run_version=None,
        recorded_at=datetime(2026, 7, 16, 18, tzinfo=UTC),
    )

    assert item.delivery_key == (_digest("intent"), RunItemKey("manifest-1", 0))
    assert item.scope_item_digest == canonical_digest(
        schema_version="qep.batch-cancellation-scope-item.v1",
        payload={
            "batch_id": "batch-1",
            "batch_cancellation_intent_digest": _digest("intent").value,
            "manifest_id": "manifest-1",
            "manifest_digest": _digest("manifest").value,
            "manifest_item_key": {"manifest_id": "manifest-1", "item_index": 0},
            "shard_plan_version": 2,
            "shard_plan_digest": _digest("plan").value,
            "resolution_kind": "not_executed",
            "run_id": None,
            "source_run_version": None,
            "delivery_key": {
                "batch_cancellation_intent_digest": _digest("intent").value,
                "manifest_item_key": {"manifest_id": "manifest-1", "item_index": 0},
            },
            "recorded_at": "2026-07-16T18:00:00Z",
        },
    )


def test_not_executed_scope_item_derives_batch_resolution_without_run_fields() -> None:
    from qarunner.domain import (
        BatchCancellationResolutionKind,
        BatchCancellationScopeItem,
        BatchItemClassification,
        BatchItemResolution,
        BatchItemSourceKind,
        RunItemKey,
    )

    fact = BatchCancellationScopeItem(
        batch_id="batch-1",
        batch_cancellation_intent_digest=_digest("intent"),
        manifest_id="manifest-1",
        manifest_digest=_digest("manifest"),
        manifest_item_key=RunItemKey("manifest-1", 0),
        shard_plan_version=2,
        shard_plan_digest=_digest("plan"),
        resolution_kind=BatchCancellationResolutionKind.NOT_EXECUTED,
        run_id=None,
        source_run_version=None,
        recorded_at=datetime(2026, 7, 16, 18, tzinfo=UTC),
    )

    entry = BatchItemResolution.from_not_executed(fact=fact)

    assert entry.source_kind is BatchItemSourceKind.NOT_EXECUTED
    assert entry.classification is BatchItemClassification.NOT_EXECUTED
    assert entry.item_key == fact.manifest_item_key
    assert entry.not_executed_fact_schema == "qep.batch-cancellation-scope-item.v1"
    assert entry.not_executed_fact_digest == fact.scope_item_digest
    assert entry.cancellation_scope_item_digest == fact.scope_item_digest
    assert entry.source_run_id is entry.source_run_version is None


def test_run_fanout_scope_item_requires_and_binds_run_identity_without_claiming_outcome() -> None:
    item = _scope_item(1, kind="RUN_FANOUT")

    assert item.run_id == "run-1"
    assert item.source_run_version == 3
    assert item.canonical_payload()["resolution_kind"] == "run_fanout"
    assert "outcome" not in item.canonical_payload()


def test_scope_collection_orders_exact_manifest_coverage_and_collapses_exact_replay() -> None:
    from qarunner.domain import RunItemKey

    first = _scope_item(0)
    second = _scope_item(1, kind="RUN_FANOUT")
    result = _canonicalize(
        expected_item_keys=(RunItemKey("manifest-1", 0), RunItemKey("manifest-1", 1)),
        items=(second, first, second),
    )

    assert result == (first, second)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_id", ""),
        ("manifest_id", " "),
        ("batch_cancellation_intent_digest", object()),
        ("manifest_digest", object()),
        ("shard_plan_digest", object()),
        ("manifest_item_key", object()),
        ("shard_plan_version", True),
        ("shard_plan_version", -1),
        ("resolution_kind", "not_executed"),
        ("recorded_at", datetime(2026, 7, 16, 18)),
    ],
)
def test_scope_item_rejects_invalid_identity_and_envelope(field, value) -> None:
    with pytest.raises(ValueError) as caught:
        _scope_item(**{field: value})
    assert caught.value.field == field


def test_scope_item_rejects_manifest_key_from_another_manifest() -> None:
    from qarunner.domain import RunItemKey

    with pytest.raises(ValueError, match="manifest_mismatch"):
        _scope_item(manifest_item_key=RunItemKey("other", 0))


@pytest.mark.parametrize(
    ("kind", "changes", "field"),
    [
        ("NOT_EXECUTED", {"run_id": "run-1"}, "run_binding"),
        ("NOT_EXECUTED", {"source_run_version": 1}, "run_binding"),
        ("RUN_FANOUT", {"run_id": None}, "run_id"),
        ("RUN_FANOUT", {"run_id": ""}, "run_id"),
        ("RUN_FANOUT", {"source_run_version": None}, "source_run_version"),
        ("RUN_FANOUT", {"source_run_version": True}, "source_run_version"),
        ("RUN_FANOUT", {"source_run_version": -1}, "source_run_version"),
    ],
)
def test_resolution_kind_is_a_closed_run_binding_union(kind, changes, field) -> None:
    with pytest.raises(ValueError) as caught:
        _scope_item(kind=kind, **changes)
    assert caught.value.field == field


def test_batch_resolution_factory_rejects_untyped_or_run_fanout_fact() -> None:
    from qarunner.domain import BatchItemResolution

    with pytest.raises(ValueError) as caught:
        BatchItemResolution.from_not_executed(fact=object())
    assert caught.value.field == "fact"
    with pytest.raises(ValueError, match="not_not_executed"):
        BatchItemResolution.from_not_executed(fact=_scope_item(kind="RUN_FANOUT"))


def test_not_executed_resolution_rejects_divergent_scope_fact_digests() -> None:
    from qarunner.domain import BatchItemResolution

    entry = BatchItemResolution.from_not_executed(fact=_scope_item())
    with pytest.raises(ValueError, match="digest_mismatch"):
        replace(entry, not_executed_fact_digest=_digest("forged-not-executed"))


@pytest.mark.parametrize(
    ("expected", "items", "field"),
    [
        ((), (_scope_item(),), "expected_item_keys"),
        ((object(),), (_scope_item(),), "expected_item_keys"),
        ([], (_scope_item(),), "expected_item_keys"),
        (None, (), "items"),
        (None, (object(),), "items"),
        (None, None, "items"),
    ],
)
def test_scope_collection_rejects_untyped_or_empty_inputs(expected, items, field) -> None:
    from qarunner.domain import RunItemKey

    expected = expected if expected is not None else (RunItemKey("manifest-1", 0),)
    with pytest.raises(ValueError) as caught:
        _canonicalize(expected_item_keys=expected, items=items)
    assert caught.value.field == field


def test_scope_collection_rejects_duplicate_expected_and_missing_or_extra_facts() -> None:
    from qarunner.domain import RunItemKey

    key = RunItemKey("manifest-1", 0)
    with pytest.raises(ValueError, match="duplicate"):
        _canonicalize(expected_item_keys=(key, key), items=(_scope_item(),))
    with pytest.raises(ValueError, match="coverage"):
        _canonicalize(
            expected_item_keys=(key, RunItemKey("manifest-1", 1)), items=(_scope_item(),)
        )
    with pytest.raises(ValueError, match="coverage"):
        _canonicalize(expected_item_keys=(key,), items=(_scope_item(), _scope_item(1)))


def test_scope_collection_rejects_same_delivery_key_with_different_digest() -> None:
    from qarunner.domain import RunItemKey

    fact = _scope_item()
    conflicting = replace(fact, recorded_at=datetime(2026, 7, 16, 19, tzinfo=UTC))
    with pytest.raises(ValueError, match="replay_conflict"):
        _canonicalize(
            expected_item_keys=(RunItemKey("manifest-1", 0),),
            items=(fact, conflicting),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_id", "batch-2"),
        ("batch_cancellation_intent_digest", _digest("other-intent")),
        ("manifest_digest", _digest("other-manifest")),
        ("shard_plan_version", 3),
        ("shard_plan_digest", _digest("other-plan")),
    ],
)
def test_scope_collection_rejects_mixed_authoritative_envelope(field, value) -> None:
    from qarunner.domain import RunItemKey

    with pytest.raises(ValueError, match="envelope_mismatch"):
        _canonicalize(
            expected_item_keys=(RunItemKey("manifest-1", 0), RunItemKey("manifest-1", 1)),
            items=(_scope_item(0), replace(_scope_item(1), **{field: value})),
        )


def test_scope_collection_rejects_internally_coherent_but_wrong_frozen_envelope() -> None:
    from qarunner.domain import RunItemKey, canonicalize_batch_cancellation_scope_items

    wrong = _scope_item(batch_id="batch-other")
    with pytest.raises(ValueError, match="authoritative_envelope_mismatch"):
        canonicalize_batch_cancellation_scope_items(
            batch_id="batch-1",
            batch_cancellation_intent_digest=_digest("intent"),
            manifest_id="manifest-1",
            manifest_digest=_digest("manifest"),
            shard_plan_version=2,
            shard_plan_digest=_digest("plan"),
            expected_item_keys=(RunItemKey("manifest-1", 0),),
            items=(wrong,),
        )
