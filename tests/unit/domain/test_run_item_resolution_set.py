"""T-M0-STATE-001G G2b: immutable ordered Run item resolution set."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(schema_version="qep.test-resolution-set.v1", payload={"label": label})


def _entry(index: int, outcome: str = "PASSED", *, unknown: bool = False):
    from qarunner.domain import (
        AttemptExecutionFact,
        EffectiveItemResolution,
        EffectiveSourceKind,
        ItemAggregationClass,
        OriginalItemResolution,
        OriginalSourceKind,
        RunItemKey,
        RunItemResolution,
        RunOutcome,
        UnknownAdjudicationDecision,
    )

    fact = AttemptExecutionFact[outcome]
    original = OriginalItemResolution(
        OriginalSourceKind.ATTEMPT_RESULT
        if outcome in {"PASSED", "TEST_FAILED"}
        else OriginalSourceKind.ATTEMPT_TERMINAL_FALLBACK,
        "attempt-1",
        1,
        1,
        _digest("items"),
        fact,
        "qep.fact.v1",
        1,
        _digest(f"fact-{index}"),
        _digest("evidence") if outcome in {"PASSED", "TEST_FAILED"} else None,
        None,
        None,
        None,
        None,
    )
    effective = EffectiveItemResolution(
        EffectiveSourceKind.AUTHORIZED_RETRY if unknown else EffectiveSourceKind.ORIGINAL,
        "attempt-2" if unknown else "attempt-1",
        2 if unknown else 1,
        2 if unknown else 1,
        _digest("items"),
        RunOutcome[outcome],
        "qep.fact.v1",
        1,
        _digest(f"retry-fact-{index}") if unknown else original.fact_digest,
        original.evidence_root_digest,
        None,
        None,
        None,
        _digest("intent") if unknown else None,
        _digest("decision") if unknown else None,
        _digest("authority") if unknown else None,
        _digest("adjudication") if unknown else None,
        UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY if unknown else None,
    )
    return RunItemResolution(
        RunItemKey("manifest-1", index),
        original,
        effective,
        _digest(f"lineage-{index}") if unknown else None,
        ItemAggregationClass.UNKNOWN_LINEAGE if unknown else ItemAggregationClass[outcome],
    )


def _set(entries=None, expected=None, **changes):
    from qarunner.domain import RunItemResolutionSet

    entries = tuple(entries or (_entry(0), _entry(1)))
    expected = tuple(expected or (entry.item_key for entry in entries))
    values = {
        "batch_id": "batch-1",
        "run_id": "run-1",
        "source_run_version": 3,
        "manifest_digest": _digest("manifest"),
        "shard_plan_digest": _digest("shard"),
        "run_item_set_digest": _digest("items"),
        "attempt_chain_digest": _digest("attempt-chain"),
        "retry_chain_digest": None,
        "adjudication_chain_digest": None,
    }
    values.update(changes)
    return RunItemResolutionSet.build(expected_item_keys=expected, entries=entries, **values)


def test_set_orders_entries_and_derives_all_three_digests() -> None:
    value = _set(entries=(_entry(1), _entry(0)))
    assert tuple(entry.item_key.item_index for entry in value.entries) == (0, 1)
    assert value.item_count == 2
    assert (
        len(
            {
                value.original_resolution_set_digest,
                value.effective_resolution_set_digest,
                value.resolution_set_digest,
            }
        )
        == 3
    )
    assert value == replace(value)


@pytest.mark.parametrize(
    "expected,entries,reason",
    [
        ((_entry(0).item_key, _entry(1).item_key), (_entry(0),), "key_set"),
        ((_entry(0).item_key,), (_entry(0), _entry(1)), "key_set"),
        ((_entry(0).item_key,), (_entry(0), _entry(0)), "duplicate"),
    ],
)
def test_build_requires_exact_unique_key_coverage(expected, entries, reason) -> None:
    with pytest.raises(ValueError, match=reason):
        _set(expected=expected, entries=entries)


def test_build_rejects_non_resolution_entries_with_stable_domain_error() -> None:
    with pytest.raises(ValueError, match="entries"):
        _set(entries=(object(),), expected=(_entry(0).item_key,))


def test_envelope_and_optional_chain_digests_are_typed() -> None:
    with pytest.raises(ValueError, match="batch_id"):
        _set(batch_id="")
    with pytest.raises(ValueError, match="source_run_version"):
        _set(source_run_version=True)
    assert _set(source_run_version=0).source_run_version == 0
    with pytest.raises(ValueError, match="retry_chain_digest"):
        _set(retry_chain_digest="raw")


@pytest.mark.parametrize(
    "outcomes,expected",
    [
        (("PASSED", "CANCELLED"), "cancelled"),
        (("CANCELLED", "TEST_FAILED"), "test_failed"),
        (("TEST_FAILED", "INFRA_FAILED"), "infra_failed"),
        (("PASSED", "PASSED"), "passed"),
    ],
)
def test_mixed_item_outcomes_have_fixed_audit_projection(outcomes, expected) -> None:
    value = _set(entries=tuple(_entry(i, outcome) for i, outcome in enumerate(outcomes)))
    assert value.audit_outcome.value == expected


def test_resolved_unknown_lineage_is_sticky_but_projects_effective_outcome() -> None:
    value = _set(
        entries=(_entry(0), _entry(1, unknown=True)),
        retry_chain_digest=_digest("retry-chain"),
        adjudication_chain_digest=_digest("adjudication-chain"),
    )
    assert value.entries[1].aggregation_class.value == "unknown_lineage"
    assert value.audit_outcome.value == "passed"


def test_digest_changes_for_envelope_chain_and_per_item_not_scalar_multiplication() -> None:
    base = _set()
    assert replace(base, source_run_version=4).resolution_set_digest != base.resolution_set_digest
    assert (
        replace(base, retry_chain_digest=_digest("retry")).resolution_set_digest
        != base.resolution_set_digest
    )
    changed = _set(entries=(_entry(0), _entry(1, "TEST_FAILED")))
    assert changed.resolution_set_digest != base.resolution_set_digest
    assert tuple(x.aggregation_class.value for x in changed.entries) == ("passed", "test_failed")


def test_canonical_payload_explicitly_contains_null_chains_and_item_digests() -> None:
    payload = _set().canonical_payload()
    assert payload["retry_chain_digest"] is None
    assert payload["adjudication_chain_digest"] is None
    assert payload["entries"][0]["item_resolution_digest"].startswith("sha256:")


def _retry_attempt(attempt_no: int, indexes=(0, 1), *, item_set_digest=None):
    from qarunner.domain import VerifiedAttemptItemResolution, VerifiedAttemptResolutionSet

    item_set_digest = item_set_digest or _digest("items")
    items = tuple(
        VerifiedAttemptItemResolution(
            _entry(index, unknown=True).item_key,
            replace(
                _entry(index, unknown=True).effective,
                attempt_id=f"attempt-{attempt_no}",
                attempt_no=attempt_no,
                attempt_fence=attempt_no,
                attempt_item_set_digest=item_set_digest,
            ),
        )
        for index in indexes
    )
    return VerifiedAttemptResolutionSet(
        attempt_id=f"attempt-{attempt_no}",
        attempt_no=attempt_no,
        attempt_fence=attempt_no,
        attempt_item_set_digest=item_set_digest,
        items=items,
    )


def test_selector_uses_latest_authorized_complete_attempt_and_ignores_partial_tail() -> None:
    from qarunner.domain import select_latest_authorized_complete_attempt

    selected = select_latest_authorized_complete_attempt(
        expected_item_keys=(_entry(0).item_key, _entry(1).item_key),
        run_item_set_digest=_digest("items"),
        original_attempt_no=1,
        original_attempt_fence=1,
        attempts=(_retry_attempt(2), _retry_attempt(3, indexes=(0,))),
        run_id="run-1",
    )
    assert selected.attempt_no == 2


@pytest.mark.parametrize(
    "attempts,reason",
    [
        ((_retry_attempt(3),), "continuous"),
        ((_retry_attempt(2), _retry_attempt(4)), "continuous"),
        ((_retry_attempt(2, item_set_digest=_digest("subset")),), "full_run_item_set"),
    ],
)
def test_selector_rejects_discontinuous_or_non_full_run_authority(attempts, reason) -> None:
    from qarunner.domain import select_latest_authorized_complete_attempt

    with pytest.raises(ValueError, match=reason):
        select_latest_authorized_complete_attempt(
            expected_item_keys=(_entry(0).item_key, _entry(1).item_key),
            run_item_set_digest=_digest("items"),
            original_attempt_no=1,
            original_attempt_fence=1,
            attempts=attempts,
            run_id="run-1",
        )


def test_selector_returns_typed_block_when_no_attempt_is_complete() -> None:
    from qarunner.domain import (
        AttemptUnknownReviewRequired,
        select_latest_authorized_complete_attempt,
    )

    with pytest.raises(AttemptUnknownReviewRequired, match="complete"):
        select_latest_authorized_complete_attempt(
            expected_item_keys=(_entry(0).item_key, _entry(1).item_key),
            run_item_set_digest=_digest("items"),
            original_attempt_no=1,
            original_attempt_fence=1,
            attempts=(_retry_attempt(2, indexes=(0,)),),
            run_id="run-1",
        )


def test_selector_validates_expected_keys_run_identity_and_original_fence() -> None:
    from qarunner.domain import select_latest_authorized_complete_attempt

    common = {
        "run_item_set_digest": _digest("items"),
        "original_attempt_no": 1,
        "original_attempt_fence": 1,
        "attempts": (_retry_attempt(2),),
        "run_id": "run-1",
    }
    for keys in ((), (_entry(0).item_key, _entry(0).item_key), (object(),)):
        with pytest.raises(ValueError, match="expected_item_keys"):
            select_latest_authorized_complete_attempt(expected_item_keys=keys, **common)
    with pytest.raises(ValueError, match="run_id"):
        select_latest_authorized_complete_attempt(
            expected_item_keys=(_entry(0).item_key, _entry(1).item_key),
            **(common | {"run_id": ""}),
        )
    with pytest.raises(ValueError, match="continuous"):
        select_latest_authorized_complete_attempt(
            expected_item_keys=(_entry(0).item_key, _entry(1).item_key),
            **(common | {"original_attempt_fence": 2}),
        )


def test_verified_attempt_requires_one_retry_authority_triple() -> None:
    from qarunner.domain import VerifiedAttemptItemResolution

    attempt = _retry_attempt(2)
    changed = VerifiedAttemptItemResolution(
        attempt.items[1].item_key,
        replace(attempt.items[1].effective, retry_intent_digest=_digest("different-intent")),
    )
    with pytest.raises(ValueError, match="retry_authority"):
        replace(attempt, items=(attempt.items[0], changed))


def test_set_and_verified_attempt_values_reject_untyped_or_noncanonical_members() -> None:
    from qarunner.domain import (
        RunItemResolutionSet,
        VerifiedAttemptItemResolution,
        VerifiedAttemptResolutionSet,
    )

    base = _set()
    with pytest.raises(ValueError, match="entries"):
        replace(base, entries=())
    with pytest.raises(ValueError, match="entries"):
        replace(base, entries=(object(),))
    with pytest.raises(ValueError, match="duplicate"):
        replace(base, entries=(base.entries[0], base.entries[0]))
    with pytest.raises(ValueError, match="not_ordered"):
        replace(base, entries=tuple(reversed(base.entries)))
    with pytest.raises(ValueError, match="item_key"):
        VerifiedAttemptItemResolution(object(), _retry_attempt(2).items[0].effective)
    with pytest.raises(ValueError, match="effective"):
        VerifiedAttemptItemResolution(_entry(0).item_key, object())
    with pytest.raises(ValueError, match="items"):
        VerifiedAttemptResolutionSet("attempt-2", 2, 2, _digest("items"), (object(),))
    item = _retry_attempt(2).items[0]
    with pytest.raises(ValueError, match="duplicate"):
        VerifiedAttemptResolutionSet("attempt-2", 2, 2, _digest("items"), (item, item))
    with pytest.raises(ValueError, match="not_authorized_retry"):
        VerifiedAttemptResolutionSet(
            "attempt-1",
            1,
            1,
            _digest("items"),
            (VerifiedAttemptItemResolution(_entry(0).item_key, _entry(0).effective),),
        )
    with pytest.raises(ValueError, match="attempt_identity_mismatch"):
        replace(_retry_attempt(2), attempt_id="different")
    assert isinstance(base, RunItemResolutionSet)
