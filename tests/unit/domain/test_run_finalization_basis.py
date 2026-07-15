from dataclasses import replace

import pytest

from qarunner.domain import (
    AttemptExecutionFact,
    ContractRuleResultRef,
    Digest,
    DomainValidationError,
    DuplicateRiskAcceptanceResultRef,
    EffectiveItemResolution,
    EffectiveSourceKind,
    FinalizationDecisionKind,
    ItemAggregationClass,
    OriginalItemResolution,
    OriginalSourceKind,
    RetryDecisionResultRef,
    RunDisposition,
    RunFinalizationBasis,
    RunItemKey,
    RunItemResolution,
    RunItemResolutionSet,
    RunOutcome,
    TerminalInputKind,
    UnknownAdjudicationDecision,
    UnknownAdjudicationResultRef,
)


def d(value: str) -> Digest:
    return Digest("sha256:" + value * 64)


def basis(**changes: object) -> RunFinalizationBasis:
    outcome = changes.get("outcome", RunOutcome.PASSED)
    intent = changes.get("cancellation_intent_digest")
    stop = changes.get("cancellation_stop_digest")
    prestart = changes.get("prestart_closure_digest")
    rule = RunFinalizationBasis.decision_ref(
        sequence=1,
        decision_kind=FinalizationDecisionKind.CONTRACT_RULE,
        decision_schema="qep.run-terminal-rule.v1",
        decision_id="rule-1",
        decision_version=1,
        decision_digest=d("a"),
        decision_result=ContractRuleResultRef(
            outcome=outcome,  # type: ignore[arg-type]
            cancellation_intent_digest=intent,  # type: ignore[arg-type]
            cancellation_stop_digest=stop,  # type: ignore[arg-type]
            prestart_closure_digest=prestart,  # type: ignore[arg-type]
        ),
    )
    values: dict[str, object] = {
        "run_id": "run-1",
        "batch_id": "batch-1",
        "source_run_version": 0,
        "manifest_digest": d("1"),
        "shard_plan_digest": d("2"),
        "run_item_set_digest": d("3"),
        "execution_spec_digest": d("4"),
        "original_attempt_id": "attempt-1",
        "original_attempt_fence": 1,
        "final_attempt_id": "attempt-1",
        "final_attempt_no": 1,
        "final_attempt_fence": 1,
        "final_attempt_version": 0,
        "final_attempt_state": AttemptExecutionFact.PASSED,
        "worker_id": "worker-1",
        "worker_generation": 1,
        "attempt_chain_digest": d("5"),
        "evidence_root_digest": d("6"),
        "unknown_observation_digest": None,
        "adjudication_chain_digest": None,
        "retry_chain_digest": None,
        "item_resolution_set_digest": d("7"),
        "original_resolution_set_digest": d("8"),
        "effective_resolution_set_digest": d("9"),
        "item_count": 1,
        "cancellation_intent_digest": None,
        "cancellation_stop_digest": None,
        "prestart_closure_digest": None,
        "terminal_rule_schema": "qep.run-terminal-rule.v1",
        "terminal_rule_version": 1,
        "terminal_rule_digest": d("a"),
        "decision_chain": (rule,),
        "disposition": RunDisposition.CLOSED_NO_RETRY,
        "outcome": RunOutcome.PASSED,
        "terminal_input_kind": TerminalInputKind.VERIFIED_EVIDENCE,
    }
    values.update(changes)
    if (
        values["terminal_input_kind"] is TerminalInputKind.UNKNOWN_ADJUDICATION
        and "decision_chain" not in changes
    ):
        unknown = RunFinalizationBasis.decision_ref(
            sequence=2,
            decision_kind=FinalizationDecisionKind.UNKNOWN_ADJUDICATION,
            decision_schema="qep.unknown-adjudication.v1",
            decision_id="adj-1",
            decision_version=1,
            decision_digest=d("c"),
            decision_result=UnknownAdjudicationResultRef(
                adjudication_digest=d("c"),
                decision=UnknownAdjudicationDecision.MARK_INFRA_FAILED_NO_RETRY,
            ),
        )
        values["decision_chain"] = (rule, unknown)
    return RunFinalizationBasis(**values)  # type: ignore[arg-type]


