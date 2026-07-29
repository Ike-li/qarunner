"""T-M6-QUEUE-001: bounded queues with stable wait_reason (DES §11.2).

Three priority classes (interactive/scheduled/background) with bounded depth.
Enqueue beyond max_depth rejects with a stable reason that identifies the
limiting class. Non-preemptive: dequeue is FIFO within each class, but
interactive has highest priority across classes. Pure domain — no adapters.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qarunner.domain import (
    AdmissionDecision,
    AdmissionDecisionKind,
    BoundedQueue,
    DomainValidationError,
    QueueClass,
    QueueOverflow,
)


def _now() -> datetime:
    return datetime(2026, 7, 29, 1, 0, tzinfo=UTC)


def _queue(
    *,
    max_depth: dict[str, int] | None = None,
    queue_id: str = "queue-001",
) -> BoundedQueue:
    limits = max_depth or {
        QueueClass.INTERACTIVE: 10,
        QueueClass.SCHEDULED: 20,
        QueueClass.BACKGROUND: 50,
    }
    return BoundedQueue(
        queue_id=queue_id,
        max_depth_by_class=tuple(sorted(limits.items())),
    )


# ── QueueClass ───────────────────────────────────────────────────────────────


def test_queue_class_enum_values() -> None:
    assert QueueClass.INTERACTIVE == "interactive"
    assert QueueClass.SCHEDULED == "scheduled"
    assert QueueClass.BACKGROUND == "background"


# ── BoundedQueue construction ────────────────────────────────────────────────


def test_bounded_queue_rejects_invalid_construction() -> None:
    with pytest.raises(DomainValidationError):
        BoundedQueue(queue_id=" ", max_depth_by_class=((QueueClass.BACKGROUND, 10),))
    with pytest.raises(DomainValidationError):
        BoundedQueue(queue_id="q", max_depth_by_class=())
    with pytest.raises(DomainValidationError):
        BoundedQueue(queue_id="q", max_depth_by_class=((QueueClass.BACKGROUND, 0),))
    with pytest.raises(DomainValidationError):
        BoundedQueue(queue_id="q", max_depth_by_class=((QueueClass.BACKGROUND, -1),))
    with pytest.raises(DomainValidationError):
        BoundedQueue(
            queue_id="q",
            max_depth_by_class=((QueueClass.BACKGROUND, True),),  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        BoundedQueue(
            queue_id="q",
            max_depth_by_class=[(QueueClass.BACKGROUND, 10)],  # type: ignore[arg-type]
        )
    with pytest.raises(DomainValidationError):
        BoundedQueue(
            queue_id="q",
            max_depth_by_class=(("invalid", 10),),  # type: ignore[arg-type]
        )
    # Duplicate class in max_depth_by_class.
    with pytest.raises(DomainValidationError):
        BoundedQueue(
            queue_id="q",
            max_depth_by_class=(
                (QueueClass.BACKGROUND, 10),
                (QueueClass.BACKGROUND, 20),
            ),
        )


def test_bounded_queue_digest_is_stable() -> None:
    q1 = _queue()
    q2 = _queue()
    assert q1.queue_digest == q2.queue_digest


# ── Enqueue + dequeue ────────────────────────────────────────────────────────


def test_enqueue_fifo_per_class_dequeue_respects_priority() -> None:
    q = _queue()
    q, receipt_a = q.enqueue(
        batch_id="batch-a",
        queue_class=QueueClass.BACKGROUND,
        enqueue_id="e-1",
        enqueued_at=_now(),
    )
    q, receipt_b = q.enqueue(
        batch_id="batch-b",
        queue_class=QueueClass.INTERACTIVE,
        enqueue_id="e-2",
        enqueued_at=_now(),
    )
    q, receipt_c = q.enqueue(
        batch_id="batch-c",
        queue_class=QueueClass.BACKGROUND,
        enqueue_id="e-3",
        enqueued_at=_now(),
    )

    # Dequeue: interactive first, then FIFO within class.
    q, item1 = q.dequeue()
    assert item1.batch_id == "batch-b"
    assert item1.queue_class is QueueClass.INTERACTIVE

    q, item2 = q.dequeue()
    assert item2.batch_id == "batch-a"
    assert item2.queue_class is QueueClass.BACKGROUND

    q, item3 = q.dequeue()
    assert item3.batch_id == "batch-c"
    assert item3.queue_class is QueueClass.BACKGROUND

    # Empty queue returns None.
    q, item4 = q.dequeue()
    assert item4 is None


def test_enqueue_respects_max_depth_per_class() -> None:
    q = _queue(
        max_depth={QueueClass.INTERACTIVE: 1, QueueClass.SCHEDULED: 1, QueueClass.BACKGROUND: 2}
    )
    q, _ = q.enqueue(
        batch_id="b1", queue_class=QueueClass.BACKGROUND, enqueue_id="e-1", enqueued_at=_now()
    )
    q, _ = q.enqueue(
        batch_id="b2", queue_class=QueueClass.BACKGROUND, enqueue_id="e-2", enqueued_at=_now()
    )

    # Third background enqueue exceeds max_depth=2.
    with pytest.raises(QueueOverflow) as caught:
        q.enqueue(
            batch_id="b3", queue_class=QueueClass.BACKGROUND, enqueue_id="e-3", enqueued_at=_now()
        )

    assert caught.value.queue_class is QueueClass.BACKGROUND
    assert caught.value.current_depth == 2
    assert caught.value.max_depth == 2
    assert caught.value.reason == "max_depth_exceeded"


def test_enqueue_rejects_duplicate_enqueue_id() -> None:
    q = _queue()
    q, _ = q.enqueue(
        batch_id="b1", queue_class=QueueClass.BACKGROUND, enqueue_id="e-1", enqueued_at=_now()
    )
    with pytest.raises(DomainValidationError) as caught:
        q.enqueue(
            batch_id="b2", queue_class=QueueClass.BACKGROUND, enqueue_id="e-1", enqueued_at=_now()
        )
    assert caught.value.reason == "duplicate"


def test_enqueue_rejects_unknown_queue_class() -> None:
    q = _queue()
    with pytest.raises(DomainValidationError) as caught:
        q.enqueue(batch_id="b1", queue_class="unknown", enqueue_id="e-1", enqueued_at=_now())  # type: ignore[arg-type]
    assert caught.value.reason == "unknown_queue_class"


def test_enqueue_rejects_missing_queue_class_not_in_max_depth() -> None:
    """queue_class is a valid QueueClass but not present in max_depth_by_class."""
    q = BoundedQueue(
        queue_id="q",
        max_depth_by_class=((QueueClass.BACKGROUND, 10),),
    )
    with pytest.raises(DomainValidationError) as caught:
        q.enqueue(
            batch_id="b1", queue_class=QueueClass.INTERACTIVE, enqueue_id="e-1", enqueued_at=_now()
        )
    assert caught.value.reason == "unknown_queue_class"


def test_enqueue_rejects_blank_batch_id() -> None:
    q = _queue()
    with pytest.raises(DomainValidationError):
        q.enqueue(
            batch_id=" ", queue_class=QueueClass.BACKGROUND, enqueue_id="e-1", enqueued_at=_now()
        )


def test_enqueue_rejects_blank_enqueue_id() -> None:
    q = _queue()
    with pytest.raises(DomainValidationError):
        q.enqueue(
            batch_id="b1", queue_class=QueueClass.BACKGROUND, enqueue_id=" ", enqueued_at=_now()
        )


def test_enqueue_rejects_naive_datetime() -> None:
    q = _queue()
    with pytest.raises(DomainValidationError):
        q.enqueue(
            batch_id="b1",
            queue_class=QueueClass.BACKGROUND,
            enqueue_id="e-1",
            enqueued_at=datetime(2026, 7, 29, 1, 0),
        )


def test_enqueue_rejects_non_utc_datetime() -> None:
    from datetime import timedelta as td
    from datetime import timezone

    q = _queue()
    non_utc = datetime(2026, 7, 29, 1, 0, tzinfo=timezone(td(hours=5)))
    with pytest.raises(DomainValidationError) as caught:
        q.enqueue(
            batch_id="b1",
            queue_class=QueueClass.BACKGROUND,
            enqueue_id="e-1",
            enqueued_at=non_utc,
        )
    assert caught.value.reason == "not_utc"


def test_enqueue_rejects_non_datetime_enqueued_at() -> None:
    q = _queue()
    with pytest.raises(DomainValidationError) as caught:
        q.enqueue(
            batch_id="b1",
            queue_class=QueueClass.BACKGROUND,
            enqueue_id="e-1",
            enqueued_at="not-a-datetime",  # type: ignore[arg-type]
        )
    assert caught.value.reason == "not_datetime"


def test_depth_counts_only_enqueued_class() -> None:
    """Each class has independent depth; filling one class doesn't affect others."""
    q = _queue(
        max_depth={QueueClass.INTERACTIVE: 1, QueueClass.SCHEDULED: 1, QueueClass.BACKGROUND: 50}
    )
    q, _ = q.enqueue(
        batch_id="b1", queue_class=QueueClass.INTERACTIVE, enqueue_id="e-1", enqueued_at=_now()
    )
    # Background is still empty.
    assert q.depth_by_class[QueueClass.BACKGROUND] == 0
    assert q.depth_by_class[QueueClass.INTERACTIVE] == 1
    q, _ = q.enqueue(
        batch_id="b2", queue_class=QueueClass.BACKGROUND, enqueue_id="e-2", enqueued_at=_now()
    )
    assert q.depth_by_class[QueueClass.BACKGROUND] == 1


