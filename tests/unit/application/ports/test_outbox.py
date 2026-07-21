"""Outbox delivery event contract."""

from dataclasses import replace

import pytest

from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.outbox import OutboxDeliveryEvent
from qarunner.domain.digest import Digest


def _event() -> OutboxDeliveryEvent:
    return OutboxDeliveryEvent(
        event_id="event-1",
        aggregate_type="batch",
        aggregate_id="batch-1",
        event_type="batch.cancelled.v1",
        payload_digest=Digest(f"sha256:{'a' * 64}"),
        payload={"batch_id": "batch-1"},
        delivery_attempt=1,
    )


def test_outbox_delivery_event_round_trips_its_fields() -> None:
    event = _event()

    assert event.event_id == "event-1"
    assert event.aggregate_type == "batch"
    assert event.aggregate_id == "batch-1"
    assert event.event_type == "batch.cancelled.v1"
    assert event.payload_digest == Digest(f"sha256:{'a' * 64}")
    assert event.payload == {"batch_id": "batch-1"}
    assert event.delivery_attempt == 1


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"event_id": ""}, "event_id", "invalid"),
        ({"event_id": "   "}, "event_id", "invalid"),
        ({"aggregate_type": ""}, "aggregate_type", "invalid"),
        ({"aggregate_id": ""}, "aggregate_id", "invalid"),
        ({"event_type": ""}, "event_type", "invalid"),
        ({"payload_digest": "not-a-digest"}, "payload_digest", "not_digest"),
        ({"payload": ["not", "a", "dict"]}, "payload", "not_dict"),
        ({"delivery_attempt": 0}, "delivery_attempt", "invalid"),
        ({"delivery_attempt": True}, "delivery_attempt", "invalid"),
        ({"delivery_attempt": 1.5}, "delivery_attempt", "invalid"),
    ],
)
def test_outbox_delivery_event_rejects_invalid_values(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    with pytest.raises(PortContractError) as captured:
        replace(_event(), **changes)

    assert captured.value.resource == "outbox_delivery_event"
    assert captured.value.field == field
    assert captured.value.reason == reason
