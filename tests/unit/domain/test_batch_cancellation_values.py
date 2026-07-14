"""T-M0-STATE-001F: Batch cancellation intent and scope value contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-cancellation-value.v1",
        payload={"label": label},
    )


def _preplan_scope(**changes: object):
    from qarunner.domain import BatchCancellationScope, BatchCancellationScopeKind

    values: dict[str, object] = {
        "kind": BatchCancellationScopeKind.PRE_PLAN,
        "preplan_scope_digest": _digest("preplan"),
        "manifest_digest": None,
        "shard_plan_version": None,
        "shard_plan_digest": None,
        "canonical_run_set_digest": None,
    }
    values.update(changes)
    return BatchCancellationScope(**values)  # type: ignore[arg-type]


def _frozen_scope(**changes: object):
    from qarunner.domain import BatchCancellationScope, BatchCancellationScopeKind

    values: dict[str, object] = {
        "kind": BatchCancellationScopeKind.FROZEN_PLAN,
        "preplan_scope_digest": None,
        "manifest_digest": _digest("manifest"),
        "shard_plan_version": 2,
        "shard_plan_digest": _digest("plan"),
        "canonical_run_set_digest": _digest("run-set"),
    }
    values.update(changes)
    return BatchCancellationScope(**values)  # type: ignore[arg-type]


def _intent(**changes: object):
    from qarunner.domain import BatchCancellationIntent, CancellationSource

    values: dict[str, object] = {
        "batch_id": "batch-001",
        "project_id": "project-001",
        "suite_revision_id": "suite-001",
        "source_batch_version": 3,
        "idempotency_key": "cancel-001",
        "source": CancellationSource.USER_REQUEST,
        "actor_id": "user-001",
        "reason": "stop before execution",
        "authorization_digest": _digest("authorization"),
        "scope": _preplan_scope(),
        "recorded_at": datetime(2026, 7, 14, 6, tzinfo=UTC),
    }
    values.update(changes)
    return BatchCancellationIntent(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"kind": "pre_plan"}, "kind", "unknown", id="kind"),
        pytest.param(
            {"preplan_scope_digest": None},
            "preplan_scope_digest",
            "required_for_kind",
            id="preplan-required",
        ),
        pytest.param(
            {"manifest_digest": _digest("manifest")},
            "manifest_digest",
            "forbidden_for_kind",
            id="manifest-forbidden",
        ),
        pytest.param(
            {"shard_plan_version": 1},
            "shard_plan_version",
            "forbidden_for_kind",
            id="plan-version-forbidden",
        ),
        pytest.param(
            {"shard_plan_digest": _digest("plan")},
            "shard_plan_digest",
            "forbidden_for_kind",
            id="plan-forbidden",
        ),
        pytest.param(
            {"canonical_run_set_digest": _digest("run-set")},
            "canonical_run_set_digest",
            "forbidden_for_kind",
            id="run-set-forbidden",
        ),
    ],
)
def test_preplan_cancel_scope_rejects_invalid_field_combinations(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _preplan_scope(**changes)

    assert caught.value.entity_type == "batch_cancellation_scope"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param(
            {"preplan_scope_digest": _digest("preplan")},
            "preplan_scope_digest",
            "forbidden_for_kind",
            id="preplan-forbidden",
        ),
        pytest.param(
            {"manifest_digest": None},
            "manifest_digest",
            "required_for_kind",
            id="manifest-required",
        ),
        pytest.param(
            {"shard_plan_digest": None},
            "shard_plan_digest",
            "required_for_kind",
            id="plan-required",
        ),
        pytest.param(
            {"canonical_run_set_digest": None},
            "canonical_run_set_digest",
            "required_for_kind",
            id="run-set-required",
        ),
        pytest.param(
            {"shard_plan_version": True},
            "shard_plan_version",
            "not_integer",
            id="plan-version-bool",
        ),
        pytest.param(
            {"shard_plan_version": 2.0},
            "shard_plan_version",
            "not_integer",
            id="plan-version-float",
        ),
        pytest.param(
            {"shard_plan_version": -1},
            "shard_plan_version",
            "negative",
            id="plan-version-negative",
        ),
    ],
)
def test_frozen_cancel_scope_requires_complete_plan_identity(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _frozen_scope(**changes)

    assert caught.value.entity_type == "batch_cancellation_scope"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "field",
    ["batch_id", "project_id", "suite_revision_id", "idempotency_key", "actor_id", "reason"],
)
@pytest.mark.parametrize(
    ("value", "reason"),
    [pytest.param(7, "not_string", id="type"), pytest.param(" ", "empty", id="empty")],
)
def test_batch_cancel_intent_requires_nonempty_identity_strings(
    field: str, value: object, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _intent(**{field: value})

    assert caught.value.entity_type == "batch_cancellation_intent"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        pytest.param(True, "not_integer", id="bool"),
        pytest.param(3.0, "not_integer", id="float"),
        pytest.param(-1, "negative", id="negative"),
    ],
)
def test_batch_cancel_intent_rejects_invalid_source_version(value: object, reason: str) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _intent(source_batch_version=value)

    assert caught.value.field == "source_batch_version"
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"source": "user_request"}, "source", "unknown", id="source"),
        pytest.param(
            {"authorization_digest": "not-a-digest"},
            "authorization_digest",
            "not_digest",
            id="authorization",
        ),
        pytest.param({"scope": "pre_plan"}, "scope", "invalid_type", id="scope"),
        pytest.param(
            {"recorded_at": "2026-07-14T06:00:00Z"},
            "recorded_at",
            "not_datetime",
            id="recorded-type",
        ),
        pytest.param(
            {"recorded_at": datetime(2026, 7, 14, 6)},
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
                    7,
                    tzinfo=timezone(timedelta(hours=1)),
                )
            },
            "recorded_at",
            "not_utc",
            id="recorded-offset",
        ),
    ],
)
def test_batch_cancel_intent_rejects_invalid_typed_fields(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        _intent(**changes)

    assert caught.value.field == field
    assert caught.value.reason == reason


def test_request_digest_excludes_server_derived_scope_authority_and_time() -> None:
    original = _intent()
    changed = replace(
        original,
        project_id="project-002",
        suite_revision_id="suite-002",
        authorization_digest=_digest("new-authorization"),
        scope=_frozen_scope(),
        recorded_at=original.recorded_at + timedelta(minutes=1),
    )

    assert changed.request_digest == original.request_digest
    assert changed.digest != original.digest