# ── QueueOverflow ────────────────────────────────────────────────────────────


def test_queue_overflow_rejects_invalid_construction() -> None:
    with pytest.raises(DomainValidationError):
        QueueOverflow(queue_class="bad", current_depth=1, max_depth=2, reason="overflow")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        QueueOverflow(
            queue_class=QueueClass.BACKGROUND, current_depth=-1, max_depth=2, reason="overflow"
        )
    with pytest.raises(DomainValidationError):
        QueueOverflow(
            queue_class=QueueClass.BACKGROUND, current_depth=1, max_depth=0, reason="overflow"
        )


# ── AdmissionDecision ────────────────────────────────────────────────────────


def test_admission_decision_admitted_has_no_limiting_factor() -> None:
    d = AdmissionDecision(
        kind=AdmissionDecisionKind.ADMITTED,
        batch_id="batch-001",
        queue_class=QueueClass.INTERACTIVE,
        limiting_factor=None,
        reason=None,
    )
    assert d.kind is AdmissionDecisionKind.ADMITTED
    assert d.limiting_factor is None


def test_admission_decision_queued_has_limiting_factor() -> None:
    d = AdmissionDecision(
        kind=AdmissionDecisionKind.QUEUED,
        batch_id="batch-001",
        queue_class=QueueClass.BACKGROUND,
        limiting_factor="host_cpu",
        reason="awaiting_capacity",
    )
    assert d.kind is AdmissionDecisionKind.QUEUED
    assert d.limiting_factor == "host_cpu"


