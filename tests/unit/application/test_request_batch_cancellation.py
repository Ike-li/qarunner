"""Application ordering for Batch-owned pre-execution cancellation."""

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_authority_unavailable_blocks_exact_replay_without_exposing_stored_intent() -> None:
    """Live authority fails closed before stored replay and stale Batch CAS."""
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import (
        RequestBatchCancellation,
        RequestBatchCancellationCommand,
        TemporarilyUnavailable,
    )
    from qarunner.domain import (
        Batch,
        BatchCancellationIntent,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        BatchState,
        CancellationSource,
        canonical_digest,
    )

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    scope = BatchCancellationScope(
        kind=BatchCancellationScopeKind.PRE_PLAN,
        preplan_scope_digest=canonical_digest(
            schema_version="qep.test-batch-cancel.v1",
            payload={"label": "preplan-scope"},
        ),
        manifest_digest=None,
        shard_plan_version=None,
        shard_plan_digest=None,
        canonical_run_set_digest=None,
    )
    intent = BatchCancellationIntent(
        batch_id=source.id,
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=source.version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
        authorization_digest=canonical_digest(
            schema_version="qep.test-batch-cancel.v1",
            payload={"label": "cancel-authorization"},
        ),
        scope=scope,
        recorded_at=datetime(2026, 7, 14, 6, tzinfo=UTC),
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    gateway = InMemoryBatchPreexecutionGateway(
        batch=requested,
        authority_available=False,
    )
    handler = RequestBatchCancellation(gateway=gateway)
    command = RequestBatchCancellationCommand(
        batch_id=source.id,
        project_id=intent.project_id,
        expected_batch_version=source.version,
        idempotency_key=intent.idempotency_key,
        source=intent.source,
        actor_id=intent.actor_id,
        reason=intent.reason,
    )

    with pytest.raises(TemporarilyUnavailable) as caught:
        await handler.execute(command)

    problem = caught.value
    assert problem.code == "TEMPORARILY_UNAVAILABLE"
    assert problem.retryable is True
    assert problem.http_status == 503
    exposed = f"{problem!r} {problem} {vars(problem)}"
    for secret in (
        "cancel-001",
        "project-001",
        intent.digest.value,
        "winner",
        "convergence",
    ):
        assert secret not in exposed
    assert gateway.authority_checks == 1
    assert gateway.batch_reads == 0
    assert gateway.batch is requested
    assert gateway.batch.version == 4
    assert gateway.batch.cancellation_intent is intent
    assert gateway.semantic_outbox == ()
