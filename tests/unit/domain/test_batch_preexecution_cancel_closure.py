"""T-M0-STATE-001F: zero-Run pre-plan Batch cancellation closure."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest


def _unsafe_batch_replace(batch, **changes):
    """Exercise command defenses after a corrupt store bypassed rehydration."""
    unsafe = object.__new__(type(batch))
    for field in batch.__dataclass_fields__:
        object.__setattr__(unsafe, field, changes.get(field, getattr(batch, field)))
    return unsafe


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution-cancel-closure.v1",
        payload={"label": label},
    )


def _preplan_intent(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import (
        BatchCancellationIntent,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        CancellationSource,
    )

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
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=_digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        recorded_at=datetime(2026, 7, 14, 6, tzinfo=UTC),
    )


def _preplan_snapshot(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import BatchPreexecutionScopeKind, BatchPreexecutionSnapshot

    return BatchPreexecutionSnapshot(
        batch_id=batch_id,
        source_batch_version=source_batch_version,
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
        scope_items=(),
        item_coverage_proof_digest=None,
    )


def _cancel_requested_batch(*, state_name: str = "COLLECTING"):
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState[state_name], version=3)
    intent = _preplan_intent(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=requested.id,
        source_batch_version=requested.version,
    )
    return source, intent, requested, snapshot


@pytest.mark.parametrize(
    "state_name",
    [
        pytest.param("DRAFT", id="draft"),
        pytest.param("VALIDATING", id="validating"),
        pytest.param("COLLECTING", id="collecting"),
        pytest.param("PLANNING", id="planning"),
    ],
)
def test_preplan_zero_run_snapshot_atomically_closes_cancelled_with_basis(
    state_name: str,
) -> None:
    """A durable intent becomes an outcome only with trusted absence facts."""
    from qarunner.domain import (
        BatchPreexecutionTerminalKind,
        BatchState,
    )

    source, intent, requested, snapshot = _cancel_requested_batch(state_name=state_name)

    cancelled = requested.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=requested.version,
    )

    assert cancelled.state is BatchState.CANCELLED
    assert cancelled.version == 5
    assert cancelled.cancellation_intent is intent
    assert cancelled.rejection_fact is None
    assert cancelled.preexecution_closure_basis is not None
    basis = cancelled.preexecution_closure_basis
    assert basis.source_batch_version == requested.version
    assert basis.source_phase is requested.state
    assert basis.terminal_kind is BatchPreexecutionTerminalKind.PRESTART_CANCEL
    assert basis.rejection_fact_digest is None
    assert basis.batch_cancellation_intent_digest == intent.digest
    assert basis.scope_kind is snapshot.scope_kind
    assert basis.submission_digest == snapshot.submission_digest
    assert basis.preplan_scope_digest == snapshot.preplan_scope_digest
    assert basis.materialized_run_absence_digest == snapshot.materialized_run_absence_digest
    assert basis.execution_absence_snapshot_digest == snapshot.execution_absence_snapshot_digest
    assert basis.batch_outcome is BatchState.CANCELLED
    assert source.state is BatchState[state_name]
    assert source.version == 3
    assert source.cancellation_intent is None
    assert requested.state is BatchState[state_name]
    assert requested.version == 4
    assert requested.preexecution_closure_basis is None


def test_exact_cancel_closure_replay_returns_terminal_before_stale_cas() -> None:
    """A response-loss replay returns the same terminal aggregate and basis."""
    _, _, requested, snapshot = _cancel_requested_batch()
    cancelled = requested.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=requested.version,
    )

    replay = cancelled.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=requested.version,
    )

    assert replay is cancelled
    assert replay.version == 5
    assert replay.preexecution_closure_basis is cancelled.preexecution_closure_basis


def test_same_cancel_intent_with_changed_snapshot_conflicts_before_stale_cas() -> None:
    """A replay cannot substitute different zero-execution evidence."""
    from qarunner.domain import IdempotencyConflict

    _, intent, requested, snapshot = _cancel_requested_batch()
    cancelled = requested.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=requested.version,
    )
    changed = replace(
        snapshot,
        execution_absence_snapshot_digest=_digest("different-no-execution"),
    )

    with pytest.raises(IdempotencyConflict) as caught:
        cancelled.finalize_unmaterialized_cancel(
            snapshot=changed,
            expected_version=requested.version,
        )

    assert caught.value.scope == "batch:batch-001:preexecution-cancel"
    assert caught.value.key == intent.idempotency_key
    assert cancelled.preexecution_closure_basis is not None
    changed_basis = replace(
        cancelled.preexecution_closure_basis,
        execution_absence_snapshot_digest=changed.execution_absence_snapshot_digest,
    )
    assert caught.value.stored_digest == cancelled.preexecution_closure_basis.digest
    assert caught.value.received_digest == changed_basis.digest
    assert cancelled.version == 5
    assert (
        cancelled.preexecution_closure_basis.execution_absence_snapshot_digest
        == snapshot.execution_absence_snapshot_digest
    )


def test_cancel_closure_requires_a_durable_batch_intent() -> None:
    """Absence facts alone are not cancellation authority."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=4)
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )

    with pytest.raises(InvalidTransition):
        source.finalize_unmaterialized_cancel(
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert source.state is BatchState.COLLECTING
    assert source.version == 4
    assert source.preexecution_closure_basis is None


@pytest.mark.parametrize(
    ("snapshot_batch_id", "snapshot_version"),
    [
        pytest.param("batch-foreign", 4, id="foreign-batch"),
        pytest.param("batch-001", 5, id="wrong-source-version"),
    ],
)
def test_cancel_closure_rejects_snapshot_for_the_wrong_batch_source(
    snapshot_batch_id: str,
    snapshot_version: int,
) -> None:
    """The closure proof must bind the authoritative post-intent version."""
    from qarunner.domain import DomainValidationError

    _, _, requested, _ = _cancel_requested_batch()
    snapshot = _preplan_snapshot(
        batch_id=snapshot_batch_id,
        source_batch_version=snapshot_version,
    )

    with pytest.raises(DomainValidationError) as caught:
        requested.finalize_unmaterialized_cancel(
            snapshot=snapshot,
            expected_version=requested.version,
        )

    assert caught.value.field == "source_batch"
    assert caught.value.reason == "mismatch"
    assert requested.version == 4
    assert requested.preexecution_closure_basis is None


@pytest.mark.parametrize(
    "state_name",
    [
        pytest.param("RUNNING", id="running"),
        pytest.param("FINALIZING", id="finalizing"),
        pytest.param("SUCCEEDED", id="succeeded"),
        pytest.param("FAILED", id="failed"),
        pytest.param("PARTIAL", id="partial"),
        pytest.param("CANCELLED", id="cancelled"),
    ],
)
def test_non_preterminal_phase_cannot_use_unmaterialized_cancel_closure(
    state_name: str,
) -> None:
    """This basis cannot overwrite execution or a previously chosen terminal."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    intent = _preplan_intent(batch_id="batch-001", source_batch_version=3)
    pending = Batch(
        id="batch-001",
        state=BatchState.COLLECTING,
        version=4,
        cancellation_intent=intent,
    )
    source = _unsafe_batch_replace(pending, state=BatchState[state_name])
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )

    with pytest.raises(InvalidTransition):
        source.finalize_unmaterialized_cancel(
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert source.state is BatchState[state_name]
    assert source.version == 4
    assert source.preexecution_closure_basis is None


@pytest.mark.parametrize("use_current_version", [False, True], ids=["stale", "current"])
def test_existing_rejection_terminal_cannot_be_overwritten_by_cancel_closure(
    use_current_version: bool,
) -> None:
    """The rejection winner remains immutable when cancellation lost the race."""
    from qarunner.domain import (
        Batch,
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
        InvalidTransition,
    )

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id=source.id,
        source_batch_version=source.version,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="manifest_request_invalid",
        input_digest=_digest("validation-input"),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 14, 6, 1, tzinfo=UTC),
    )
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    expected_version = rejected.version if use_current_version else source.version

    with pytest.raises(InvalidTransition):
        rejected.finalize_unmaterialized_cancel(
            snapshot=snapshot,
            expected_version=expected_version,
        )

    assert rejected.state is BatchState.REJECTED
    assert rejected.version == 4
    assert rejected.rejection_fact is rejection
    assert rejected.preexecution_closure_basis is not None
    assert rejected.preexecution_closure_basis.rejection_fact_digest == rejection.digest
