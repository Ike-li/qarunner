"""PostgreSQL outbox publisher: concurrent-safe claim, delivery, retry, and quarantine."""

from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg

from qarunner.application.ports.outbox import OutboxDeliveryEvent, OutboxSink
from qarunner.domain.digest import Digest


@dataclass(frozen=True, slots=True)
class OutboxPublicationReport:
    """Event IDs grouped by what happened to them in one publish_pending call."""

    published: tuple[str, ...]
    retried: tuple[str, ...]
    quarantined: tuple[str, ...]


class PostgresOutboxPublisher:
    """Lease pending outbox rows, deliver through a Sink, and record the result.

    Claiming is one short transaction with no external I/O held under lock. Delivery happens
    outside any transaction, matching the boundary-review rule that no writer may perform
    external I/O while holding a Batch/aggregate lock. Recording the delivery result is a
    second, independent short transaction, so a crash between delivery and recording leaves the
    row leased for the repair scan to reclaim -- at-least-once delivery, requiring an idempotent
    sink/consumer, exactly as the outbox contract specifies.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        sink: OutboxSink,
        max_attempts: int,
        retry_backoff_seconds: int,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        self._pool = pool
        self._sink = sink
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds

    async def publish_pending(self, *, limit: int) -> OutboxPublicationReport:
        claimed = await self._claim_batch(limit=limit)
        published: list[str] = []
        retried: list[str] = []
        quarantined: list[str] = []
        for row in claimed:
            event = _to_delivery_event(row)
            try:
                await self._sink.deliver(event)
            except Exception as error:  # noqa: BLE001 - sink failures are arbitrary external I/O
                outcome = await self._record_delivery_failure(row, error=error)
                (quarantined if outcome == "quarantined" else retried).append(row["event_id"])
                continue
            await self._record_delivery_success(row)
            published.append(row["event_id"])
        return OutboxPublicationReport(
            published=tuple(published),
            retried=tuple(retried),
            quarantined=tuple(quarantined),
        )

    async def repair_stuck_leases(self, *, lease_timeout_seconds: int) -> int:
        """Return leases stuck past a bounded timeout to pending; idempotent and safe to poll.

        A crash between claim and result-recording leaves a row `leased` forever otherwise;
        returning it to `pending` (with `available_at` reset to now) never touches domain facts,
        matching the outbox contract's repair-scan requirement.
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                UPDATE qep_outbox_events
                SET status = 'pending',
                    available_at = transaction_timestamp()
                WHERE status = 'leased'
                  AND leased_at < transaction_timestamp() - make_interval(secs => $1)
                RETURNING id
                """,
                lease_timeout_seconds,
            )
        return len(rows)

    async def _claim_batch(self, *, limit: int) -> list[asyncpg.Record]:
        async with self._pool.acquire() as connection:
            return await connection.fetch(
                """
                UPDATE qep_outbox_events
                SET status = 'leased',
                    leased_at = transaction_timestamp()
                WHERE id IN (
                    SELECT id
                    FROM qep_outbox_events
                    WHERE status = 'pending'
                      AND available_at <= transaction_timestamp()
                    ORDER BY available_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING
                    id, event_id, aggregate_type, aggregate_id, event_type,
                    payload_digest, payload, attempts
                """,
                limit,
            )

    async def _record_delivery_success(self, row: asyncpg.Record) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE qep_outbox_events
                SET status = 'published'
                WHERE id = $1
                  AND status = 'leased'
                """,
                row["id"],
            )

    async def _record_delivery_failure(self, row: asyncpg.Record, *, error: Exception) -> str:
        attempts = row["attempts"] + 1
        last_error = str(error)[:2000]
        async with self._pool.acquire() as connection:
            if attempts >= self._max_attempts:
                await connection.execute(
                    """
                    UPDATE qep_outbox_events
                    SET status = 'quarantined',
                        attempts = $2,
                        last_error = $3
                    WHERE id = $1
                      AND status = 'leased'
                    """,
                    row["id"],
                    attempts,
                    last_error,
                )
                return "quarantined"
            await connection.execute(
                """
                UPDATE qep_outbox_events
                SET status = 'pending',
                    attempts = $2,
                    last_error = $3,
                    available_at = transaction_timestamp() + make_interval(secs => $4)
                WHERE id = $1
                  AND status = 'leased'
                """,
                row["id"],
                attempts,
                last_error,
                self._retry_backoff_seconds,
            )
            return "retried"


def _to_delivery_event(row: asyncpg.Record) -> OutboxDeliveryEvent:
    return OutboxDeliveryEvent(
        event_id=row["event_id"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        event_type=row["event_type"],
        payload_digest=Digest(f"sha256:{row['payload_digest']}"),
        payload=json.loads(row["payload"]),
        delivery_attempt=row["attempts"] + 1,
    )
