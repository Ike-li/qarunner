"""T-M0-PORT-001 append-only Attempt event contract."""

from dataclasses import replace

import pytest
from tests.fakes.greenfield.facts import InMemoryAttemptEventLog

from qarunner.application.ports.facts import AttemptEventLog
from qarunner.domain import AttemptEvent, Digest, EventConflict


async def test_attempt_event_log_appends_one_event_and_returns_an_immutable_snapshot() -> None:
    event = AttemptEvent(
        event_id="event-1",
        event_seq=1,
        event_type="started",
        payload_digest=Digest("sha256:" + "1" * 64),
    )
    log = InMemoryAttemptEventLog()

    result = await log.append("attempt-1", event)

    assert isinstance(log, AttemptEventLog)
    assert result.value == event
    assert result.replayed is False
    assert await log.list_for_attempt("attempt-1") == (event,)


async def test_attempt_event_log_replays_the_first_exact_event_without_appending() -> None:
    event = AttemptEvent(
        event_id="event-1",
        event_seq=1,
        event_type="started",
        payload_digest=Digest("sha256:" + "1" * 64),
    )
    log = InMemoryAttemptEventLog()
    await log.append("attempt-1", event)

    replay = await log.append("attempt-1", replace(event))

    assert replay.value == event
    assert replay.replayed is True
    assert await log.list_for_attempt("attempt-1") == (event,)


async def test_attempt_event_stream_identity_is_scoped_to_each_attempt() -> None:
    event = AttemptEvent(
        event_id="event-1",
        event_seq=1,
        event_type="started",
        payload_digest=Digest("sha256:" + "1" * 64),
    )
    log = InMemoryAttemptEventLog()

    first = await log.append("attempt-1", event)
    second = await log.append("attempt-2", event)

    assert first.replayed is False
    assert second.replayed is False
    assert await log.list_for_attempt("attempt-1") == (event,)
    assert await log.list_for_attempt("attempt-2") == (event,)


async def test_attempt_event_snapshot_does_not_change_after_later_appends() -> None:
    first = AttemptEvent(
        event_id="event-1",
        event_seq=1,
        event_type="started",
        payload_digest=Digest("sha256:" + "1" * 64),
    )
    second = replace(
        first,
        event_id="event-2",
        event_seq=2,
        event_type="finished",
        payload_digest=Digest("sha256:" + "2" * 64),
    )
    log = InMemoryAttemptEventLog()
    await log.append("attempt-1", first)
    snapshot = await log.list_for_attempt("attempt-1")

    await log.append("attempt-1", second)

    assert snapshot == (first,)
    assert await log.list_for_attempt("attempt-1") == (first, second)


async def test_attempt_event_log_rejects_reused_event_id_without_changing_history() -> None:
    event = AttemptEvent(
        event_id="event-1",
        event_seq=1,
        event_type="started",
        payload_digest=Digest("sha256:" + "1" * 64),
    )
    conflict = replace(event, event_type="finished")
    log = InMemoryAttemptEventLog()
    await log.append("attempt-1", event)

    with pytest.raises(EventConflict) as captured:
        await log.append("attempt-1", conflict)

    assert captured.value.attempt_id == "attempt-1"
    assert captured.value.stored_event == event
    assert captured.value.received_event == conflict
    assert await log.list_for_attempt("attempt-1") == (event,)


async def test_attempt_event_log_rejects_reused_sequence_without_changing_history() -> None:
    event = AttemptEvent(
        event_id="event-1",
        event_seq=1,
        event_type="started",
        payload_digest=Digest("sha256:" + "1" * 64),
    )
    conflict = replace(
        event,
        event_id="event-2",
        payload_digest=Digest("sha256:" + "2" * 64),
    )
    log = InMemoryAttemptEventLog()
    await log.append("attempt-1", event)

    with pytest.raises(EventConflict) as captured:
        await log.append("attempt-1", conflict)

    assert captured.value.attempt_id == "attempt-1"
    assert captured.value.stored_event == event
    assert captured.value.received_event == conflict
    assert await log.list_for_attempt("attempt-1") == (event,)
