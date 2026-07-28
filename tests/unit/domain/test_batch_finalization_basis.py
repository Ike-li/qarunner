"""T-M0-STATE-001H H4: immutable Batch finalization basis."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-finalization-basis.v1", payload={"label": label}
    )


def _canonical_run_set(batch_id: str, *run_ids: str):
    from qarunner.domain import canonical_materialized_run_set_digest

    return canonical_materialized_run_set_digest(batch_id=batch_id, run_ids=run_ids)


def test_terminal_run_ref_is_derived_from_closed_immutable_run_basis() -> None:
    from qarunner.domain import BatchTerminalRunRef
    from tests.unit.application.test_finalize_run import bound_basis
    from tests.unit.domain.test_run_finalization_basis import resolution_set

    resolved = resolution_set()
    basis = bound_basis(resolved)
    ref = BatchTerminalRunRef.from_basis(basis=basis)

    assert ref.canonical_payload() == {
        "run_id": resolved.run_id,
        "source_run_version": resolved.source_run_version,
        "run_basis_digest": basis.basis_digest.value,
        "run_outcome": "passed",
        "run_item_set_digest": resolved.run_item_set_digest.value,
        "original_resolution_set_digest": resolved.original_resolution_set_digest.value,
        "effective_resolution_set_digest": resolved.effective_resolution_set_digest.value,
        "item_resolution_set_digest": resolved.resolution_set_digest.value,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", ""),
        ("source_run_version", True),
        ("source_run_version", -1),
        ("run_basis_digest", object()),
        ("run_outcome", "passed"),
        ("run_item_set_digest", object()),
        ("original_resolution_set_digest", object()),
        ("effective_resolution_set_digest", object()),
        ("item_resolution_set_digest", object()),
    ],
)
def test_terminal_run_ref_rejects_invalid_immutable_fields(field, value) -> None:
    from qarunner.domain import BatchTerminalRunRef
    from tests.unit.application.test_finalize_run import bound_basis

    ref = BatchTerminalRunRef.from_basis(basis=bound_basis())
    with pytest.raises(ValueError) as caught:
        replace(ref, **{field: value})
    assert caught.value.field == field


def test_terminal_run_ref_rejects_untyped_basis() -> None:
    from qarunner.domain import BatchTerminalRunRef

    with pytest.raises(ValueError) as caught:
        BatchTerminalRunRef.from_basis(basis=object())
    assert caught.value.field == "basis"


def test_non_run_ref_is_derived_from_not_executed_scope_fact_with_item_identity() -> None:
    from qarunner.domain import BatchNonRunResolutionRef
    from tests.unit.domain.test_batch_cancellation_scope_item import _scope_item

    fact = _scope_item(2)
    ref = BatchNonRunResolutionRef.from_scope_item(fact=fact)

    assert ref.canonical_payload() == {
        "item_key": {"manifest_id": "manifest-1", "item_index": 2},
        "not_executed_fact_schema": "qep.batch-cancellation-scope-item.v1",
        "not_executed_fact_digest": fact.scope_item_digest.value,
    }


def test_unknown_ref_binds_item_resolution_lineage_and_adjudication() -> None:
    from qarunner.domain import BatchUnknownFactRef
    from tests.unit.domain.test_run_item_resolution_set import _entry

    resolution = _entry(0, unknown=True)
    ref = BatchUnknownFactRef.from_resolution(resolution=resolution)

    assert ref.canonical_payload() == {
        "item_key": {"manifest_id": "manifest-1", "item_index": 0},
        "source_item_resolution_digest": resolution.item_resolution_digest.value,
        "unknown_lineage_digest": resolution.unknown_lineage_digest.value,
        "adjudication_digest": resolution.effective.adjudication_digest.value,
    }


def _basis_inputs():
    from qarunner.domain import (
        BatchItemResolution,
        BatchItemResolutionSet,
        RunItemKey,
        evaluate_batch_outcome,
    )
    from tests.unit.application.test_finalize_run import bound_basis
    from tests.unit.domain.test_batch_cancellation_scope_item import _scope_item
    from tests.unit.domain.test_batch_finalization import _policy
    from tests.unit.domain.test_run_finalization_basis import resolution_set

    run_set = resolution_set()
    run_basis = bound_basis(run_set)
    run_scope = _scope_item(
        0,
        kind="RUN_FANOUT",
        batch_id=run_set.batch_id,
        manifest_digest=run_set.manifest_digest,
        shard_plan_digest=run_set.shard_plan_digest,
        run_id=run_set.run_id,
        source_run_version=run_set.source_run_version,
    )
    not_executed_scope = _scope_item(
        1,
        batch_id=run_set.batch_id,
        manifest_digest=run_set.manifest_digest,
        shard_plan_digest=run_set.shard_plan_digest,
    )
    resolution = BatchItemResolutionSet.build(
        batch_id=run_set.batch_id,
        source_batch_version=4,
        manifest_id="manifest-1",
        manifest_digest=run_set.manifest_digest,
        shard_plan_id="plan-1",
        shard_plan_version=2,
        shard_plan_digest=run_set.shard_plan_digest,
        canonical_run_set_digest=_canonical_run_set(run_set.batch_id, run_set.run_id),
        expected_item_keys=(RunItemKey("manifest-1", 0), RunItemKey("manifest-1", 1)),
        entries=(
            BatchItemResolution.from_run_resolution(
                basis=run_basis,
                resolution_set=run_set,
                resolution=run_set.entries[0],
            ),
            BatchItemResolution.from_not_executed(fact=not_executed_scope),
        ),
    )
    policy = _policy()
    evaluation = evaluate_batch_outcome(
        counts=resolution.counts,
        policy=policy,
        suite_id=policy.suite_id,
        batch_cancellation_intent_digest=not_executed_scope.batch_cancellation_intent_digest,
    )
    return {
        "resolution_set": resolution,
        "run_resolution_sets": (run_set,),
        "run_bases": (run_basis,),
        "cancellation_scope_items": (not_executed_scope, run_scope),
        "policy": policy,
        "evaluation": evaluation,
    }


def test_basis_builder_recomputes_strong_completeness_proof_and_canonical_refs() -> None:
    from qarunner.domain import BatchFinalizationBasis

    value = BatchFinalizationBasis.build(**_basis_inputs())

    assert value.schema_version == "qep.batch-finalization-basis.v1"
    assert value.item_count == value.counts.original_denominator == 2
    assert tuple(ref.run_id for ref in value.terminal_run_refs) == ("run-1",)
    assert tuple(ref.item_key.item_index for ref in value.non_run_refs) == (1,)
    assert value.unknown_fact_refs == ()
    assert (
        value.completeness_proof_digest
        == BatchFinalizationBasis.build(**_basis_inputs()).completeness_proof_digest
    )
    assert value.basis_digest == BatchFinalizationBasis.build(**_basis_inputs()).basis_digest


def test_basis_proof_critical_digests_are_derived_not_constructor_inputs() -> None:
    from qarunner.domain import BatchFinalizationBasis

    value = BatchFinalizationBasis.build(**_basis_inputs())

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        replace(value, completeness_proof_digest=_digest("forged-proof"))
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        replace(value, batch_item_resolution_set_digest=_digest("forged-resolution-set"))


def test_direct_basis_rejects_terminal_ref_forged_away_from_typed_run_basis() -> None:
    from qarunner.domain import BatchFinalizationBasis

    value = BatchFinalizationBasis.build(**_basis_inputs())
    forged_ref = replace(value.terminal_run_refs[0], run_basis_digest=_digest("forged-basis"))

    with pytest.raises(ValueError, match="terminal_run_refs"):
        replace(value, terminal_run_refs=(forged_ref,))


def test_direct_basis_rejects_unknown_ref_forged_away_from_typed_resolution() -> None:
    from qarunner.domain import BatchFinalizationBasis

    value = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    forged_ref = replace(
        value.unknown_fact_refs[0], adjudication_digest=_digest("forged-adjudication")
    )

    with pytest.raises(ValueError, match="unknown_fact_refs"):
        replace(value, unknown_fact_refs=(forged_ref,))


def test_direct_basis_rejects_jointly_forged_non_run_resolution_and_ref() -> None:
    from qarunner.domain import BatchFinalizationBasis

    value = BatchFinalizationBasis.build(**_basis_inputs())
    forged_digest = _digest("forged-not-executed-fact")
    run_entry, non_run_entry = value.resolution_set.entries
    forged_entry = replace(
        non_run_entry,
        not_executed_fact_digest=forged_digest,
        cancellation_scope_item_digest=forged_digest,
    )
    forged_resolution_set = replace(
        value.resolution_set,
        entries=(run_entry, forged_entry),
    )
    forged_ref = replace(value.non_run_refs[0], not_executed_fact_digest=forged_digest)

    with pytest.raises(ValueError, match="non_run_refs"):
        replace(
            value,
            resolution_set=forged_resolution_set,
            non_run_refs=(forged_ref,),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("item_key", object()),
        ("not_executed_fact_schema", "qep.other.v1"),
        ("not_executed_fact_digest", object()),
    ],
)
def test_non_run_ref_rejects_invalid_contract(field, value) -> None:
    from qarunner.domain import BatchNonRunResolutionRef
    from tests.unit.domain.test_batch_cancellation_scope_item import _scope_item

    ref = BatchNonRunResolutionRef.from_scope_item(fact=_scope_item())
    with pytest.raises(ValueError) as caught:
        replace(ref, **{field: value})
    assert caught.value.field == field


def test_non_run_ref_factory_rejects_untyped_or_run_fanout_fact() -> None:
    from qarunner.domain import BatchNonRunResolutionRef
    from tests.unit.domain.test_batch_cancellation_scope_item import _scope_item

    with pytest.raises(ValueError) as caught:
        BatchNonRunResolutionRef.from_scope_item(fact=object())
    assert caught.value.field == "fact"
    with pytest.raises(ValueError, match="not_not_executed"):
        BatchNonRunResolutionRef.from_scope_item(fact=_scope_item(kind="RUN_FANOUT"))


@pytest.mark.parametrize(
    "field",
    [
        "source_item_resolution_digest",
        "unknown_lineage_digest",
        "adjudication_digest",
    ],
)
def test_unknown_ref_rejects_invalid_digest_fields(field) -> None:
    from qarunner.domain import BatchUnknownFactRef
    from tests.unit.domain.test_run_item_resolution_set import _entry

    ref = BatchUnknownFactRef.from_resolution(resolution=_entry(0, unknown=True))
    with pytest.raises(ValueError) as caught:
        replace(ref, **{field: object()})
    assert caught.value.field == field


def test_unknown_ref_rejects_invalid_item_and_non_unknown_resolution() -> None:
    from qarunner.domain import BatchUnknownFactRef
    from tests.unit.domain.test_run_item_resolution_set import _entry

    ref = BatchUnknownFactRef.from_resolution(resolution=_entry(0, unknown=True))
    with pytest.raises(ValueError, match="item_key"):
        replace(ref, item_key=object())
    with pytest.raises(ValueError) as caught:
        BatchUnknownFactRef.from_resolution(resolution=object())
    assert caught.value.field == "resolution"
    with pytest.raises(ValueError, match="not_unknown_lineage"):
        BatchUnknownFactRef.from_resolution(resolution=_entry(0))


def _unknown_basis_inputs():
    from qarunner.domain import (
        BatchItemResolution,
        BatchItemResolutionSet,
        evaluate_batch_outcome,
    )
    from tests.unit.application.test_finalize_run import bound_basis
    from tests.unit.domain.test_batch_finalization import _policy
    from tests.unit.domain.test_run_item_resolution_set import _entry, _set

    item = _entry(0, unknown=True)
    run_set = _set(
        entries=(item,),
        expected=(item.item_key,),
        retry_chain_digest=_digest("retry-chain"),
        adjudication_chain_digest=_digest("adjudication-chain"),
    )
    run_basis = bound_basis(run_set)
    resolution = BatchItemResolutionSet.build(
        batch_id=run_set.batch_id,
        source_batch_version=4,
        manifest_id="manifest-1",
        manifest_digest=run_set.manifest_digest,
        shard_plan_id="plan-1",
        shard_plan_version=2,
        shard_plan_digest=run_set.shard_plan_digest,
        canonical_run_set_digest=_canonical_run_set(run_set.batch_id, run_set.run_id),
        expected_item_keys=(item.item_key,),
        entries=(
            BatchItemResolution.from_run_resolution(
                basis=run_basis, resolution_set=run_set, resolution=item
            ),
        ),
    )
    policy = _policy()
    return {
        "resolution_set": resolution,
        "run_resolution_sets": (run_set,),
        "run_bases": (run_basis,),
        "cancellation_scope_items": (),
        "policy": policy,
        "evaluation": evaluate_batch_outcome(
            counts=resolution.counts,
            policy=policy,
            suite_id=policy.suite_id,
            batch_cancellation_intent_digest=None,
        ),
    }


def test_basis_derives_unknown_refs_and_binds_them_into_completeness_proof() -> None:
    from qarunner.domain import BatchFinalizationBasis

    value = BatchFinalizationBasis.build(**_unknown_basis_inputs())

    assert len(value.unknown_fact_refs) == 1
    assert value.unknown_fact_refs[0].item_key.item_index == 0
    assert value.batch_outcome.value == "partial"
    changed = replace(
        value.unknown_fact_refs[0], adjudication_digest=_digest("other-adjudication")
    )
    assert changed.canonical_payload() != value.unknown_fact_refs[0].canonical_payload()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resolution_set", object()),
        ("policy", object()),
        ("evaluation", object()),
        ("run_resolution_sets", object()),
        ("run_resolution_sets", (object(),)),
        ("run_bases", object()),
        ("run_bases", (object(),)),
        ("cancellation_scope_items", object()),
        ("cancellation_scope_items", (object(),)),
    ],
)
def test_basis_rejects_untyped_direct_sources(field, value) -> None:
    from qarunner.domain import BatchFinalizationBasis

    basis = BatchFinalizationBasis.build(**_basis_inputs())
    with pytest.raises(ValueError) as caught:
        replace(basis, **{field: value})
    assert caught.value.field == field


def test_direct_basis_rejects_run_source_set_mismatch_overlap_and_resolution_drift() -> None:
    from qarunner.domain import BatchFinalizationBasis
    from tests.unit.application.test_finalize_run import bound_basis

    value = BatchFinalizationBasis.build(**_basis_inputs())
    with pytest.raises(ValueError, match="run_set_mismatch"):
        replace(value, run_bases=())

    second_set = replace(value.run_resolution_sets[0], run_id="run-2")
    second_basis = bound_basis(second_set)
    with pytest.raises(ValueError, match="item_overlap"):
        replace(
            value,
            run_resolution_sets=value.run_resolution_sets + (second_set,),
            run_bases=value.run_bases + (second_basis,),
        )

    with pytest.raises(ValueError, match="run_sources_mismatch"):
        replace(
            value,
            resolution_set=replace(
                value.resolution_set,
                entries=(value.resolution_set.entries[1],),
                expected_item_keys=(value.resolution_set.entries[1].item_key,),
            ),
        )


@pytest.mark.parametrize("field", ["batch_id", "manifest_digest", "shard_plan_digest"])
def test_direct_basis_rejects_resolution_envelope_rebound_away_from_run_facts(field) -> None:
    from qarunner.domain import BatchFinalizationBasis

    value = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    changes = {field: "batch-forged" if field == "batch_id" else _digest(f"forged-{field}")}
    if field == "batch_id":
        changes["canonical_run_set_digest"] = _canonical_run_set(
            "batch-forged", value.run_resolution_sets[0].run_id
        )

    with pytest.raises(ValueError, match="envelope_mismatch"):
        replace(value, resolution_set=replace(value.resolution_set, **changes))


def test_direct_basis_recomputes_evaluation_run_set_and_cancel_scope_presence() -> None:
    from qarunner.domain import BatchFinalizationBasis, BatchState
    from tests.unit.domain.test_batch_cancellation_scope_item import _scope_item

    value = BatchFinalizationBasis.build(**_basis_inputs())
    with pytest.raises(ValueError, match="evaluation"):
        replace(value, evaluation=replace(value.evaluation, outcome=BatchState.FAILED))
    with pytest.raises(ValueError, match="canonical_run_set_digest"):
        replace(
            value,
            resolution_set=replace(
                value.resolution_set,
                canonical_run_set_digest=_canonical_run_set(value.batch_id, "run-other"),
            ),
        )

    unknown = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    with pytest.raises(ValueError, match="cancellation_scope_items"):
        replace(unknown, cancellation_scope_items=(_scope_item(),))


def test_basis_rejects_noncanonical_refs() -> None:
    from qarunner.domain import BatchFinalizationBasis, RunItemKey

    basis = BatchFinalizationBasis.build(**_basis_inputs())
    with pytest.raises(ValueError, match="terminal_run_refs"):
        replace(basis, terminal_run_refs=(object(),))
    with pytest.raises(ValueError, match="duplicate"):
        replace(basis, terminal_run_refs=basis.terminal_run_refs * 2)
    later = replace(basis.terminal_run_refs[0], run_id="run-2")
    with pytest.raises(ValueError, match="not_ordered"):
        replace(basis, terminal_run_refs=(later, basis.terminal_run_refs[0]))
    earlier_non_run = replace(basis.non_run_refs[0], item_key=RunItemKey("manifest-1", 0))
    with pytest.raises(ValueError, match="not_ordered"):
        replace(basis, non_run_refs=(basis.non_run_refs[0], earlier_non_run))


def test_basis_builder_rejects_duplicate_run_sources() -> None:
    from qarunner.domain import BatchFinalizationBasis

    inputs = _basis_inputs()
    inputs["run_resolution_sets"] *= 2
    with pytest.raises(ValueError, match="duplicate_run"):
        BatchFinalizationBasis.build(**inputs)


def test_basis_builder_recomputes_canonical_run_set_from_actual_run_ids() -> None:
    from qarunner.domain import BatchFinalizationBasis

    inputs = _basis_inputs()
    inputs["resolution_set"] = replace(
        inputs["resolution_set"],
        canonical_run_set_digest=_canonical_run_set("batch-1", "run-other"),
    )

    with pytest.raises(ValueError, match="canonical_run_set_digest"):
        BatchFinalizationBasis.build(**inputs)


@pytest.mark.parametrize(
    "change",
    ["outcome", "suite_id"],
)
def test_basis_builder_recomputes_and_rejects_tampered_outcome_evaluation(change) -> None:
    from qarunner.domain import BatchFinalizationBasis, BatchState

    inputs = _basis_inputs()
    changes = (
        {"outcome": BatchState.FAILED} if change == "outcome" else {"suite_id": "suite-other"}
    )
    inputs["evaluation"] = replace(inputs["evaluation"], **changes)
    with pytest.raises(ValueError, match="evaluation"):
        BatchFinalizationBasis.build(**inputs)


def test_direct_basis_cannot_drop_required_run_non_run_unknown_or_cancel_refs() -> None:
    from qarunner.domain import BatchFinalizationBasis

    mixed = BatchFinalizationBasis.build(**_basis_inputs())
    with pytest.raises(ValueError, match="terminal_run_refs"):
        replace(mixed, terminal_run_refs=())
    with pytest.raises(ValueError, match="non_run_refs"):
        replace(mixed, non_run_refs=())
    with pytest.raises(ValueError, match="evaluation"):
        replace(
            mixed,
            evaluation=replace(mixed.evaluation, batch_cancellation_intent_digest=None),
        )

    unknown = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    with pytest.raises(ValueError, match="unknown_fact_refs"):
        replace(unknown, unknown_fact_refs=())


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("resolution_set", object(), "invalid"),
        ("policy", object(), "invalid"),
        ("evaluation", object(), "invalid"),
        ("run_resolution_sets", object(), "invalid"),
        ("run_resolution_sets", (object(),), "invalid"),
        ("run_bases", object(), "invalid"),
        ("run_bases", (object(),), "invalid"),
    ],
)
def test_basis_builder_rejects_untyped_core_inputs(field, value, reason) -> None:
    from qarunner.domain import BatchFinalizationBasis, DomainValidationError

    inputs = _basis_inputs()
    inputs[field] = value
    with pytest.raises(DomainValidationError) as caught:
        BatchFinalizationBasis.build(**inputs)
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_basis_builder_rejects_evaluation_counts_and_policy_digest_drift() -> None:
    from qarunner.domain import (
        BatchFinalizationBasis,
        BatchResolutionCounts,
        DomainValidationError,
    )

    inputs = _basis_inputs()
    drifted_counts = BatchResolutionCounts(2, 2, 0, 0, 0, 0, 0)
    with pytest.raises(DomainValidationError) as counts_caught:
        BatchFinalizationBasis.build(
            **{
                **inputs,
                "evaluation": replace(inputs["evaluation"], counts=drifted_counts),
            }
        )
    assert counts_caught.value.field == "evaluation"
    assert counts_caught.value.reason == "counts_mismatch"

    with pytest.raises(DomainValidationError) as policy_caught:
        BatchFinalizationBasis.build(
            **{
                **inputs,
                "evaluation": replace(inputs["evaluation"], policy_digest=_digest("other-policy")),
            }
        )
    assert policy_caught.value.field == "evaluation"
    assert policy_caught.value.reason == "policy_mismatch"


def test_basis_builder_rejects_run_bases_set_and_envelope_and_item_overlap() -> None:
    from qarunner.domain import BatchFinalizationBasis, DomainValidationError
    from tests.unit.application.test_finalize_run import bound_basis

    inputs = _basis_inputs()
    with pytest.raises(DomainValidationError) as set_caught:
        BatchFinalizationBasis.build(**{**inputs, "run_bases": ()})
    assert set_caught.value.field == "run_bases"
    assert set_caught.value.reason == "run_set_mismatch"

    with pytest.raises(DomainValidationError) as envelope_caught:
        BatchFinalizationBasis.build(
            **{
                **inputs,
                "run_resolution_sets": (
                    replace(inputs["run_resolution_sets"][0], batch_id="batch-forged"),
                ),
            }
        )
    assert envelope_caught.value.field == "run_resolution_sets"
    assert envelope_caught.value.reason == "envelope_mismatch"

    second_set = replace(inputs["run_resolution_sets"][0], run_id="run-2")
    second_basis = bound_basis(second_set)
    with pytest.raises(DomainValidationError) as overlap_caught:
        BatchFinalizationBasis.build(
            **{
                **inputs,
                "run_resolution_sets": inputs["run_resolution_sets"] + (second_set,),
                "run_bases": inputs["run_bases"] + (second_basis,),
                "resolution_set": replace(
                    inputs["resolution_set"],
                    canonical_run_set_digest=_canonical_run_set(
                        inputs["resolution_set"].batch_id, "run-1", "run-2"
                    ),
                ),
            }
        )
    assert overlap_caught.value.field == "run_resolution_sets"
    assert overlap_caught.value.reason == "item_overlap"


def test_basis_builder_rejects_run_item_resolution_mismatch() -> None:
    from qarunner.domain import BatchFinalizationBasis, DomainValidationError

    inputs = _basis_inputs()
    # Keep the RUN_RESOLUTION entry in the batch resolution_set, but drop every
    # run source that would re-derive it. Completeness requires those entries
    # to be reconstructible from the typed run bases.
    with pytest.raises(DomainValidationError) as caught:
        BatchFinalizationBasis.build(
            **{
                **inputs,
                "run_resolution_sets": (),
                "run_bases": (),
                "resolution_set": replace(
                    inputs["resolution_set"],
                    canonical_run_set_digest=_canonical_run_set(inputs["resolution_set"].batch_id),
                ),
            }
        )
    assert caught.value.field == "run_resolution_sets"
    assert caught.value.reason == "item_resolution_mismatch"


def test_basis_builder_rejects_unexpected_cancel_scope_without_intent() -> None:
    from qarunner.domain import BatchFinalizationBasis, DomainValidationError
    from tests.unit.domain.test_batch_cancellation_scope_item import _scope_item

    inputs = _unknown_basis_inputs()
    with pytest.raises(DomainValidationError) as caught:
        BatchFinalizationBasis.build(**{**inputs, "cancellation_scope_items": (_scope_item(),)})
    assert caught.value.field == "cancellation_scope_items"
    assert caught.value.reason == "unexpected"


def test_basis_builder_rejects_not_executed_and_run_fanout_scope_mismatches() -> None:
    from qarunner.domain import BatchFinalizationBasis, DomainValidationError
    from tests.unit.domain.test_batch_cancellation_scope_item import _scope_item

    inputs = _basis_inputs()
    not_executed_scope, run_scope = inputs["cancellation_scope_items"]

    # Same delivery key / item index as the real NOT_EXECUTED fact, but a different
    # recorded_at so the digest diverges — builder must refuse the stale entry.
    forged_not_executed = _scope_item(
        1,
        batch_id=not_executed_scope.batch_id,
        batch_cancellation_intent_digest=not_executed_scope.batch_cancellation_intent_digest,
        manifest_digest=not_executed_scope.manifest_digest,
        shard_plan_digest=not_executed_scope.shard_plan_digest,
        recorded_at=not_executed_scope.recorded_at.replace(minute=1),
    )
    with pytest.raises(DomainValidationError) as not_executed_caught:
        BatchFinalizationBasis.build(
            **{
                **inputs,
                "cancellation_scope_items": (forged_not_executed, run_scope),
            }
        )
    assert not_executed_caught.value.field == "cancellation_scope_items"
    assert not_executed_caught.value.reason == "not_executed_mismatch"

    forged_run_fanout = _scope_item(
        0,
        kind="RUN_FANOUT",
        batch_id=run_scope.batch_id,
        batch_cancellation_intent_digest=run_scope.batch_cancellation_intent_digest,
        manifest_digest=run_scope.manifest_digest,
        shard_plan_digest=run_scope.shard_plan_digest,
        run_id="run-other",
        source_run_version=run_scope.source_run_version,
        recorded_at=run_scope.recorded_at,
    )
    with pytest.raises(DomainValidationError) as fanout_caught:
        BatchFinalizationBasis.build(
            **{
                **inputs,
                "cancellation_scope_items": (not_executed_scope, forged_run_fanout),
            }
        )
    assert fanout_caught.value.field == "cancellation_scope_items"
    assert fanout_caught.value.reason == "run_fanout_mismatch"
