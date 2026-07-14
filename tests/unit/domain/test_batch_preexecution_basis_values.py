"""T-M0-STATE-001F: pre-execution closure-basis value contracts."""

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution-basis-value.v1",
        payload={"label": label},
    )


def _ordered_digests(*labels: str):
    return tuple(sorted((_digest(label) for label in labels), key=lambda digest: digest.value))


def _basis(
    *,
    terminal_name: str = "REJECTION",
    scope_name: str = "PRE_PLAN",
    **changes: object,
):
    from qarunner.domain import (
        BatchPreexecutionClosureBasis,
        BatchPreexecutionScopeKind,
        BatchPreexecutionTerminalKind,
        BatchState,
    )

    rejection = terminal_name == "REJECTION"
    preplan = scope_name == "PRE_PLAN"
    values: dict[str, object] = {
        "batch_id": "batch-001",
        "source_batch_version": 3,
        "source_phase": (BatchState.PLANNING if preplan else BatchState.AWAITING_ADMISSION),
        "terminal_kind": BatchPreexecutionTerminalKind[terminal_name],
        "rejection_fact_digest": _digest("rejection-001") if rejection else None,
        "batch_cancellation_intent_digest": (
            None if rejection else _digest("cancellation-intent-001")
        ),
        "scope_kind": BatchPreexecutionScopeKind[scope_name],
        "submission_digest": _digest("submission-001"),
        "preplan_scope_digest": _digest("preplan-scope-001") if preplan else None,
        "manifest_digest": None if preplan else _digest("manifest-001"),
        "shard_plan_version": None if preplan else 2,
        "shard_plan_digest": None if preplan else _digest("shard-plan-001-v2"),
        "canonical_run_set_digest": (None if preplan else _digest("canonical-empty-run-set")),
        "materialized_run_absence_digest": _digest("no-materialized-runs"),
        "execution_absence_snapshot_digest": _digest("no-execution"),
        "task_stop_fact_digests": _ordered_digests("task-stop-001", "task-stop-002"),
        "preexecution_scope_item_fact_digests": (
            () if preplan else (_digest("scope-item-case-001"), _digest("scope-item-case-002"))
        ),
        "item_coverage_proof_digest": None if preplan else _digest("item-coverage"),
        "batch_outcome": BatchState.REJECTED if rejection else BatchState.CANCELLED,
    }
    values.update(changes)
    return BatchPreexecutionClosureBasis(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("terminal_name", "scope_name"),
    [
        pytest.param("REJECTION", "PRE_PLAN", id="rejection-pre-plan"),
        pytest.param(
            "REJECTION",
            "PLANNED_UNMATERIALIZED",
            id="rejection-planned-unmaterialized",
        ),
        pytest.param("PRESTART_CANCEL", "PRE_PLAN", id="cancel-pre-plan"),
        pytest.param(
            "PRESTART_CANCEL",
            "PLANNED_UNMATERIALIZED",
            id="cancel-planned-unmaterialized",
        ),
    ],
)
def test_closure_basis_digest_binds_every_signed_v1_field(
    terminal_name: str,
    scope_name: str,
) -> None:
    """Both terminal and scope families use one complete canonical payload."""
    from qarunner.domain import canonical_digest

    basis = _basis(terminal_name=terminal_name, scope_name=scope_name)

    assert basis.digest == canonical_digest(
        schema_version="qep.batch-preexecution-closure-basis.v1",
        payload={
            "batch_id": basis.batch_id,
            "source_batch_version": basis.source_batch_version,
            "source_phase": basis.source_phase.value,
            "terminal_kind": basis.terminal_kind.value,
            "rejection_fact_digest": (
                None if basis.rejection_fact_digest is None else basis.rejection_fact_digest.value
            ),
            "batch_cancellation_intent_digest": (
                None
                if basis.batch_cancellation_intent_digest is None
                else basis.batch_cancellation_intent_digest.value
            ),
            "scope_kind": basis.scope_kind.value,
            "submission_digest": basis.submission_digest.value,
            "preplan_scope_digest": (
                None if basis.preplan_scope_digest is None else basis.preplan_scope_digest.value
            ),
            "manifest_digest": (
                None if basis.manifest_digest is None else basis.manifest_digest.value
            ),
            "shard_plan_version": basis.shard_plan_version,
            "shard_plan_digest": (
                None if basis.shard_plan_digest is None else basis.shard_plan_digest.value
            ),
            "canonical_run_set_digest": (
                None
                if basis.canonical_run_set_digest is None
                else basis.canonical_run_set_digest.value
            ),
            "materialized_run_absence_digest": basis.materialized_run_absence_digest.value,
            "execution_absence_snapshot_digest": (basis.execution_absence_snapshot_digest.value),
            "task_stop_fact_digest": [digest.value for digest in basis.task_stop_fact_digests],
            "preexecution_scope_item_fact_digest": [
                digest.value for digest in basis.preexecution_scope_item_fact_digests
            ],
            "item_coverage_proof_digest": (
                None
                if basis.item_coverage_proof_digest is None
                else basis.item_coverage_proof_digest.value
            ),
            "batch_outcome": basis.batch_outcome.value,
        },
    )