def test_verified_evidence_basis_is_deterministic_and_explicit() -> None:
    value = basis()
    assert value.basis_digest == basis().basis_digest
    assert value.canonical_payload()["schema_version"] == "qep.run-finalization-basis.v1"
    assert value.canonical_payload()["unknown_observation_digest"] is None
    assert value.canonical_payload()["item_resolution_schema"] == "qep.run-item-resolution-set"
    assert value.canonical_payload()["item_resolution_version"] == 1


def test_prestart_cancel_forbids_attempt_authority() -> None:
    value = basis(
        original_attempt_id=None,
        original_attempt_fence=None,
        final_attempt_id=None,
        final_attempt_no=None,
        final_attempt_fence=None,
        final_attempt_version=None,
        final_attempt_state=None,
        worker_id=None,
        worker_generation=None,
        evidence_root_digest=None,
        attempt_chain_digest=None,
        cancellation_intent_digest=d("c"),
        prestart_closure_digest=d("d"),
        outcome=RunOutcome.CANCELLED,
        terminal_input_kind=TerminalInputKind.PRESTART_CANCEL,
    )
    assert value.final_attempt_id is None
    with pytest.raises(DomainValidationError):
        replace(value, final_attempt_id="attempt-1")


def test_verified_cancellation_requires_intent_stop_and_evidence() -> None:
    value = basis(
        final_attempt_state=AttemptExecutionFact.CANCELLED,
        cancellation_intent_digest=d("c"),
        cancellation_stop_digest=d("d"),
        outcome=RunOutcome.CANCELLED,
        terminal_input_kind=TerminalInputKind.VERIFIED_CANCELLATION_EVIDENCE,
    )
    required = ("cancellation_intent_digest", "cancellation_stop_digest", "evidence_root_digest")
    for field in required:
        with pytest.raises(DomainValidationError):
            replace(value, **{field: None})


def test_unknown_adjudication_requires_unknown_and_adjudication() -> None:
    value = basis(
        final_attempt_state=AttemptExecutionFact.ATTEMPT_UNKNOWN,
        evidence_root_digest=None,
        unknown_observation_digest=d("b"),
        adjudication_chain_digest=d("c"),
        outcome=RunOutcome.INFRA_FAILED,
        terminal_input_kind=TerminalInputKind.UNKNOWN_ADJUDICATION,
    )
    with pytest.raises(DomainValidationError):
        replace(value, unknown_observation_digest=None)
    with pytest.raises(DomainValidationError):
        replace(value, adjudication_chain_digest=None)


@pytest.mark.parametrize("field", ["source_run_version", "final_attempt_version"])
def test_versions_reject_bool(field: str) -> None:
    with pytest.raises(DomainValidationError):
        basis(**{field: True})


def test_decision_chain_must_be_contiguous_and_contract_rule_present() -> None:
    decision = RunFinalizationBasis.decision_ref(
        sequence=2,
        decision_kind=FinalizationDecisionKind.CONTRACT_RULE,
        decision_schema="qep.rule.v1",
        decision_id="rule-1",
        decision_version=1,
        decision_digest=d("e"),
        decision_result=ContractRuleResultRef(outcome=RunOutcome.PASSED),
    )
    with pytest.raises(DomainValidationError):
        basis(decision_chain=(decision,))
    with pytest.raises(DomainValidationError):
        replace(decision, sequence=1, decision_kind=FinalizationDecisionKind.SUITE_RETRY)
    valid_retry = replace(
        decision,
        sequence=1,
        decision_kind=FinalizationDecisionKind.SUITE_RETRY,
        decision_result=RetryDecisionResultRef(
            d("b"), d("e"), d("c"), "attempt-2", 2, 2, d("3")
        ),
    )
    with pytest.raises(DomainValidationError, match="decision_digest_mismatch"):
        replace(valid_retry, decision_digest=d("f"))
    with pytest.raises(DomainValidationError):
        basis(decision_chain=(object(),))
    retry_only = RunFinalizationBasis.decision_ref(
        sequence=1,
        decision_kind=FinalizationDecisionKind.SUITE_RETRY,
        decision_schema="qep.retry-decision.v1",
        decision_id="retry-1",
        decision_version=1,
        decision_digest=d("a"),
        decision_result=RetryDecisionResultRef(
            d("b"), d("a"), d("c"), "attempt-2", 2, 2, d("3")
        ),
    )
    with pytest.raises(DomainValidationError, match="contract_rule_required"):
        basis(decision_chain=(retry_only,))


