"""T-M0-PORT-001 execution stop-control contract."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.fakes.greenfield.clock import FakeUtcClock
from tests.fakes.greenfield.execution import InMemoryExecutionStopControl

from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.execution import (
    ExecutionStopControl,
    ExecutionStopReceipt,
    ExecutionStopRequest,
)
from qarunner.domain import (
    AttemptAuthority,
    Digest,
    EvidenceNotReady,
    IdempotencyConflict,
    PlatformExitClass,
    TrustedExitFacts,
    WorkerRef,
    build_evidence_manifest,
)


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"request_id": ""}, "request_id", "empty"),
        ({"run_id": 7}, "run_id", "not_string"),
        ({"attempt_id": "   "}, "attempt_id", "empty"),
        ({"authority": None}, "authority", "not_attempt_authority"),
        (
            {"cancellation_intent_digest": "sha256:not-a-digest"},
            "cancellation_intent_digest",
            "not_digest",
        ),
    ],
)
def test_execution_stop_request_rejects_invalid_authority_bindings(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    with pytest.raises(PortContractError) as captured:
        replace(_request(), **changes)

    assert captured.value.resource == "execution_stop_request"
    assert captured.value.field == field
    assert captured.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"request_id": None}, "request_id", "not_string"),
        ({"request_id": "   "}, "request_id", "empty"),
        ({"request_digest": "sha256:not-a-digest"}, "request_digest", "not_digest"),
        (
            {"recorded_at": datetime(2026, 7, 13, 12, 0)},
            "recorded_at",
            "not_utc",
        ),
    ],
)
def test_execution_stop_receipt_rejects_invalid_recorded_values(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    receipt = ExecutionStopReceipt(
        request_id="stop-request-1",
        request_digest=Digest("sha256:" + "1" * 64),
        recorded_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(PortContractError) as captured:
        replace(receipt, **changes)

    assert captured.value.resource == "execution_stop_receipt"
    assert captured.value.field == field
    assert captured.value.reason == reason


async def test_stop_control_accepts_one_authority_bound_request() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    request = _request()
    control = InMemoryExecutionStopControl(FakeUtcClock(now))

    result = await control.request_stop(request)

    assert isinstance(control, ExecutionStopControl)
    assert result.value.request_id == request.request_id
    assert result.value.request_digest == request.digest
    assert result.value.recorded_at == now
    assert result.replayed is False
    assert control.accepted_requests() == (request,)


async def test_stop_receipt_cannot_be_used_as_trusted_stop_proof() -> None:
    request = _request()
    control = InMemoryExecutionStopControl(FakeUtcClock(datetime(2026, 7, 13, 12, 0, tzinfo=UTC)))
    receipt = (await control.request_stop(request)).value

    with pytest.raises(EvidenceNotReady) as captured:
        build_evidence_manifest(
            attempt_id=request.attempt_id,
            run_id=request.run_id,
            attempt_no=1,
            assignment_id="assignment-1",
            fence=request.authority.current_fence,
            worker=request.authority.current_worker,
            execution_spec_digest=Digest("sha256:" + "2" * 64),
            trusted_exit=TrustedExitFacts(
                source_event_id="event-cancelled-1",
                pid=731,
                exit_class=PlatformExitClass.CANCELLED,
                exit_code=None,
                signal=15,
                oom=False,
                timeout=False,
            ),
            case_summary=None,
            artifacts=(),
            cancellation_stop=receipt,  # type: ignore[arg-type]
        )

    assert captured.value.reason == "trusted cancellation stop proof is invalid"


async def test_exact_stop_request_replays_the_first_receipt_without_redispatch() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    clock = FakeUtcClock(now)
    request = _request()
    control = InMemoryExecutionStopControl(clock)
    first = await control.request_stop(request)
    clock.advance(timedelta(minutes=5))

    replay = await control.request_stop(replace(request))

    assert replay.value == first.value
    assert replay.value.recorded_at == now
    assert replay.replayed is True
    assert control.accepted_requests() == (request,)


async def test_stop_request_id_reuse_with_changed_intent_digest_conflicts() -> None:
    request = _request()
    conflict = replace(
        request,
        cancellation_intent_digest=Digest("sha256:" + "9" * 64),
    )
    control = InMemoryExecutionStopControl(FakeUtcClock(datetime(2026, 7, 13, 12, 0, tzinfo=UTC)))
    await control.request_stop(request)

    with pytest.raises(IdempotencyConflict) as captured:
        await control.request_stop(conflict)

    assert captured.value.scope == "execution_stop"
    assert captured.value.key == request.request_id
    assert captured.value.stored_digest == request.digest
    assert captured.value.received_digest == conflict.digest
    assert control.accepted_requests() == (request,)


async def test_stop_request_id_reuse_with_changed_authority_conflicts() -> None:
    request = _request()
    conflict = replace(
        request,
        authority=AttemptAuthority(
            current_fence=4,
            current_worker=WorkerRef(worker_id="worker-1", generation=3),
        ),
    )
    control = InMemoryExecutionStopControl(FakeUtcClock(datetime(2026, 7, 13, 12, 0, tzinfo=UTC)))
    await control.request_stop(request)

    with pytest.raises(IdempotencyConflict) as captured:
        await control.request_stop(conflict)

    assert captured.value.stored_digest == request.digest
    assert captured.value.received_digest == conflict.digest
    assert control.accepted_requests() == (request,)


async def test_stop_failure_before_accept_records_nothing_then_allows_retry() -> None:
    request = _request()
    unavailable = RuntimeError("stop control unavailable")
    control = InMemoryExecutionStopControl(
        FakeUtcClock(datetime(2026, 7, 13, 12, 0, tzinfo=UTC)),
        before_accept_error=unavailable,
    )

    with pytest.raises(RuntimeError, match="stop control unavailable"):
        await control.request_stop(request)

    assert control.accepted_requests() == ()

    retry = await control.request_stop(request)

    assert retry.replayed is False
    assert control.accepted_requests() == (request,)


async def test_stop_response_loss_after_accept_replays_the_durable_receipt() -> None:
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    clock = FakeUtcClock(now)
    request = _request()
    response_lost = RuntimeError("stop response lost")
    control = InMemoryExecutionStopControl(
        clock,
        after_accept_error=response_lost,
    )

    with pytest.raises(RuntimeError, match="stop response lost"):
        await control.request_stop(request)

    assert control.accepted_requests() == (request,)
    clock.advance(timedelta(minutes=5))

    replay = await control.request_stop(request)

    assert replay.value.recorded_at == now
    assert replay.replayed is True
    assert control.accepted_requests() == (request,)


def _request() -> ExecutionStopRequest:
    return ExecutionStopRequest(
        request_id="stop-request-1",
        run_id="run-1",
        attempt_id="attempt-1",
        authority=AttemptAuthority(
            current_fence=3,
            current_worker=WorkerRef(worker_id="worker-1", generation=2),
        ),
        cancellation_intent_digest=Digest("sha256:" + "1" * 64),
    )
