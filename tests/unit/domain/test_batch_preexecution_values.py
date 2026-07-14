"""T-M0-STATE-001F: pre-execution fact and snapshot value contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution-value.v1",
        payload={"label": label},
    )


def _rejection(**changes: object):
    from qarunner.domain import (
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
    )

    values: dict[str, object] = {
        "rejection_id": "rejection-001",
        "batch_id": "batch-001",
        "source_batch_version": 3,
        "stage": BatchRejectionStage.VALIDATION,
        "reason_class": BatchRejectionReasonClass.INVALID_INPUT,
        "reason_code": "manifest_request_invalid",
        "input_digest": _digest("validation-input"),
        "authority_digest": None,
        "recorded_at": datetime(2026, 7, 14, 5, 0, tzinfo=UTC),
    }
    values.update(changes)
    return BatchRejection(**values)  # type: ignore[arg-type]


def _ordered_digests(*labels: str):
    return tuple(sorted((_digest(label) for label in labels), key=lambda digest: digest.value))


def _preplan_snapshot(**changes: object):
    from qarunner.domain import BatchPreexecutionScopeKind, BatchPreexecutionSnapshot

    values: dict[str, object] = {
        "batch_id": "batch-001",
        "source_batch_version": 3,
        "scope_kind": BatchPreexecutionScopeKind.PRE_PLAN,
        "submission_digest": _digest("submission"),
        "preplan_scope_digest": _digest("preplan-scope"),
        "manifest_digest": None,
        "shard_plan_version": None,
        "shard_plan_digest": None,
        "canonical_run_set_digest": None,
        "materialized_run_absence_digest": _digest("no-materialized-runs"),
        "execution_absence_snapshot_digest": _digest("no-execution"),
        "task_stop_fact_digests": (),
        "scope_items": (),
        "item_coverage_proof_digest": None,
    }
    values.update(changes)
    return BatchPreexecutionSnapshot(**values)  # type: ignore[arg-type]


def _planned_unmaterialized_snapshot(**changes: object):
    from qarunner.domain import (
        BatchPreexecutionScopeItem,
        BatchPreexecutionScopeKind,
        BatchPreexecutionSnapshot,
        BatchPreexecutionTerminalKind,
    )

    def scope_item(item_key: str):
        return BatchPreexecutionScopeItem(
            batch_id="batch-001",
            source_batch_version=3,
            terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
            rejection_fact_digest=_digest("rejection"),
            batch_cancellation_intent_digest=None,
            manifest_id="manifest-001",
            manifest_digest=_digest("manifest"),
            manifest_item_key=item_key,
            shard_plan_id="plan-001",
            shard_plan_version=1,
            shard_plan_digest=_digest("shard-plan"),
            materialized_run_absence_digest=_digest("no-materialized-runs"),
            resolution="not_started",
        )

    values: dict[str, object] = {
        "batch_id": "batch-001",
        "source_batch_version": 3,
        "scope_kind": BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED,
        "submission_digest": _digest("submission"),
        "preplan_scope_digest": None,
        "manifest_digest": _digest("manifest"),
        "shard_plan_version": 1,
        "shard_plan_digest": _digest("shard-plan"),
        "canonical_run_set_digest": _digest("empty-canonical-run-set"),
        "materialized_run_absence_digest": _digest("no-materialized-runs"),
        "execution_absence_snapshot_digest": _digest("no-execution"),
        "task_stop_fact_digests": (),
        "scope_items": (scope_item("case-001"), scope_item("case-002")),
        "item_coverage_proof_digest": _digest("item-coverage"),
    }
    values.update(changes)
    return BatchPreexecutionSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"rejection_id": 7}, "rejection_id", "not_string", id="id-type"),
        pytest.param({"rejection_id": " "}, "rejection_id", "empty", id="id-empty"),
        pytest.param({"batch_id": 7}, "batch_id", "not_string", id="batch-type"),
        pytest.param({"batch_id": " "}, "batch_id", "empty", id="batch-empty"),
        pytest.param({"reason_code": 7}, "reason_code", "not_string", id="code-type"),
        pytest.param({"reason_code": " "}, "reason_code", "empty", id="code-empty"),
        pytest.param(
            {"source_batch_version": True},
            "source_batch_version",
            "not_integer",
            id="version-bool",
        ),
        pytest.param(
            {"source_batch_version": 3.0},
            "source_batch_version",
            "not_integer",
            id="version-float",
        ),
        pytest.param(
            {"source_batch_version": -1},
            "source_batch_version",
            "negative",
            id="version-negative",
        ),
        pytest.param({"stage": "validation"}, "stage", "unknown", id="stage-enum"),
        pytest.param(
            {"reason_class": "invalid_input"},
            "reason_class",
            "unknown",
            id="reason-enum",
        ),
        pytest.param(
            {"input_digest": "sha256:not-a-value-object"},
            "input_digest",
            "not_digest",
            id="input-digest",
        ),
        pytest.param(
            {"authority_digest": "sha256:not-a-value-object"},
            "authority_digest",
            "not_digest",
            id="authority-digest",
        ),
        pytest.param(
            {"recorded_at": "2026-07-14T05:00:00Z"},
            "recorded_at",
            "not_datetime",
            id="recorded-type",
        ),
        pytest.param(
            {"recorded_at": datetime(2026, 7, 14, 5, 0)},
            "recorded_at",
            "not_utc",
            id="recorded-naive",
        ),
        pytest.param(
            {
                "recorded_at": datetime(
                    2026,
                    7,
                    14,
                    5,
                    0,
                    tzinfo=timezone(timedelta(hours=1)),
                )
            },
            "recorded_at",
            "not_utc",
            id="recorded-offset",
        ),
    ],
)
def test_batch_rejection_rejects_values_outside_the_v1_schema(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    """A rejection fact is valid before its digest or terminal command is used."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _rejection(**changes)

    assert caught.value.entity_type == "batch_rejection"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("stage_name", "reason_name"),
    [
        pytest.param("VALIDATION", "AUTHORIZATION_DENIED", id="authorization"),
        pytest.param("PLANNING", "POLICY_DENIED", id="policy"),
        pytest.param("ADMISSION", "CAPACITY_REJECTED", id="admission"),
    ],
)
def test_batch_rejection_requires_authority_for_authorized_or_admission_rejection(
    stage_name: str,
    reason_name: str,
) -> None:
    """Authorization, policy, and admission failures retain their authority fact."""
    from qarunner.domain import (
        BatchRejectionReasonClass,
        BatchRejectionStage,
        DomainValidationError,
    )

    with pytest.raises(DomainValidationError) as caught:
        _rejection(
            stage=BatchRejectionStage[stage_name],
            reason_class=BatchRejectionReasonClass[reason_name],
            authority_digest=None,
        )

    assert caught.value.entity_type == "batch_rejection"
    assert caught.value.field == "authority_digest"
    assert caught.value.reason == "required_for_rejection"


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param(
            {"submission_digest": None},
            "submission_digest",
            "not_digest",
            id="submission",
        ),
        pytest.param(
            {"preplan_scope_digest": None},
            "preplan_scope_digest",
            "required_for_scope",
            id="preplan-scope",
        ),
        pytest.param(
            {"preplan_scope_digest": "not-a-digest"},
            "preplan_scope_digest",
            "not_digest",
            id="preplan-scope-type",
        ),
        pytest.param(
            {"materialized_run_absence_digest": None},
            "materialized_run_absence_digest",
            "not_digest",
            id="run-absence",
        ),
        pytest.param(
            {"execution_absence_snapshot_digest": None},
            "execution_absence_snapshot_digest",
            "not_digest",
            id="execution-absence",
        ),
    ],
)
def test_preplan_snapshot_requires_scope_and_absence_proofs(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    """A pre-plan snapshot freezes submission scope and zero-execution proof."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _preplan_snapshot(**changes)

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("manifest_digest", "manifest", id="manifest"),
        pytest.param("shard_plan_version", 1, id="plan-version"),
        pytest.param("shard_plan_digest", "shard-plan", id="plan-digest"),
        pytest.param("canonical_run_set_digest", "run-set", id="run-set"),
        pytest.param("scope_items", "scope-item", id="scope-items"),
        pytest.param("item_coverage_proof_digest", "coverage", id="coverage"),
    ],
)
def test_preplan_snapshot_forbids_planned_scope_fields(field: str, value: object) -> None:
    """Facts that do not exist before Plan freeze must remain explicitly absent."""
    from qarunner.domain import DomainValidationError

    if field == "scope_items":
        changed = (_digest(str(value)),)
    else:
        changed = _digest(value) if isinstance(value, str) else value

    with pytest.raises(DomainValidationError) as caught:
        _preplan_snapshot(**{field: changed})

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == field
    assert caught.value.reason == "forbidden_for_scope"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        pytest.param({"manifest_digest": None}, "manifest_digest", id="manifest"),
        pytest.param({"shard_plan_version": None}, "shard_plan_version", id="plan-version"),
        pytest.param({"shard_plan_digest": None}, "shard_plan_digest", id="plan-digest"),
        pytest.param(
            {"canonical_run_set_digest": None},
            "canonical_run_set_digest",
            id="empty-run-set",
        ),
        pytest.param(
            {"scope_items": ()},
            "scope_items",
            id="scope-items",
        ),
        pytest.param(
            {"item_coverage_proof_digest": None},
            "item_coverage_proof_digest",
            id="coverage",
        ),
    ],
)
def test_planned_unmaterialized_snapshot_requires_plan_and_item_coverage(
    changes: dict[str, object],
    field: str,
) -> None:
    """A frozen zero-Run plan closes only with complete per-item coverage."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _planned_unmaterialized_snapshot(**changes)

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == field
    assert caught.value.reason == "required_for_scope"


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
def test_planned_unmaterialized_snapshot_rejects_forbidden_or_invalid_fields(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    """The planned scope cannot mix pre-plan identity or malformed Plan facts."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _planned_unmaterialized_snapshot(**changes)

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "field",
    ["task_stop_fact_digests"],
)
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        pytest.param([_digest("mutable")], "not_tuple", id="mutable"),
        pytest.param(("not-a-digest",), "not_digest", id="item-type"),
    ],
)
def test_snapshot_fact_digest_collections_require_digest_tuples(
    field: str,
    value: object,
    reason: str,
) -> None:
    """Proof references are immutable tuples containing only Digest values."""
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _preplan_snapshot(**{field: value})

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_snapshot_task_stop_fact_digests_reject_duplicates() -> None:
    """One immutable stop fact cannot appear twice in the proof array."""
    from qarunner.domain import DomainValidationError

    duplicate = _digest("duplicate-stop-fact")
    with pytest.raises(DomainValidationError) as caught:
        _preplan_snapshot(task_stop_fact_digests=(duplicate, duplicate))

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == "task_stop_fact_digests"
    assert caught.value.reason == "duplicate"


def test_snapshot_preserves_task_stop_fact_order_from_external_stable_keys() -> None:
    """Digest values cannot recover the authoritative task-key ordering."""
    reverse_digest_order = tuple(
        sorted(
            (_digest("task-stop-001"), _digest("task-stop-002")),
            key=lambda digest: digest.value,
            reverse=True,
        )
    )
    task_refs_by_stable_key = (
        ("task-a", reverse_digest_order[0]),
        ("task-b", reverse_digest_order[1]),
    )
    task_stable_key_order = tuple(digest for _, digest in task_refs_by_stable_key)
    assert task_stable_key_order[0].value > task_stable_key_order[1].value

    snapshot = _preplan_snapshot(task_stop_fact_digests=task_stable_key_order)

    assert snapshot.task_stop_fact_digests == task_stable_key_order


def test_snapshot_value_helpers_construct_valid_contract_examples() -> None:
    """The RED cases mutate valid examples rather than invalid shared fixtures."""
    from qarunner.domain import BatchPreexecutionScopeKind

    preplan = _preplan_snapshot()
    planned = _planned_unmaterialized_snapshot()

    assert preplan.scope_kind is BatchPreexecutionScopeKind.PRE_PLAN
    assert planned.scope_kind is BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
    assert replace(preplan) == preplan
    assert replace(planned) == planned


def test_snapshot_rejects_raw_scope_kind() -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _preplan_snapshot(scope_kind="pre_plan")

    assert caught.value.field == "scope_kind"
    assert caught.value.reason == "unknown"