def test_admission_decision_rejected_has_reason() -> None:
    d = AdmissionDecision(
        kind=AdmissionDecisionKind.CAPACITY_REJECTED,
        batch_id="batch-001",
        queue_class=QueueClass.BACKGROUND,
        limiting_factor="browser_slots",
        reason="max_depth_exceeded",
    )
    assert d.kind is AdmissionDecisionKind.CAPACITY_REJECTED
    assert d.reason == "max_depth_exceeded"


def test_admission_decision_rejects_invalid_construction() -> None:
    with pytest.raises(DomainValidationError):
        AdmissionDecision(
            kind="bad",  # type: ignore[arg-type]
            batch_id="b",
            queue_class=QueueClass.BACKGROUND,
            limiting_factor=None,
            reason=None,
        )
    with pytest.raises(DomainValidationError):
        AdmissionDecision(
            kind=AdmissionDecisionKind.ADMITTED,
            batch_id="",
            queue_class=QueueClass.BACKGROUND,
            limiting_factor=None,
            reason=None,
        )
    with pytest.raises(DomainValidationError):
        AdmissionDecision(
            kind=AdmissionDecisionKind.ADMITTED,
            batch_id="b",
            queue_class="bad",  # type: ignore[arg-type]
            limiting_factor=None,
            reason=None,
        )
