"""Deterministic Fake for outbox event delivery."""

from qarunner.application.ports.outbox import OutboxDeliveryEvent


class InMemoryOutboxSink:
    """Records delivered events; can be configured to fail specific ones."""

    def __init__(
        self,
        *,
        fail_event_ids: frozenset[str] = frozenset(),
        fail_until_attempt: dict[str, int] | None = None,
    ) -> None:
        self.delivered: list[OutboxDeliveryEvent] = []
        self._fail_event_ids = fail_event_ids
        self._fail_until_attempt = dict(fail_until_attempt or {})

    async def deliver(self, event: OutboxDeliveryEvent) -> None:
        if event.event_id in self._fail_event_ids:
            raise RuntimeError(f"outbox_sink_permanent_failure:{event.event_id}")
        threshold = self._fail_until_attempt.get(event.event_id)
        if threshold is not None and event.delivery_attempt < threshold:
            raise RuntimeError(
                f"outbox_sink_transient_failure:{event.event_id}:{event.delivery_attempt}"
            )
        self.delivered.append(event)
