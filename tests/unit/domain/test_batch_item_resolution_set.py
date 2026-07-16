"""T-M0-STATE-001H H2a: canonical Batch item resolution set."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-resolution.v1", payload={"label": label}
    )


def _run_entry(index: int, classification="passed"):
    from qarunner.domain import (
        BatchItemClassification,
        BatchItemResolution,
        BatchItemSourceKind,
        RunItemKey,
    )

    return BatchItemResolution(
        item_key=RunItemKey("manifest-1", index),
        source_kind=BatchItemSourceKind.RUN_RESOLUTION,
        source_run_id="run-1",
        source_run_version=3,
        source_run_basis_digest=_digest("run-basis"),
        source_run_item_resolution_set_digest=_digest("run-set"),
        source_item_resolution_digest=_digest(f"run-item-{index}"),
        not_executed_fact_schema=None,
        not_executed_fact_digest=None,
        cancellation_scope_item_digest=None,
        classification=BatchItemClassification(classification),
    )


def _not_executed_entry(index: int):
    from qarunner.domain import (
        BatchItemClassification,
        BatchItemResolution,
        BatchItemSourceKind,
        RunItemKey,
    )

    return BatchItemResolution(
        item_key=RunItemKey("manifest-1", index),
        source_kind=BatchItemSourceKind.NOT_EXECUTED,
        source_run_id=None,
        source_run_version=None,
        source_run_basis_digest=None,
        source_run_item_resolution_set_digest=None,
        source_item_resolution_digest=None,
        not_executed_fact_schema="qep.batch-cancellation-scope-item.v1",
        not_executed_fact_digest=_digest(f"not-executed-{index}"),
        cancellation_scope_item_digest=_digest(f"scope-{index}"),
        classification=BatchItemClassification.NOT_EXECUTED,
    )


def test_public_facade_exposes_batch_item_resolution_contract() -> None:
    from qarunner.domain import (
        BatchItemClassification,
        BatchItemResolution,
        BatchItemResolutionSet,
        BatchItemSourceKind,
    )

    assert BatchItemSourceKind.RUN_RESOLUTION.value == "run_resolution"
    assert BatchItemClassification.NOT_EXECUTED.value == "not_executed"
    assert BatchItemResolution is not None
    assert BatchItemResolutionSet is not None


def test_entry_union_generates_canonical_digest_for_each_source_kind() -> None:
    run_entry = _run_entry(0)
    skipped_entry = _not_executed_entry(1)

    assert run_entry.batch_item_resolution_digest == _run_entry(0).batch_item_resolution_digest
    assert (
        skipped_entry.batch_item_resolution_digest
        == _not_executed_entry(1).batch_item_resolution_digest
    )
    assert run_entry.batch_item_resolution_digest != skipped_entry.batch_item_resolution_digest


def test_set_orders_manifest_entries_and_recomputes_six_counts_and_digests() -> None:
    from qarunner.domain import BatchItemResolutionSet, RunItemKey

    entries = (
        _not_executed_entry(5),
        _run_entry(2, "infra_failed"),
        _run_entry(0, "passed"),
        _run_entry(4, "unknown_lineage"),
        _run_entry(1, "test_failed"),
        _run_entry(3, "cancelled"),
    )
    expected = tuple(RunItemKey("manifest-1", index) for index in range(6))
    value = BatchItemResolutionSet.build(
        batch_id="batch-1",
        source_batch_version=7,
        manifest_id="manifest-1",
        manifest_digest=_digest("manifest"),
        shard_plan_id="plan-1",
        shard_plan_version=2,
        shard_plan_digest=_digest("plan"),
        canonical_run_set_digest=_digest("runs"),
        expected_item_keys=expected,
        entries=entries,
    )

    assert tuple(entry.item_key for entry in value.entries) == expected
    assert value.counts.original_denominator == 6
    assert value.item_count == 6
    assert value.passed_count == value.test_failed_count == value.infra_failed_count == 1
    assert value.cancelled_count == value.unknown_lineage_count == value.not_executed_count == 1
    assert (
        value.counts.passed_count,
        value.counts.test_failed_count,
        value.counts.infra_failed_count,
        value.counts.cancelled_count,
        value.counts.unknown_lineage_count,
        value.counts.not_executed_count,
    ) == (1, 1, 1, 1, 1, 1)
    assert (
        value.resolution_set_digest
        == BatchItemResolutionSet.build(
            batch_id="batch-1",
            source_batch_version=7,
            manifest_id="manifest-1",
            manifest_digest=_digest("manifest"),
            shard_plan_id="plan-1",
            shard_plan_version=2,
            shard_plan_digest=_digest("plan"),
            canonical_run_set_digest=_digest("runs"),
            expected_item_keys=expected,
            entries=tuple(reversed(entries)),
        ).resolution_set_digest
    )


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"item_key": object()}, "item_key"),
        ({"source_kind": object()}, "source_kind"),
        ({"classification": object()}, "classification"),
        ({"source_run_id": ""}, "source_run_id"),
        ({"source_run_version": -1}, "source_run_version"),
        ({"source_run_basis_digest": object()}, "run_resolution_source"),
        ({"not_executed_fact_schema": "unexpected"}, "not_executed_source"),
        ({"classification": "not_executed"}, "classification"),
    ],
)
def test_run_resolution_entry_rejects_invalid_or_mixed_source_fields(change, field) -> None:
    with pytest.raises(ValueError) as caught:
        replace(_run_entry(0), **change)
    assert caught.value.field == field


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"source_run_id": "run-1"}, "run_resolution_source"),
        ({"not_executed_fact_schema": ""}, "not_executed_source"),
        ({"not_executed_fact_digest": object()}, "not_executed_source"),
        ({"cancellation_scope_item_digest": object()}, "not_executed_source"),
        ({"classification": "passed"}, "classification"),
    ],
)
def test_not_executed_entry_rejects_invalid_or_mixed_source_fields(change, field) -> None:
    with pytest.raises(ValueError) as caught:
        replace(_not_executed_entry(0), **change)
    assert caught.value.field == field


def _set(entries=None, expected=None, **changes):
    from qarunner.domain import BatchItemResolutionSet, RunItemKey

    entries = tuple(entries or (_run_entry(0), _not_executed_entry(1)))
    expected = tuple(expected or (RunItemKey("manifest-1", 0), RunItemKey("manifest-1", 1)))
    envelope = {
        "batch_id": "batch-1",
        "source_batch_version": 7,
        "manifest_id": "manifest-1",
        "manifest_digest": _digest("manifest"),
        "shard_plan_id": "plan-1",
        "shard_plan_version": 2,
        "shard_plan_digest": _digest("plan"),
        "canonical_run_set_digest": _digest("runs"),
    }
    envelope.update(changes)
    return BatchItemResolutionSet.build(expected_item_keys=expected, entries=entries, **envelope)


@pytest.mark.parametrize(
    ("expected", "entries", "reason"),
    [
        ((0, 1), (0,), "coverage"),
        ((0,), (0, 1), "coverage"),
        ((0, 1), (0, 0), "duplicate"),
    ],
)
def test_set_rejects_missing_extra_duplicate_and_overlapping_entries(
    expected, entries, reason
) -> None:
    from qarunner.domain import RunItemKey

    expected_keys = tuple(RunItemKey("manifest-1", index) for index in expected)
    resolved = tuple(_run_entry(index) for index in entries)
    with pytest.raises(ValueError, match=reason):
        _set(expected=expected_keys, entries=resolved)


def test_set_rejects_wrong_manifest_and_untyped_collections() -> None:
    from qarunner.domain import RunItemKey

    with pytest.raises(ValueError, match="manifest"):
        _set(
            expected=(RunItemKey("other", 0),),
            entries=(replace(_run_entry(0), item_key=RunItemKey("other", 0)),),
        )
    with pytest.raises(ValueError, match="expected_item_keys"):
        _set(expected=(object(),))
    with pytest.raises(ValueError, match="entries"):
        _set(entries=(object(),))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_id", ""),
        ("manifest_id", ""),
        ("shard_plan_id", ""),
        ("source_batch_version", True),
        ("shard_plan_version", -1),
        ("manifest_digest", object()),
        ("shard_plan_digest", object()),
        ("canonical_run_set_digest", object()),
    ],
)
def test_set_rejects_invalid_envelope(field, value) -> None:
    with pytest.raises(ValueError) as caught:
        _set(**{field: value})
    assert caught.value.field == field


def test_entry_and_set_digests_bind_every_field_without_scalar_run_outcome() -> None:
    entry = _run_entry(0)
    for field, value in (
        ("source_run_version", 4),
        ("source_run_basis_digest", _digest("other-basis")),
        ("classification", entry.classification.TEST_FAILED),
    ):
        assert replace(entry, **{field: value}).batch_item_resolution_digest != (
            entry.batch_item_resolution_digest
        )
    baseline = _set()
    assert replace(baseline, source_batch_version=8).resolution_set_digest != (
        baseline.resolution_set_digest
    )
    assert "run_outcome" not in str(baseline.canonical_payload()).lower()


def test_source_kind_and_classification_union_cannot_overlap() -> None:
    from qarunner.domain import BatchItemClassification

    with pytest.raises(ValueError, match="source_mismatch"):
        replace(_run_entry(0), classification=BatchItemClassification.NOT_EXECUTED)
    with pytest.raises(ValueError, match="source_mismatch"):
        replace(_not_executed_entry(0), classification=BatchItemClassification.PASSED)


def test_direct_set_construction_enforces_nonempty_typed_unique_canonical_entries() -> None:
    from qarunner.domain import RunItemKey

    baseline = _set()
    with pytest.raises(ValueError, match="entries"):
        replace(baseline, entries=())
    with pytest.raises(ValueError, match="entries"):
        replace(baseline, entries=(object(),))
    with pytest.raises(ValueError, match="duplicate"):
        replace(baseline, entries=(_run_entry(0), _run_entry(0)))
    with pytest.raises(ValueError, match="ordered"):
        replace(baseline, entries=tuple(reversed(baseline.entries)))
    with pytest.raises(ValueError, match="coverage"):
        replace(baseline, entries=(baseline.entries[0],))
    with pytest.raises(ValueError, match="expected_item_keys"):
        replace(baseline, expected_item_keys=(object(),))
    with pytest.raises(ValueError, match="duplicate"):
        replace(baseline, expected_item_keys=(baseline.expected_item_keys[0],) * 2)
    with pytest.raises(ValueError, match="ordered"):
        replace(baseline, expected_item_keys=tuple(reversed(baseline.expected_item_keys)))
    with pytest.raises(ValueError, match="manifest"):
        replace(baseline, expected_item_keys=(RunItemKey("other", 0),))
    with pytest.raises(ValueError, match="manifest"):
        replace(
            baseline,
            entries=(replace(baseline.entries[0], item_key=RunItemKey("other", 0)),),
        )


def test_not_executed_requires_cancellation_scope_item_v1_schema() -> None:
    with pytest.raises(ValueError, match="not_executed_fact_schema"):
        replace(_not_executed_entry(0), not_executed_fact_schema="qep.other-fact.v1")
