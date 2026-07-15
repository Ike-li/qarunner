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


@pytest.mark.asyncio
async def test_revoked_authority_blocks_exact_replay_without_exposing_winner() -> None:
    """A caller cannot use a stored intent to recover revoked object permission."""
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import ObjectForbidden, RequestBatchCancellation
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = _intent(batch_id=source.id, source_batch_version=source.version)
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    gateway = InMemoryBatchPreexecutionGateway(batch=requested, authority_allowed=False)

    with pytest.raises(ObjectForbidden) as caught:
        await RequestBatchCancellation(gateway=gateway).execute(_command(intent=intent))

    assert caught.value.code == "OBJECT_FORBIDDEN"
    assert caught.value.retryable is False
    assert caught.value.http_status == 403
    assert gateway.authority_checks == 1
    assert gateway.batch_reads == 0
    assert gateway.batch is requested
    assert gateway.semantic_outbox == ()


@pytest.mark.asyncio
async def test_current_authority_allows_exact_replay_before_stale_batch_cas() -> None:
    """A response-loss retry returns the first intent without another side effect."""
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import RequestBatchCancellation
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = _intent(batch_id=source.id, source_batch_version=source.version)
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    gateway = InMemoryBatchPreexecutionGateway(batch=requested)
    handler = RequestBatchCancellation(gateway=gateway)

    replayed = await handler.execute(_command(intent=intent))

    assert replayed is intent
    assert gateway.authority_checks == 1
    assert gateway.batch_reads == 1
    assert gateway.batch is requested
    assert gateway.batch.version == 4
    assert gateway.semantic_outbox == ()


@pytest.mark.asyncio
async def test_cancel_key_reuse_with_changed_request_conflicts_before_stale_cas() -> None:
    """Current authority does not let one key bind a different caller request."""
    from dataclasses import replace

    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import RequestBatchCancellation
    from qarunner.domain import Batch, BatchState, IdempotencyConflict

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = _intent(batch_id=source.id, source_batch_version=source.version)
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    gateway = InMemoryBatchPreexecutionGateway(batch=requested)
    handler = RequestBatchCancellation(gateway=gateway)

    with pytest.raises(IdempotencyConflict):
        await handler.execute(
            replace(
                _command(intent=intent),
                reason="changed cancellation reason",
            )
        )

    assert gateway.authority_checks == 1
    assert gateway.batch_reads == 1
    assert gateway.batch is requested
    assert gateway.batch.version == 4
    assert gateway.batch.cancellation_intent is intent
    assert gateway.semantic_outbox == ()


@pytest.mark.asyncio
async def test_second_cancel_key_cannot_replace_the_stored_intent() -> None:
    """A new command identity cannot replace the Batch-owned first intent."""
    from dataclasses import replace

    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import RequestBatchCancellation
    from qarunner.domain import Batch, BatchCancellationConflict, BatchState

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = _intent(batch_id=source.id, source_batch_version=source.version)
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    gateway = InMemoryBatchPreexecutionGateway(batch=requested)

    with pytest.raises(BatchCancellationConflict):
        await RequestBatchCancellation(gateway=gateway).execute(
            replace(_command(intent=intent), idempotency_key="cancel-002")
        )

    assert gateway.authority_checks == 1
    assert gateway.batch_reads == 1
    assert gateway.batch is requested
    assert gateway.semantic_outbox == ()


@pytest.mark.asyncio
async def test_new_cancel_intent_atomically_publishes_batch_audit_and_outbox() -> None:
    """A new authorized request publishes one immutable intent and its side effects."""
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import (
        RequestBatchCancellation,
        RequestBatchCancellationCommand,
    )
    from qarunner.domain import Batch, BatchState, CancellationSource

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    gateway = InMemoryBatchPreexecutionGateway(batch=source)
    command = RequestBatchCancellationCommand(
        batch_id=source.id,
        project_id="project-001",
        expected_batch_version=source.version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
    )

    accepted = await RequestBatchCancellation(gateway=gateway).execute(command)

    assert accepted is gateway.batch.cancellation_intent
    assert accepted.batch_id == source.id
    assert accepted.project_id == command.project_id
    assert accepted.source_batch_version == source.version
    assert gateway.batch.state is source.state
    assert gateway.batch.version == source.version + 1
    assert len(gateway.audit_records) == 1
    assert gateway.audit_records[0].intent_digest == accepted.digest
    assert len(gateway.semantic_outbox) == 1
    assert gateway.semantic_outbox[0].intent_digest == accepted.digest


@pytest.mark.asyncio
async def test_publication_failure_leaves_batch_audit_and_outbox_unchanged() -> None:
    """The Fake models one atomic publication boundary, including failure."""
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import (
        RequestBatchCancellation,
        RequestBatchCancellationCommand,
    )
    from qarunner.domain import Batch, BatchState, CancellationSource

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    gateway = InMemoryBatchPreexecutionGateway(
        batch=source,
        publication_error=RuntimeError("commit failed"),
    )
    command = RequestBatchCancellationCommand(
        batch_id=source.id,
        project_id="project-001",
        expected_batch_version=source.version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await RequestBatchCancellation(gateway=gateway).execute(command)

    assert gateway.batch is source
    assert gateway.batch.cancellation_intent is None
    assert gateway.audit_records == ()
    assert gateway.semantic_outbox == ()


@pytest.mark.asyncio
async def test_stale_version_blocks_only_a_new_cancel_mutation() -> None:
    """After authority and replay checks, a new mutation still requires current CAS."""
    from dataclasses import replace

    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway

    from qarunner.application.batch_preexecution import RequestBatchCancellation
    from qarunner.domain import Batch, BatchState, VersionConflict

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=4)
    gateway = InMemoryBatchPreexecutionGateway(batch=source)
    intent = _intent(batch_id=source.id, source_batch_version=3)

    with pytest.raises(VersionConflict):
        await RequestBatchCancellation(gateway=gateway).execute(
            replace(_command(intent=intent), expected_batch_version=3)
        )

    assert gateway.authority_checks == 1
    assert gateway.batch_reads == 1
    assert gateway.batch is source
    assert gateway.audit_records == ()
    assert gateway.semantic_outbox == ()


def _intent(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import (
        BatchCancellationIntent,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        CancellationSource,
        canonical_digest,
    )

    def digest(label: str):
        return canonical_digest(
            schema_version="qep.test-batch-cancel.v1",
            payload={"label": label},
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
        authorization_digest=digest("cancel-authorization"),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        recorded_at=datetime(2026, 7, 14, 6, tzinfo=UTC),
    )


def _command(*, intent):
    from qarunner.application.batch_preexecution import RequestBatchCancellationCommand

    return RequestBatchCancellationCommand(
        batch_id=intent.batch_id,
        project_id=intent.project_id,
        expected_batch_version=intent.source_batch_version,
        idempotency_key=intent.idempotency_key,
        source=intent.source,
        actor_id=intent.actor_id,
        reason=intent.reason,
    )
