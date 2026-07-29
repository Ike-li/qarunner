"""Bounded queues with stable wait_reason (M6, T-M6-QUEUE-001).

Three priority classes (interactive/scheduled/background) with bounded depth.
Enqueue beyond max_depth rejects with a stable reason. Non-preemptive: dequeue
is FIFO within each class, interactive has highest priority across classes.
Pure domain — no adapters.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError


class QueueClass(enum.StrEnum):
    """Priority classes for bounded queues (DES §11.2)."""

    INTERACTIVE = "interactive"
    SCHEDULED = "scheduled"
    BACKGROUND = "background"


# Dequeue priority order: interactive first, then scheduled, then background.
_DEQUEUE_PRIORITY = (QueueClass.INTERACTIVE, QueueClass.SCHEDULED, QueueClass.BACKGROUND)


class AdmissionDecisionKind(enum.StrEnum):
    """Outcome of an admission attempt against a bounded queue."""

    ADMITTED = "admitted"
    QUEUED = "queued"
    CAPACITY_REJECTED = "capacity_rejected"


class QueueOverflow(ValueError):
    """Raised when enqueue exceeds max_depth for a queue class."""

    code = "queue_overflow"

    def __init__(
        self,
        *,
        queue_class: QueueClass,
        current_depth: int,
        max_depth: int,
        reason: str,
    ) -> None:
        if not isinstance(queue_class, QueueClass):
            raise DomainValidationError(
                entity_type="queue_overflow", field="queue_class", reason="invalid"
            )
        if (
            isinstance(current_depth, bool)
            or not isinstance(current_depth, int)
            or current_depth < 0
        ):
            raise DomainValidationError(
                entity_type="queue_overflow", field="current_depth", reason="not_nonnegative"
            )
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
            raise DomainValidationError(
                entity_type="queue_overflow", field="max_depth", reason="not_positive"
            )
        self.queue_class = queue_class
        self.current_depth = current_depth
        self.max_depth = max_depth
        self.reason = reason
        msg = (
            f"queue overflow: {queue_class.value} depth "
            f"{current_depth} >= max {max_depth} ({reason})"
        )
        super().__init__(msg)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Audit-ready admission outcome with stable limiting_factor."""

    kind: AdmissionDecisionKind
    batch_id: str
    queue_class: QueueClass
    limiting_factor: str | None
    reason: str | None

    def __post_init__(self) -> None:
        entity = "admission_decision"
        if not isinstance(self.kind, AdmissionDecisionKind):
            raise DomainValidationError(entity_type=entity, field="kind", reason="invalid")
        if not isinstance(self.batch_id, str) or not self.batch_id.strip():
            raise DomainValidationError(entity_type=entity, field="batch_id", reason="invalid")
        if not isinstance(self.queue_class, QueueClass):
            raise DomainValidationError(entity_type=entity, field="queue_class", reason="invalid")


@dataclass(frozen=True, slots=True)
class EnqueueReceipt:
    """Receipt returned after successful enqueue."""

    enqueue_id: str
    batch_id: str
    queue_class: QueueClass
    depth_at_enqueue: int


@dataclass(frozen=True, slots=True)
class DequeueItem:
    """Item returned by dequeue."""

    batch_id: str
    queue_class: QueueClass
    enqueue_id: str