def test_decision_source_attempt_is_all_or_none() -> None:
    rule = basis().decision_chain[0]
    with pytest.raises(DomainValidationError):
        replace(rule, source_attempt_id="attempt-1")
    sourced = replace(
        rule,
        source_attempt_id="attempt-1",
        source_attempt_no=1,
        source_attempt_fence=1,
        source_item_set_digest=d("3"),
    )
    assert sourced.canonical_payload()["source_attempt_no"] == 1


def test_non_prestart_requires_terminal_attempt() -> None:
    with pytest.raises(DomainValidationError):
        basis(final_attempt_state=None)


def test_unknown_retry_completion_uses_verified_evidence_and_retains_history() -> None:
    value = basis(
        final_attempt_id="attempt-2",
        final_attempt_no=2,
        final_attempt_fence=2,
        unknown_observation_digest=d("b"),
        adjudication_chain_digest=d("c"),
        retry_chain_digest=d("d"),
    )
    assert value.terminal_input_kind is TerminalInputKind.VERIFIED_EVIDENCE


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"disposition": RunDisposition.REVIEW_REQUIRED}, "disposition"),
        ({"prestart_closure_digest": d("e")}, "prestart_closure_digest"),
        (
            {
                "terminal_input_kind": TerminalInputKind.VERIFIED_CANCELLATION_EVIDENCE,
                "final_attempt_state": AttemptExecutionFact.PASSED,
                "outcome": RunOutcome.CANCELLED,
            },
            "final_attempt_state",
        ),
        (
            {
                "terminal_input_kind": TerminalInputKind.VERIFIED_CANCELLATION_EVIDENCE,
                "final_attempt_state": AttemptExecutionFact.CANCELLED,
                "cancellation_intent_digest": d("c"),
                "cancellation_stop_digest": d("d"),
            },
            "outcome",
        ),
        (
            {"terminal_input_kind": TerminalInputKind.UNKNOWN_ADJUDICATION},
            "final_attempt_state",
        ),
        ({"final_attempt_state": AttemptExecutionFact.ATTEMPT_UNKNOWN}, "final_attempt_state"),
    ],
)
def test_basis_rejects_cross_field_mismatch(changes: dict[str, object], field: str) -> None:
    with pytest.raises(DomainValidationError, match=field):
        basis(**changes)


def test_prestart_cancel_rejects_stop_and_non_cancelled_outcome() -> None:
    prestart = {
        "original_attempt_id": None,
        "original_attempt_fence": None,
        "final_attempt_id": None,
        "final_attempt_no": None,
        "final_attempt_fence": None,
        "final_attempt_version": None,
        "final_attempt_state": None,
        "worker_id": None,
        "worker_generation": None,
        "evidence_root_digest": None,
        "attempt_chain_digest": None,
        "cancellation_intent_digest": d("c"),
        "prestart_closure_digest": d("d"),
        "terminal_input_kind": TerminalInputKind.PRESTART_CANCEL,
    }
    with pytest.raises(DomainValidationError, match="cancellation_stop_digest"):
        basis(**prestart, cancellation_stop_digest=d("e"), outcome=RunOutcome.CANCELLED)
    with pytest.raises(DomainValidationError, match="outcome"):
        basis(**prestart, outcome=RunOutcome.PASSED)


