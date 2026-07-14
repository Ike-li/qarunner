"""T-M0-STATE-001F: Batch-owned pre-execution cancellation intent."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution-cancel.v1",
        payload={"label": label},
    )


def _scope(kind_name: str):
    from qarunner.domain import BatchCancellationScope, BatchCancellationScopeKind

    if kind_name == "PRE_PLAN":
        return BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=_digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        )
    return BatchCancellationScope(
        kind=BatchCancellationScopeKind.FROZEN_PLAN,
        preplan_scope_digest=None,
        manifest_digest=_digest("manifest"),
        shard_plan_version=2,
        shard_plan_digest=_digest("shard-plan-v2"),
        canonical_run_set_digest=_digest("canonical-run-set"),
    )


def _intent(*, batch_id: str, source_batch_version: int, scope_kind: str):
    from qarunner.domain import BatchCancellationIntent, CancellationSource

    return BatchCancellationIntent(
        batch_id=batch_id,
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=source_batch_version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
        authorization_digest=_digest("cancel-authorization"),
        scope=_scope(scope_kind),
        recorded_at=datetime(2026, 7, 14, 6, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("state_name", "scope_kind"),
    [
        pytest.param("DRAFT", "PRE_PLAN", id="draft"),
        pytest.param("VALIDATING", "PRE_PLAN", id="validating"),
        pytest.param("COLLECTING", "PRE_PLAN", id="collecting"),
        pytest.param("PLANNING", "PRE_PLAN", id="planning"),
        pytest.param("AWAITING_ADMISSION", "FROZEN_PLAN", id="awaiting-admission"),
        pytest.param("QUEUED", "FROZEN_PLAN", id="queued"),
    ],
)
def test_six_preterminal_phases_record_intent_without_claiming_an_outcome(
    state_name: str,
    scope_kind: str,
) -> None:
    """Accepting an intent does not imply that cancellation has converged."""
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState[state_name], version=3)
    intent = _intent(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind=scope_kind,
    )

    requested = source.request_cancel(intent=intent, expected_version=source.version)

    assert requested.state is source.state
    assert requested.version == source.version + 1
    assert requested.cancellation_intent is intent
    assert requested.rejection_fact is None
    assert requested.preexecution_closure_basis is None
    assert source.cancellation_intent is None
    assert source.version == 3


def test_exact_cancel_replay_returns_stored_intent_before_stale_cas() -> None:
    """A response-loss replay ignores a new server time and stale version."""
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = _intent(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind="PRE_PLAN",
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    replay_intent = replace(
        intent,
        recorded_at=intent.recorded_at + timedelta(minutes=1),
    )

    assert replay_intent.request_digest == intent.request_digest
    assert replay_intent.digest != intent.digest

    replay = requested.request_cancel(
        intent=replay_intent,
        expected_version=source.version,
    )

    assert replay is requested
    assert replay.cancellation_intent is intent
    assert replay.version == requested.version


def test_cancel_key_reuse_with_changed_request_conflicts_before_stale_cas() -> None:
    """One Batch cancel key cannot be rebound to different caller content."""
    from qarunner.domain import Batch, BatchState, IdempotencyConflict

    source = Batch(id="batch-001", state=BatchState.PLANNING, version=3)
    intent = _intent(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind="PRE_PLAN",
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    changed = replace(
        intent,
        reason="changed cancellation reason",
        recorded_at=intent.recorded_at + timedelta(minutes=1),
    )

    with pytest.raises(IdempotencyConflict) as caught:
        requested.request_cancel(
            intent=changed,
            expected_version=source.version,
        )

    assert caught.value.scope == "batch:batch-001:cancel"
    assert caught.value.key == "cancel-001"
    assert caught.value.stored_digest == intent.request_digest
    assert caught.value.received_digest == changed.request_digest
    assert requested.cancellation_intent is intent
    assert requested.version == 4


def test_second_cancel_key_cannot_replace_the_first_batch_intent() -> None:
    """A second command identity cannot overwrite the immutable Batch intent."""
    from qarunner.domain import (
        Batch,
        BatchCancellationConflict,
        BatchState,
    )

    source = Batch(id="batch-001", state=BatchState.QUEUED, version=3)
    intent = _intent(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind="FROZEN_PLAN",
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    replacement = replace(
        intent,
        idempotency_key="cancel-002",
        recorded_at=intent.recorded_at + timedelta(minutes=1),
    )

    with pytest.raises(BatchCancellationConflict) as caught:
        requested.request_cancel(
            intent=replacement,
            expected_version=source.version,
        )

    assert caught.value.batch_id == source.id
    assert caught.value.stored_key == "cancel-001"
    assert caught.value.received_key == "cancel-002"
    assert requested.cancellation_intent is intent
    assert requested.version == 4


@pytest.mark.parametrize(
    ("source_name", "target_name", "scope_kind"),
    [
        pytest.param("DRAFT", "VALIDATING", "PRE_PLAN", id="draft"),
        pytest.param("VALIDATING", "COLLECTING", "PRE_PLAN", id="validating"),
        pytest.param("COLLECTING", "PLANNING", "PRE_PLAN", id="collecting"),
        pytest.param("PLANNING", "AWAITING_ADMISSION", "PRE_PLAN", id="planning"),
        pytest.param(
            "AWAITING_ADMISSION",
            "QUEUED",
            "FROZEN_PLAN",
            id="awaiting-admission",
        ),
        pytest.param("QUEUED", "RUNNING", "FROZEN_PLAN", id="queued"),
    ],
)
def test_cancel_intent_blocks_generic_phase_advancement(
    source_name: str,
    target_name: str,
    scope_kind: str,
) -> None:
    """Once intent is durable, generic transitions cannot expand execution."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState[source_name], version=3)
    intent = _intent(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind=scope_kind,
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)

    with pytest.raises(InvalidTransition):
        requested.transition(
            BatchState[target_name],
            expected_version=requested.version,
        )

    assert requested.state is BatchState[source_name]
    assert requested.version == 4
    assert requested.cancellation_intent is intent


def test_cancel_request_rejects_non_preterminal_runtime_state() -> None:
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState.RUNNING, version=3)
    intent = _intent(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind="PRE_PLAN",
    )

    with pytest.raises(InvalidTransition):
        source.request_cancel(intent=intent, expected_version=source.version)


@pytest.mark.parametrize("drift", ["batch-id", "source-version"])
def test_new_cancel_request_must_bind_the_current_batch_source(drift: str) -> None:
    from qarunner.domain import Batch, BatchState, DomainValidationError

    source = Batch(id="batch-001", state=BatchState.DRAFT, version=3)
    intent = _intent(
        batch_id=("batch-other" if drift == "batch-id" else source.id),
        source_batch_version=(source.version - 1 if drift == "source-version" else source.version),
        scope_kind="PRE_PLAN",
    )

    with pytest.raises(DomainValidationError) as caught:
        source.request_cancel(intent=intent, expected_version=source.version)

    assert caught.value.field == "source_batch"
    assert caught.value.reason == "mismatch"


def test_cancel_scope_kind_must_match_the_current_phase() -> None:
    from qarunner.domain import Batch, BatchState, DomainValidationError

    source = Batch(id="batch-001", state=BatchState.DRAFT, version=3)
    intent = _intent(
        batch_id=source.id,
        source_batch_version=source.version,
        scope_kind="FROZEN_PLAN",
    )

    with pytest.raises(DomainValidationError) as caught:
        source.request_cancel(intent=intent, expected_version=source.version)

    assert caught.value.field == "scope"
    assert caught.value.reason == "source_phase_mismatch"