@dataclass(frozen=True, slots=True)
class BoundedQueue:
    """Pure domain bounded queue with per-class depth limits.

    Items are stored as immutable tuples per class: (batch_id, enqueued_at_iso, enqueue_id).
    Operations return new BoundedQueue instances (functional/immutable).
    """

    queue_id: str
    max_depth_by_class: tuple[tuple[QueueClass, int], ...]
    _items_interactive: tuple[tuple[str, str, str], ...] = ()
    _items_scheduled: tuple[tuple[str, str, str], ...] = ()
    _items_background: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        entity = "bounded_queue"
        if not isinstance(self.queue_id, str) or not self.queue_id.strip():
            raise DomainValidationError(entity_type=entity, field="queue_id", reason="invalid")
        if not isinstance(self.max_depth_by_class, tuple) or not self.max_depth_by_class:
            raise DomainValidationError(
                entity_type=entity, field="max_depth_by_class", reason="empty"
            )
        seen_classes: set[str] = set()
        for queue_class, max_depth in self.max_depth_by_class:
            if not isinstance(queue_class, QueueClass):
                raise DomainValidationError(
                    entity_type=entity, field="max_depth_by_class", reason="invalid"
                )
            if queue_class.value in seen_classes:
                raise DomainValidationError(
                    entity_type=entity, field="max_depth_by_class", reason="duplicate"
                )
            seen_classes.add(queue_class.value)
            if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
                raise DomainValidationError(
                    entity_type=entity, field="max_depth", reason="not_positive"
                )

    @property
    def queue_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.bounded-queue.v1",
            payload={
                "queue_id": self.queue_id,
                "max_depth_by_class": [
                    {"queue_class": qc.value, "max_depth": md}
                    for qc, md in sorted(self.max_depth_by_class, key=lambda x: x[0].value)
                ],
            },
        )

    def max_depth_for(self, queue_class: QueueClass) -> int:
        for qc, md in self.max_depth_by_class:
            if qc is queue_class:
                return md
        raise DomainValidationError(
            entity_type="bounded_queue", field="queue_class", reason="unknown_queue_class"
        )

    def enqueue(
        self,
        *,
        batch_id: str,
        queue_class: QueueClass,
        enqueue_id: str,
        enqueued_at: datetime,
    ) -> tuple[BoundedQueue, EnqueueReceipt]:
        """Enqueue a batch; raises QueueOverflow on max_depth exceeded."""
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise DomainValidationError(
                entity_type="bounded_queue", field="batch_id", reason="invalid"
            )
        if not isinstance(queue_class, QueueClass):
            raise DomainValidationError(
                entity_type="bounded_queue", field="queue_class", reason="unknown_queue_class"
            )
        if not isinstance(enqueue_id, str) or not enqueue_id.strip():
            raise DomainValidationError(
                entity_type="bounded_queue", field="enqueue_id", reason="invalid"
            )
        self._require_utc("bounded_queue", "enqueued_at", enqueued_at)

        # Duplicate check across all classes.
        for qc in QueueClass:
            for item in self._items_for(qc):
                if item[2] == enqueue_id:
                    raise DomainValidationError(
                        entity_type="bounded_queue", field="enqueue_id", reason="duplicate"
                    )

        max_depth = self.max_depth_for(queue_class)
        current_depth = len(self._items_for(queue_class))
        if current_depth >= max_depth:
            raise QueueOverflow(
                queue_class=queue_class,
                current_depth=current_depth,
                max_depth=max_depth,
                reason="max_depth_exceeded",
            )

        new_items = self._items_for(queue_class) + (
            (batch_id, enqueued_at.isoformat(), enqueue_id),
        )
        new_state = self._with_items(queue_class, new_items)

        receipt = EnqueueReceipt(
            enqueue_id=enqueue_id,
            batch_id=batch_id,
            queue_class=queue_class,
            depth_at_enqueue=current_depth + 1,
        )
        return new_state, receipt

    def dequeue(self) -> tuple[BoundedQueue, DequeueItem | None]:
        """Dequeue highest-priority item (interactive > scheduled > background)."""
        for queue_class in _DEQUEUE_PRIORITY:
            items = self._items_for(queue_class)
            if items:
                item = items[0]
                new_state = self._with_items(queue_class, items[1:])
                return new_state, DequeueItem(
                    batch_id=item[0],
                    queue_class=queue_class,
                    enqueue_id=item[2],
                )
        return self, None

    @property
    def depth_by_class(self) -> dict[QueueClass, int]:
        return {qc: len(self._items_for(qc)) for qc in QueueClass}

    def _items_for(self, queue_class: QueueClass) -> tuple[tuple[str, str, str], ...]:
        if queue_class is QueueClass.INTERACTIVE:
            return self._items_interactive
        if queue_class is QueueClass.SCHEDULED:
            return self._items_scheduled
        return self._items_background

    def _with_items(
        self, queue_class: QueueClass, items: tuple[tuple[str, str, str], ...]
    ) -> BoundedQueue:
        kwargs: dict[str, object] = {
            "queue_id": self.queue_id,
            "max_depth_by_class": self.max_depth_by_class,
        }
        kwargs[f"_items_{queue_class.value}"] = items
        # Preserve other classes' items.
        for qc in QueueClass:
            if qc is not queue_class:
                kwargs[f"_items_{qc.value}"] = self._items_for(qc)
        return BoundedQueue(**kwargs)  # type: ignore[arg-type]

    def _require_utc(self, entity: str, field: str, value: object) -> None:
        if not isinstance(value, datetime):
            raise DomainValidationError(entity_type=entity, field=field, reason="not_datetime")
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise DomainValidationError(entity_type=entity, field=field, reason="not_utc")