def resolution_set() -> RunItemResolutionSet:
    original = OriginalItemResolution(
        OriginalSourceKind.ATTEMPT_RESULT,
        "attempt-1",
        1,
        1,
        d("3"),
        AttemptExecutionFact.PASSED,
        "qep.case-result.v1",
        1,
        d("b"),
        d("6"),
        None,
        None,
        None,
        None,
    )
    effective = EffectiveItemResolution(
        EffectiveSourceKind.ORIGINAL,
        "attempt-1",
        1,
        1,
        d("3"),
        RunOutcome.PASSED,
        "qep.case-result.v1",
        1,
        d("b"),
        d("6"),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    entry = RunItemResolution(
        RunItemKey("manifest-1", 0),
        original,
        effective,
        None,
        ItemAggregationClass.PASSED,
    )
    return RunItemResolutionSet.build(
        expected_item_keys=(entry.item_key,),
        entries=(entry,),
        batch_id="batch-1",
        run_id="run-1",
        source_run_version=0,
        manifest_digest=d("1"),
        shard_plan_digest=d("2"),
        run_item_set_digest=d("3"),
        attempt_chain_digest=d("5"),
        retry_chain_digest=None,
        adjudication_chain_digest=None,
    )


def test_typed_result_refs_validate_and_canonicalize_nulls() -> None:
    rule = ContractRuleResultRef(outcome=RunOutcome.PASSED)
    assert rule.canonical_payload() == {
        "outcome": "passed",
        "cancellation_intent_digest": None,
        "cancellation_stop_digest": None,
        "prestart_closure_digest": None,
    }
    retry = RetryDecisionResultRef(d("a"), d("b"), d("c"), "attempt-2", 2, 3, d("3"))
    assert retry.canonical_payload()["target_attempt_no"] == 2
    unknown = UnknownAdjudicationResultRef(
        d("c"), UnknownAdjudicationDecision.MARK_INFRA_FAILED_NO_RETRY
    )
    assert unknown.canonical_payload()["evidence_root_digest"] is None
    risk = DuplicateRiskAcceptanceResultRef(d("d"))
    assert risk.canonical_payload() == {"risk_acceptance_digest": d("d").value}
    with pytest.raises(DomainValidationError, match="target_attempt_id"):
        replace(retry, target_attempt_id="")
    with pytest.raises(DomainValidationError, match="decision"):
        replace(unknown, decision="raw")
    with pytest.raises(DomainValidationError, match="risk_acceptance_digest"):
        DuplicateRiskAcceptanceResultRef("raw")  # type: ignore[arg-type]


def test_builder_derives_resolution_fields_and_rejects_envelope_mismatch() -> None:
    resolved = resolution_set()
    values = basis().__dict__ if hasattr(basis(), "__dict__") else {
        field: getattr(basis(), field) for field in basis().__dataclass_fields__
    }
    for derived in (
        "run_id",
        "batch_id",
        "source_run_version",
        "manifest_digest",
        "shard_plan_digest",
        "run_item_set_digest",
        "attempt_chain_digest",
        "retry_chain_digest",
        "adjudication_chain_digest",
        "item_resolution_set_digest",
        "original_resolution_set_digest",
        "effective_resolution_set_digest",
        "item_count",
    ):
        values.pop(derived, None)
    built = RunFinalizationBasis.build(
        resolution_set=resolved,
        manifest_digest=d("1"),
        shard_plan_digest=d("2"),
        run_item_set_digest=d("3"),
        source_run_version=0,
        **values,
    )
    assert built.item_resolution_set_digest == resolved.resolution_set_digest
    assert built.item_count == resolved.item_count
    for field, value in (
        ("manifest_digest", d("e")),
        ("shard_plan_digest", d("e")),
        ("run_item_set_digest", d("e")),
        ("source_run_version", 9),
    ):
        arguments = {
            "manifest_digest": d("1"),
            "shard_plan_digest": d("2"),
            "run_item_set_digest": d("3"),
            "source_run_version": 0,
        }
        arguments[field] = value
        with pytest.raises(DomainValidationError, match="envelope_mismatch"):
            RunFinalizationBasis.build(resolution_set=resolved, **arguments, **values)
    with pytest.raises(DomainValidationError, match="resolution_set"):
        RunFinalizationBasis.build(
            resolution_set=object(),  # type: ignore[arg-type]
            manifest_digest=d("1"),
            shard_plan_digest=d("2"),
            run_item_set_digest=d("3"),
            source_run_version=0,
            **values,
        )


def test_builder_prestart_derives_null_attempt_chain() -> None:
    resolved = resolution_set()
    prestart = basis(
        original_attempt_id=None,
        original_attempt_fence=None,
        final_attempt_id=None,
        final_attempt_no=None,
        final_attempt_fence=None,
        final_attempt_version=None,
        final_attempt_state=None,
        worker_id=None,
        worker_generation=None,
        evidence_root_digest=None,
        attempt_chain_digest=None,
        cancellation_intent_digest=d("c"),
        prestart_closure_digest=d("d"),
        outcome=RunOutcome.CANCELLED,
        terminal_input_kind=TerminalInputKind.PRESTART_CANCEL,
    )
    values = {field: getattr(prestart, field) for field in prestart.__dataclass_fields__}
    for field in (
        "run_id", "batch_id", "source_run_version", "manifest_digest", "shard_plan_digest",
        "run_item_set_digest", "attempt_chain_digest", "retry_chain_digest",
        "adjudication_chain_digest", "item_resolution_set_digest",
        "original_resolution_set_digest", "effective_resolution_set_digest", "item_count",
    ):
        values.pop(field, None)
    built = RunFinalizationBasis.build(
        resolution_set=resolved, manifest_digest=d("1"), shard_plan_digest=d("2"),
        run_item_set_digest=d("3"), source_run_version=0, **values
    )
    assert built.attempt_chain_digest is None


def retry_ref(sequence: int, source_id: str, source_no: int, source_fence: int,
              target_id: str, target_no: int, target_fence: int) -> object:
    return RunFinalizationBasis.decision_ref(
        sequence=sequence,
        decision_kind=FinalizationDecisionKind.SUITE_RETRY,
        decision_schema="qep.retry-decision.v1",
        decision_id=f"decision-{sequence}",
        decision_version=1,
        decision_digest=d(chr(96 + sequence)),
        decision_result=RetryDecisionResultRef(
            d("a"), d(chr(96 + sequence)), d("b"), target_id, target_no, target_fence, d("3")
        ),
        source_attempt_id=source_id,
        source_attempt_no=source_no,
        source_attempt_fence=source_fence,
        source_item_set_digest=d("3"),
    )


def test_retry_chain_requires_source_target_continuity_and_basis_binding() -> None:
    rule = basis().decision_chain[0]
    first = retry_ref(2, "attempt-1", 1, 1, "attempt-2", 2, 2)
    second = retry_ref(3, "attempt-2", 2, 2, "attempt-3", 3, 3)
    valid = basis(
        final_attempt_id="attempt-3", final_attempt_no=3, final_attempt_fence=3,
        decision_chain=(rule, first, second), retry_chain_digest=d("d")
    )
    assert valid.final_attempt_id == "attempt-3"
    with pytest.raises(DomainValidationError, match="retry_target_discontinuous"):
        basis(decision_chain=(rule, replace(first, decision_result=replace(
            first.decision_result, target_attempt_no=3))), retry_chain_digest=d("d"))
    with pytest.raises(DomainValidationError, match="retry_source_discontinuous"):
        basis(
            final_attempt_id="attempt-3", final_attempt_no=3, final_attempt_fence=3,
            decision_chain=(rule, first, replace(second, source_attempt_id="other")),
            retry_chain_digest=d("d"),
        )
    with pytest.raises(DomainValidationError, match="retry_basis_mismatch"):
        basis(final_attempt_id="attempt-2", final_attempt_no=2, final_attempt_fence=2,
              decision_chain=(rule, replace(first, source_attempt_id="other")),
              retry_chain_digest=d("d"))


def test_unknown_completed_matrix_and_cancel_rule_mismatch() -> None:
    completed = UnknownAdjudicationResultRef(
        d("c"), UnknownAdjudicationDecision.MARK_COMPLETED_FROM_VERIFIED_EVIDENCE,
        evidence_root_digest=d("6")
    )
    unknown_ref = RunFinalizationBasis.decision_ref(
        sequence=2, decision_kind=FinalizationDecisionKind.UNKNOWN_ADJUDICATION,
        decision_schema="qep.unknown-adjudication.v1", decision_id="adj-1",
        decision_version=1, decision_digest=d("c"), decision_result=completed
    )
    value = basis(
        final_attempt_state=AttemptExecutionFact.ATTEMPT_UNKNOWN,
        unknown_observation_digest=d("b"), adjudication_chain_digest=d("c"),
        decision_chain=(basis().decision_chain[0], unknown_ref),
        terminal_input_kind=TerminalInputKind.UNKNOWN_ADJUDICATION,
    )
    assert value.evidence_root_digest == d("6")
    with pytest.raises(DomainValidationError, match="adjudication_mismatch"):
        replace(value, evidence_root_digest=d("e"))
    retry_unknown = replace(
        unknown_ref,
        decision_result=UnknownAdjudicationResultRef(
            d("c"), UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY, proof_digest=d("e")
        ),
    )
    with pytest.raises(DomainValidationError, match="retry_cannot_close_unknown"):
        replace(value, decision_chain=(basis().decision_chain[0], retry_unknown))
    with pytest.raises(DomainValidationError, match="unknown_adjudication_required"):
        replace(value, decision_chain=(basis().decision_chain[0],))
    infra = basis(
        final_attempt_state=AttemptExecutionFact.ATTEMPT_UNKNOWN,
        evidence_root_digest=None,
        unknown_observation_digest=d("b"),
        adjudication_chain_digest=d("c"),
        outcome=RunOutcome.INFRA_FAILED,
        terminal_input_kind=TerminalInputKind.UNKNOWN_ADJUDICATION,
    )
    with pytest.raises(DomainValidationError, match="infra_adjudication_matrix"):
        replace(infra, evidence_root_digest=d("6"))
    cancelled = basis(
        final_attempt_state=AttemptExecutionFact.CANCELLED,
        cancellation_intent_digest=d("c"), cancellation_stop_digest=d("d"),
        outcome=RunOutcome.CANCELLED,
        terminal_input_kind=TerminalInputKind.VERIFIED_CANCELLATION_EVIDENCE,
    )
    with pytest.raises(DomainValidationError, match="cancellation_rule_mismatch"):
        replace(cancelled, decision_chain=(basis().decision_chain[0],))


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        (AttemptExecutionFact.PASSED, RunOutcome.TEST_FAILED),
        (AttemptExecutionFact.TEST_FAILED, RunOutcome.PASSED),
        (AttemptExecutionFact.INFRA_FAILED, RunOutcome.CANCELLED),
    ],
)
def test_verified_evidence_rejects_outcome_mismatch(state, outcome) -> None:
    with pytest.raises(DomainValidationError, match="attempt_state_mismatch"):
        basis(final_attempt_state=state, outcome=outcome)


def test_retry_decision_requires_source_attempt_binding() -> None:
    rule = basis().decision_chain[0]
    unsourced = RunFinalizationBasis.decision_ref(
        sequence=2,
        decision_kind=FinalizationDecisionKind.PLATFORM_RETRY,
        decision_schema="qep.retry-decision.v1",
        decision_id="retry-1",
        decision_version=1,
        decision_digest=d("a"),
        decision_result=RetryDecisionResultRef(
            d("b"), d("a"), d("c"), "attempt-2", 2, 2, d("3")
        ),
    )
    with pytest.raises(DomainValidationError, match="retry_source_required"):
        basis(decision_chain=(rule, unsourced), retry_chain_digest=d("d"))