@pytest.mark.parametrize(
    ("terminal_name", "changes", "field", "reason"),
    [
        pytest.param(
            "REJECTION",
            {"rejection_fact_digest": None},
            "rejection_fact_digest",
            "required_for_terminal",
            id="rejection-missing",
        ),
        pytest.param(
            "REJECTION",
            {"batch_cancellation_intent_digest": _digest("unexpected-cancel")},
            "batch_cancellation_intent_digest",
            "forbidden_for_terminal",
            id="rejection-with-cancel",
        ),
        pytest.param(
            "REJECTION",
            {"rejection_fact_digest": "not-a-digest"},
            "rejection_fact_digest",
            "not_digest",
            id="rejection-command-type",
        ),
        pytest.param(
            "PRESTART_CANCEL",
            {"batch_cancellation_intent_digest": None},
            "batch_cancellation_intent_digest",
            "required_for_terminal",
            id="cancel-missing",
        ),
        pytest.param(
            "PRESTART_CANCEL",
            {"rejection_fact_digest": _digest("unexpected-rejection")},
            "rejection_fact_digest",
            "forbidden_for_terminal",
            id="cancel-with-rejection",
        ),
        pytest.param(
            "PRESTART_CANCEL",
            {"batch_cancellation_intent_digest": "not-a-digest"},
            "batch_cancellation_intent_digest",
            "not_digest",
            id="cancel-command-type",
        ),
    ],
)
def test_terminal_kind_requires_exactly_its_matching_command_fact(
    terminal_name: str,
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    """Rejection and cancellation identities are mutually exclusive."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(terminal_name=terminal_name, **changes)

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("terminal_name", "state_names"),
    [
        pytest.param(
            "REJECTION",
            ("VALIDATING", "COLLECTING", "PLANNING", "AWAITING_ADMISSION"),
            id="rejection",
        ),
        pytest.param(
            "PRESTART_CANCEL",
            (
                "DRAFT",
                "VALIDATING",
                "COLLECTING",
                "PLANNING",
                "AWAITING_ADMISSION",
                "QUEUED",
            ),
            id="prestart-cancel",
        ),
    ],
)
def test_terminal_kind_accepts_exactly_its_preexecution_source_phases(
    terminal_name: str,
    state_names: tuple[str, ...],
) -> None:
    """Every contract-authorized source phase can form the matching terminal basis."""
    from qarunner.domain import BatchState

    for state_name in state_names:
        scope_name = (
            "PLANNED_UNMATERIALIZED"
            if state_name in {"AWAITING_ADMISSION", "QUEUED"}
            else "PRE_PLAN"
        )
        basis = _basis(
            terminal_name=terminal_name,
            scope_name=scope_name,
            source_phase=BatchState[state_name],
        )

        assert basis.source_phase is BatchState[state_name]


@pytest.mark.parametrize(
    ("terminal_name", "state_name"),
    [
        pytest.param("REJECTION", "VALIDATING", id="rejection-validating"),
        pytest.param("REJECTION", "COLLECTING", id="rejection-collecting"),
        pytest.param("REJECTION", "PLANNING", id="rejection-planning"),
        pytest.param("PRESTART_CANCEL", "DRAFT", id="cancel-draft"),
        pytest.param("PRESTART_CANCEL", "VALIDATING", id="cancel-validating"),
        pytest.param("PRESTART_CANCEL", "COLLECTING", id="cancel-collecting"),
        pytest.param("PRESTART_CANCEL", "PLANNING", id="cancel-planning"),
    ],
)
def test_planned_unmaterialized_scope_is_rejected_before_plan_freeze_phase(
    terminal_name: str,
    state_name: str,
) -> None:
    """Only post-plan source phases can carry a planned-unmaterialized scope."""
    from qarunner.domain import BatchState, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(
            terminal_name=terminal_name,
            scope_name="PLANNED_UNMATERIALIZED",
            source_phase=BatchState[state_name],
        )

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == "scope_kind"
    assert caught.value.reason == "invalid_for_source_phase"


@pytest.mark.parametrize(
    ("terminal_name", "state_name"),
    [
        pytest.param("REJECTION", "DRAFT", id="rejection-draft"),
        pytest.param("REJECTION", "QUEUED", id="rejection-queued"),
        pytest.param("REJECTION", "RUNNING", id="rejection-running"),
        pytest.param("REJECTION", "REJECTED", id="rejection-terminal"),
        pytest.param("PRESTART_CANCEL", "RUNNING", id="cancel-running"),
        pytest.param("PRESTART_CANCEL", "FINALIZING", id="cancel-finalizing"),
        pytest.param("PRESTART_CANCEL", "CANCELLED", id="cancel-terminal"),
    ],
)
def test_terminal_kind_rejects_source_phases_outside_its_allowlist(
    terminal_name: str,
    state_name: str,
) -> None:
    from qarunner.domain import BatchState, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(terminal_name=terminal_name, source_phase=BatchState[state_name])

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == "source_phase"
    assert caught.value.reason == "invalid_for_terminal"


@pytest.mark.parametrize(
    ("terminal_name", "valid_outcome", "invalid_outcomes"),
    [
        pytest.param(
            "REJECTION",
            "REJECTED",
            ("CANCELLED", "FAILED", "PARTIAL", "SUCCEEDED"),
            id="rejection",
        ),
        pytest.param(
            "PRESTART_CANCEL",
            "CANCELLED",
            ("REJECTED", "FAILED", "PARTIAL", "SUCCEEDED"),
            id="prestart-cancel",
        ),
    ],
)
def test_terminal_kind_accepts_only_its_contract_outcome(
    terminal_name: str,
    valid_outcome: str,
    invalid_outcomes: tuple[str, ...],
) -> None:
    from qarunner.domain import BatchState, DomainValidationError

    assert (
        _basis(
            terminal_name=terminal_name,
            batch_outcome=BatchState[valid_outcome],
        ).batch_outcome
        is BatchState[valid_outcome]
    )

    for invalid_outcome in invalid_outcomes:
        with pytest.raises(DomainValidationError) as caught:
            _basis(
                terminal_name=terminal_name,
                batch_outcome=BatchState[invalid_outcome],
            )

        assert caught.value.field == "batch_outcome"
        assert caught.value.reason == "invalid_for_terminal"


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param(
            {"preplan_scope_digest": None},
            "preplan_scope_digest",
            "required_for_scope",
            id="missing-preplan-scope",
        ),
        pytest.param(
            {"preplan_scope_digest": "not-a-digest"},
            "preplan_scope_digest",
            "not_digest",
            id="preplan-scope-type",
        ),
        pytest.param(
            {"manifest_digest": _digest("unexpected-manifest")},
            "manifest_digest",
            "forbidden_for_scope",
            id="manifest",
        ),
        pytest.param(
            {"shard_plan_version": 0},
            "shard_plan_version",
            "forbidden_for_scope",
            id="plan-version",
        ),
        pytest.param(
            {"shard_plan_digest": _digest("unexpected-plan")},
            "shard_plan_digest",
            "forbidden_for_scope",
            id="plan-digest",
        ),
        pytest.param(
            {"canonical_run_set_digest": _digest("unexpected-run-set")},
            "canonical_run_set_digest",
            "forbidden_for_scope",
            id="run-set",
        ),
        pytest.param(
            {"preexecution_scope_item_fact_digests": (_digest("unexpected-item"),)},
            "preexecution_scope_item_fact_digests",
            "forbidden_for_scope",
            id="scope-items",
        ),
        pytest.param(
            {"item_coverage_proof_digest": _digest("unexpected-coverage")},
            "item_coverage_proof_digest",
            "forbidden_for_scope",
            id="item-coverage",
        ),
    ],
)
def test_preplan_basis_requires_only_preplan_scope_fields(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    """Manifest, Plan, Run-set, and item facts remain absent before Plan freeze."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(scope_name="PRE_PLAN", **changes)

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param(
            {"preplan_scope_digest": _digest("unexpected-preplan")},
            "preplan_scope_digest",
            "forbidden_for_scope",
            id="preplan-scope",
        ),
        pytest.param(
            {"manifest_digest": None},
            "manifest_digest",
            "required_for_scope",
            id="manifest",
        ),
        pytest.param(
            {"shard_plan_version": None},
            "shard_plan_version",
            "required_for_scope",
            id="plan-version",
        ),
        pytest.param(
            {"shard_plan_digest": None},
            "shard_plan_digest",
            "required_for_scope",
            id="plan-digest",
        ),
        pytest.param(
            {"canonical_run_set_digest": None},
            "canonical_run_set_digest",
            "required_for_scope",
            id="run-set",
        ),
        pytest.param(
            {"preexecution_scope_item_fact_digests": ()},
            "preexecution_scope_item_fact_digests",
            "required_for_scope",
            id="scope-items",
        ),
        pytest.param(
            {"item_coverage_proof_digest": None},
            "item_coverage_proof_digest",
            "required_for_scope",
            id="item-coverage",
        ),
    ],
)
def test_planned_unmaterialized_basis_requires_frozen_plan_and_item_coverage(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    """A frozen zero-Run Plan cannot close with a partial scope identity."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(scope_name="PLANNED_UNMATERIALIZED", **changes)

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        pytest.param(
            "task_stop_fact_digests",
            [_digest("mutable-task-stop")],
            "not_tuple",
            id="task-stop-not-tuple",
        ),
        pytest.param(
            "task_stop_fact_digests",
            ("not-a-digest",),
            "not_digest",
            id="task-stop-member-type",
        ),
        pytest.param(
            "preexecution_scope_item_fact_digests",
            [_digest("mutable-scope-item")],
            "not_tuple",
            id="scope-item-not-tuple",
        ),
        pytest.param(
            "preexecution_scope_item_fact_digests",
            ("not-a-digest",),
            "not_digest",
            id="scope-item-member-type",
        ),
    ],
)
def test_basis_fact_references_require_immutable_digest_tuples(
    field: str,
    value: object,
    reason: str,
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(scope_name="PLANNED_UNMATERIALIZED", **{field: value})

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "field",
    ["task_stop_fact_digests", "preexecution_scope_item_fact_digests"],
)
def test_basis_fact_references_reject_duplicate_digests(field: str) -> None:
    from qarunner.domain import DomainValidationError

    duplicate = _digest(f"duplicate-{field}")
    with pytest.raises(DomainValidationError) as caught:
        _basis(scope_name="PLANNED_UNMATERIALIZED", **{field: (duplicate, duplicate)})

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == field
    assert caught.value.reason == "duplicate"


def test_task_stop_fact_digest_order_is_preserved_from_external_task_stable_keys() -> None:
    """Stop refs retain authoritative task-key order, which a digest cannot recover."""
    task_stable_key_order = tuple(
        sorted(
            (_digest("task-stop-001"), _digest("task-stop-002")),
            key=lambda digest: digest.value,
            reverse=True,
        )
    )
    assert task_stable_key_order[0].value > task_stable_key_order[1].value

    basis = _basis(task_stop_fact_digests=task_stable_key_order)

    assert basis.task_stop_fact_digests == task_stable_key_order


def test_scope_item_fact_digest_order_is_preserved_without_digest_sorting() -> None:
    """Item refs are already ordered by item key, which cannot be recovered from a digest."""
    digest_order = _ordered_digests("scope-item-a", "scope-item-b")
    item_key_order = tuple(reversed(digest_order))

    basis = _basis(
        scope_name="PLANNED_UNMATERIALIZED",
        preexecution_scope_item_fact_digests=item_key_order,
    )

    assert basis.preexecution_scope_item_fact_digests == item_key_order


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"batch_id": 7}, "batch_id", "not_string", id="batch-id-type"),
        pytest.param({"batch_id": " "}, "batch_id", "empty", id="batch-id-empty"),
        pytest.param(
            {"source_batch_version": True},
            "source_batch_version",
            "not_integer",
            id="source-version-bool",
        ),
        pytest.param(
            {"source_batch_version": -1},
            "source_batch_version",
            "negative",
            id="source-version-negative",
        ),
        pytest.param({"source_phase": "planning"}, "source_phase", "unknown", id="phase"),
        pytest.param(
            {"terminal_kind": "rejection"},
            "terminal_kind",
            "unknown",
            id="terminal-kind",
        ),
        pytest.param({"scope_kind": "pre_plan"}, "scope_kind", "unknown", id="scope-kind"),
        pytest.param(
            {"batch_outcome": "rejected"},
            "batch_outcome",
            "unknown",
            id="batch-outcome",
        ),
        pytest.param(
            {"submission_digest": "not-a-digest"},
            "submission_digest",
            "not_digest",
            id="submission",
        ),
        pytest.param(
            {"materialized_run_absence_digest": "not-a-digest"},
            "materialized_run_absence_digest",
            "not_digest",
            id="run-absence",
        ),
        pytest.param(
            {"execution_absence_snapshot_digest": "not-a-digest"},
            "execution_absence_snapshot_digest",
            "not_digest",
            id="execution-absence",
        ),
    ],
)
def test_closure_basis_rejects_values_outside_the_v1_envelope(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(**changes)

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param(
            {"manifest_digest": "not-a-digest"},
            "manifest_digest",
            "not_digest",
            id="manifest-type",
        ),
        pytest.param(
            {"shard_plan_version": True},
            "shard_plan_version",
            "not_integer",
            id="plan-version-bool",
        ),
        pytest.param(
            {"shard_plan_version": -1},
            "shard_plan_version",
            "negative",
            id="plan-version-negative",
        ),
        pytest.param(
            {"shard_plan_digest": "not-a-digest"},
            "shard_plan_digest",
            "not_digest",
            id="plan-digest-type",
        ),
        pytest.param(
            {"canonical_run_set_digest": "not-a-digest"},
            "canonical_run_set_digest",
            "not_digest",
            id="run-set-type",
        ),
        pytest.param(
            {"item_coverage_proof_digest": "not-a-digest"},
            "item_coverage_proof_digest",
            "not_digest",
            id="coverage-type",
        ),
    ],
)
def test_planned_unmaterialized_basis_rejects_malformed_plan_values(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _basis(scope_name="PLANNED_UNMATERIALIZED", **changes)

    assert caught.value.entity_type == "batch_preexecution_closure_basis"
    assert caught.value.field == field
    assert caught.value.reason == reason
