"""T-M0-STATE-001F deterministic handoff consumer boundary."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from tests.fakes.greenfield.handoff import InMemoryMaterializedScopeHandoffConsumer

from qarunner.application.handoff import build_cancel_handoff, build_rejection_conflict_handoff
from qarunner.application.ports.handoff import (
    HandoffDeliveryMetadata,
    HandoffEventBlocked,
    HandoffEventConflict,
)
from qarunner.domain import (
    BatchCancellationIntent,
    BatchCancellationScope,
    BatchCancellationScopeKind,
    BatchRejection,
    BatchRejectionReasonClass,
    BatchRejectionStage,
    CancellationSource,
    canonical_digest,
)


@pytest.mark.asyncio
async def test_handoff_consumer_exact_duplicate_returns_first_value_without_downstream_work() -> (
    None
):
    consumer = InMemoryMaterializedScopeHandoffConsumer()
    handoff = _handoff()

    first = await consumer.consume(handoff=handoff)
    replay = await consumer.consume(handoff=handoff)

    assert first.value is handoff
    assert first.replayed is False
    assert replay.value is handoff
    assert replay.replayed is True
    assert consumer.accepted == (handoff,)
    _assert_no_downstream_mutation(consumer)


@pytest.mark.asyncio
async def test_handoff_consumer_quarantines_poison_event_without_overwriting_first_value() -> None:
    consumer = InMemoryMaterializedScopeHandoffConsumer()
    handoff = _handoff()
    await consumer.consume(handoff=handoff)
    poisoned = replace(
        handoff,
        payload_digest=canonical_digest(
            schema_version="qep.test-poison-handoff.v1",
            payload={"event_id": handoff.event_id},
        ),
    )

    with pytest.raises(HandoffEventConflict) as captured:
        await consumer.consume(handoff=poisoned)

    assert captured.value.event_id == handoff.event_id
    assert captured.value.stored_payload_digest == handoff.payload_digest
    assert captured.value.received_payload_digest == poisoned.payload_digest
    assert consumer.accepted == (handoff,)
    assert consumer.quarantined_event_ids == (handoff.event_id,)
    assert len(consumer.alerts) == 1
    assert consumer.alerts[0].event_id == handoff.event_id
    assert consumer.alerts[0].severity == "high"
    with pytest.raises(HandoffEventBlocked):
        await consumer.consume(handoff=handoff)
    assert consumer.quarantined_event_ids == (handoff.event_id,)
    assert len(consumer.alerts) == 1
    _assert_no_downstream_mutation(consumer)


@pytest.mark.asyncio
async def test_publisher_delivery_metadata_does_not_change_semantic_replay_identity() -> None:
    consumer = InMemoryMaterializedScopeHandoffConsumer()
    handoff = _handoff()

    first = await consumer.consume(
        handoff=handoff,
        publisher_metadata=HandoffDeliveryMetadata(
            claim_id="claim-001",
            attempt=1,
            status="claimed",
        ),
    )
    replay = await consumer.consume(
        handoff=handoff,
        publisher_metadata=HandoffDeliveryMetadata(
            claim_id="claim-002",
            attempt=2,
            status="redelivered",
        ),
    )

    assert first.value is replay.value
    assert replay.replayed is True
    assert consumer.accepted == (handoff,)


def test_handoff_identity_is_deterministic_while_binding_drift_changes_only_full_digests() -> None:
    handoff = _handoff()
    replay = _handoff()
    drifted = _handoff(run_label="run-set-drift")

    assert replay == handoff
    assert drifted.semantic_trigger_key == handoff.semantic_trigger_key
    assert drifted.handoff_id == handoff.handoff_id
    assert drifted.event_id == handoff.event_id
    assert drifted.handoff_digest != handoff.handoff_digest
    assert drifted.payload_digest != handoff.payload_digest


def test_rejection_conflict_handoff_uses_rejection_observation_without_terminal_result() -> None:
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id="batch-001",
        source_batch_version=3,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="invalid_suite",
        input_digest=_digest("rejection-input"),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
    )

    handoff = build_rejection_conflict_handoff(
        rejection=rejection,
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        preplan_scope_digest=_digest("preplan-scope"),
        manifest_digest=None,
        shard_plan_version=None,
        shard_plan_digest=None,
        authoritative_run_set_digest=_digest("run-set"),
        authority_digest=_digest("phase-authority"),
        write_epoch=1,
    )

    assert handoff.trigger_kind.value == "rejection_conflict"
    assert handoff.command_or_observation_digest == rejection.digest
    assert handoff.source_batch_version == rejection.source_batch_version
    assert handoff.destination == "execution_path"
    assert not hasattr(handoff, "batch_terminal")
    assert not hasattr(handoff, "run_resolution")
    assert not hasattr(handoff, "fanout_result")
    assert not hasattr(handoff, "execution_basis")


def _handoff(*, run_label: str = "run-set"):
    intent = BatchCancellationIntent(
        batch_id="batch-001",
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=3,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
        authorization_digest=_digest("cancel-authority"),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=_digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        recorded_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
    )
    return build_cancel_handoff(
        intent=intent,
        source_batch_version=4,
        authoritative_run_set_digest=_digest(run_label),
        authority_digest=_digest("closure-authority"),
        write_epoch=1,
    )


def _digest(label: str):
    return canonical_digest(
        schema_version="qep.test-handoff.v1",
        payload={"label": label},
    )


def _assert_no_downstream_mutation(consumer) -> None:
    assert consumer.run_resolutions == ()
    assert consumer.fanout_results == ()
    assert consumer.not_executed_facts == ()
    assert consumer.run_outcomes == ()
    assert consumer.execution_bases == ()
