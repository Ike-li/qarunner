"""M0 immutable Attempt event envelope contract."""

from dataclasses import replace

import pytest

from qarunner.domain import AttemptEvent, Digest, DomainValidationError


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        ({"event_id": None}, "event_id", "not_string"),
        ({"event_id": "   "}, "event_id", "empty"),
        ({"event_seq": True}, "event_seq", "not_integer"),
        ({"event_seq": "1"}, "event_seq", "not_integer"),
        ({"event_seq": 0}, "event_seq", "not_positive"),
        ({"event_seq": -1}, "event_seq", "not_positive"),
        ({"event_type": 7}, "event_type", "not_string"),
        ({"event_type": ""}, "event_type", "empty"),
        ({"payload_digest": "sha256:not-a-digest"}, "payload_digest", "not_digest"),
    ],
)
def test_attempt_event_rejects_an_invalid_envelope(
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    event = AttemptEvent(
        event_id="event-1",
        event_seq=1,
        event_type="started",
        payload_digest=Digest("sha256:" + "1" * 64),
    )

    with pytest.raises(DomainValidationError) as captured:
        replace(event, **changes)

    assert captured.value.entity_type == "attempt_event"
    assert captured.value.field == field
    assert captured.value.reason == reason
