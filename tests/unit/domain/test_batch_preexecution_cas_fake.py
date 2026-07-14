"""T-M0-STATE-001F: deterministic Batch CAS race publication."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tests.fakes.versioned_batch_store import InMemoryVersionedBatchStore


def _unsafe_batch_replace(batch, **changes):
    """Exercise Fake defenses after a corrupt store bypassed rehydration."""
    unsafe = object.__new__(type(batch))
    for field in batch.__dataclass_fields__:
        object.__setattr__(unsafe, field, changes.get(field, getattr(batch, field)))
    return unsafe


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-cas-fake.v1",
        payload={"label": label},
    )


def _rejection(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import (
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
    )

    return BatchRejection(
        rejection_id="rejection-001",
        batch_id=batch_id,
        source_batch_version=source_batch_version,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="manifest_request_invalid",
        input_digest=_digest("validation-input"),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
    )


def _snapshot(*, batch_id: str, source_batch_version: int):
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


def _cancel_intent(*, batch_id: str, source_batch_version: int):
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
        recorded_at=datetime(2026, 7, 14, 8, 1, tzinfo=UTC),
    )


def _competing_candidates():
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _snapshot(batch_id=source.id, source_batch_version=source.version)
    intent = _cancel_intent(batch_id=source.id, source_batch_version=source.version)
    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    cancel_requested = source.request_cancel(
        intent=intent,
        expected_version=source.version,
    )
    return source, rejection, snapshot, intent, rejected, cancel_requested


@pytest.mark.parametrize("winner_name", ["rejection", "cancel"])
def test_competing_v4_candidates_publish_exactly_one_winner(
    winner_name: str,
) -> None:
    """Two candidates from v3 cannot both replace the same committed snapshot."""
    from qarunner.domain import BatchState, InvalidTransition, VersionConflict

    source, rejection, snapshot, intent, rejected, cancel_requested = _competing_candidates()
    candidates = {"rejection": rejected, "cancel": cancel_requested}
    winner = candidates[winner_name]
    loser = candidates["cancel" if winner_name == "rejection" else "rejection"]
    store = InMemoryVersionedBatchStore(source)

    committed = store.commit(
        source=source,
        candidate=winner,
        expected_version=source.version,
    )

    with pytest.raises(VersionConflict) as stale:
        store.commit(
            source=source,
            candidate=loser,
            expected_version=source.version,
        )

    assert committed is winner
    assert store.current is winner
    assert store.current is not loser
    assert stale.value.current_version == 4
    assert stale.value.expected_version == 3
    assert source.state is BatchState.VALIDATING
    assert source.version == 3
    assert source.rejection_fact is None
    assert source.cancellation_intent is None

    if winner_name == "rejection":
        with pytest.raises(InvalidTransition) as reread:
            store.current.request_cancel(
                intent=intent,
                expected_version=store.current.version,
            )
        assert reread.value.current_state is BatchState.REJECTED
        assert reread.value.requested_state is BatchState.CANCELLED
        assert store.current.rejection_fact is rejection
        assert store.current.cancellation_intent is None
    else:
        with pytest.raises(InvalidTransition) as reread:
            store.current.reject_preexecution(
                rejection=rejection,
                snapshot=snapshot,
                expected_version=store.current.version,
            )
        assert reread.value.current_state is BatchState.VALIDATING
        assert reread.value.requested_state is BatchState.REJECTED
        assert store.current.cancellation_intent is intent
        assert store.current.rejection_fact is None
    assert store.current is winner
    assert store.current.version == 4


@pytest.mark.parametrize(
    "invalid_case",
    ["stale_expected", "source_not_current", "candidate_id", "candidate_version"],
)
def test_batch_cas_fake_rejects_invalid_source_or_candidate_without_publication(
    invalid_case: str,
) -> None:
    """The Fake never publishes a candidate detached from its exact source."""
    from qarunner.domain import BatchState, VersionConflict

    source, _, _, _, _, candidate = _competing_candidates()
    commit_source = source
    expected_version = source.version
    if invalid_case == "stale_expected":
        expected_version -= 1
    elif invalid_case == "source_not_current":
        commit_source = replace(source, state=BatchState.COLLECTING)
    elif invalid_case == "candidate_id":
        other_source = replace(source, id="batch-other")
        other_intent = _cancel_intent(
            batch_id=other_source.id,
            source_batch_version=other_source.version,
        )
        candidate = other_source.request_cancel(
            intent=other_intent,
            expected_version=other_source.version,
        )
    else:
        candidate = _unsafe_batch_replace(candidate, version=source.version + 2)
    store = InMemoryVersionedBatchStore(source)

    with pytest.raises(VersionConflict):
        store.commit(
            source=commit_source,
            candidate=candidate,
            expected_version=expected_version,
        )

    assert store.current is source
    assert store.current.state is BatchState.VALIDATING
    assert store.current.version == 3
    assert store.current.rejection_fact is None
    assert store.current.cancellation_intent is None
