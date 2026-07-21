"""Real PostgreSQL transaction coverage for the outbox publisher."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from tests.fakes.greenfield.outbox_sink import InMemoryOutboxSink
from tests.integration.migration_operator import run_migration_operator

from qarunner.adapters.postgres_outbox_publisher import (
    OutboxPublicationReport,
    PostgresOutboxPublisher,
)
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.domain.digest import Digest


@pytest.fixture
async def outbox_store(monkeypatch: pytest.MonkeyPatch) -> PostgresStore:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "postgres-outbox-publisher-admin-password-at-least-32-chars",
    )
    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    migration = await asyncio.to_thread(
        run_migration_operator,
        database_url,
        schema,
        "upgrade",
        "head",
    )
    assert migration.returncode == 0, migration.stderr
    store = PostgresStore(database_url, schema=schema)
    try:
        await store.initialize()
        yield store
    finally:
        await store.close()
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()


async def test_concurrent_claim_leases_each_pending_row_to_exactly_one_claimer(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    await _seed_pending_events(pool, count=20)
    sink = InMemoryOutboxSink()
    publishers = [
        PostgresOutboxPublisher(pool, sink=sink, max_attempts=3, retry_backoff_seconds=30)
        for _ in range(5)
    ]
    start = asyncio.Barrier(5)

    async def run(publisher: PostgresOutboxPublisher):
        await start.wait()
        return await publisher.publish_pending(limit=10)

    reports = await asyncio.gather(*(run(publisher) for publisher in publishers))

    all_published = [event_id for report in reports for event_id in report.published]
    expected_ids = [f"event-{i:02d}" for i in range(20)]
    assert sorted(all_published) == expected_ids
    assert len(set(all_published)) == 20
    assert sorted(event.event_id for event in sink.delivered) == expected_ids
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT status, count(*) AS count FROM qep_outbox_events GROUP BY status"
        )
    assert {row["status"]: row["count"] for row in rows} == {"published": 20}


async def test_successful_delivery_passes_the_committed_event_content_to_the_sink(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    await _seed_pending_events(pool, count=1)
    sink = InMemoryOutboxSink()
    publisher = PostgresOutboxPublisher(pool, sink=sink, max_attempts=3, retry_backoff_seconds=30)

    report = await publisher.publish_pending(limit=10)

    assert report == OutboxPublicationReport(published=("event-00",), retried=(), quarantined=())
    assert len(sink.delivered) == 1
    delivered = sink.delivered[0]
    assert delivered.event_id == "event-00"
    assert delivered.aggregate_type == "test_aggregate"
    assert delivered.aggregate_id == "aggregate-00"
    assert delivered.event_type == "test.event.v1"
    assert delivered.payload_digest == Digest(f"sha256:{'a' * 64}")
    assert delivered.payload == {"index": 0}
    assert delivered.delivery_attempt == 1


async def test_transient_delivery_failure_retries_with_backoff_then_succeeds(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    await _seed_pending_events(pool, count=1)
    sink = InMemoryOutboxSink(fail_until_attempt={"event-00": 3})
    publisher = PostgresOutboxPublisher(pool, sink=sink, max_attempts=5, retry_backoff_seconds=60)

    first = await publisher.publish_pending(limit=10)
    assert first == OutboxPublicationReport(published=(), retried=("event-00",), quarantined=())
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, attempts, last_error, available_at FROM qep_outbox_events"
        )
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "outbox_sink_transient_failure:event-00:1" in row["last_error"]
    assert row["available_at"] > datetime(2026, 7, 21, 6, tzinfo=UTC)

    # Not yet available: retrying immediately must not reclaim it early.
    immediate_retry = await publisher.publish_pending(limit=10)
    assert immediate_retry == OutboxPublicationReport(published=(), retried=(), quarantined=())

    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_outbox_events SET available_at = transaction_timestamp()"
        )
    second = await publisher.publish_pending(limit=10)
    assert second == OutboxPublicationReport(published=(), retried=("event-00",), quarantined=())

    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_outbox_events SET available_at = transaction_timestamp()"
        )
    third = await publisher.publish_pending(limit=10)
    assert third == OutboxPublicationReport(published=("event-00",), retried=(), quarantined=())
    assert [event.delivery_attempt for event in sink.delivered] == [3]


async def test_delivery_failure_past_max_attempts_quarantines_the_event(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    await _seed_pending_events(pool, count=1)
    sink = InMemoryOutboxSink(fail_event_ids=frozenset({"event-00"}))
    publisher = PostgresOutboxPublisher(pool, sink=sink, max_attempts=2, retry_backoff_seconds=0)

    first = await publisher.publish_pending(limit=10)
    assert first == OutboxPublicationReport(published=(), retried=("event-00",), quarantined=())

    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE qep_outbox_events SET available_at = transaction_timestamp()"
        )
    second = await publisher.publish_pending(limit=10)

    assert second == OutboxPublicationReport(published=(), retried=(), quarantined=("event-00",))
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, attempts, last_error FROM qep_outbox_events"
        )
    assert row["status"] == "quarantined"
    assert row["attempts"] == 2
    assert "outbox_sink_permanent_failure:event-00" in row["last_error"]
    # A quarantined row must never be claimed again.
    third = await publisher.publish_pending(limit=10)
    assert third == OutboxPublicationReport(published=(), retried=(), quarantined=())


async def test_not_yet_available_pending_rows_are_not_claimed(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    await _seed_pending_events(pool, count=1, available_at=datetime(2099, 1, 1, tzinfo=UTC))
    sink = InMemoryOutboxSink()
    publisher = PostgresOutboxPublisher(pool, sink=sink, max_attempts=3, retry_backoff_seconds=30)

    report = await publisher.publish_pending(limit=10)

    assert report == OutboxPublicationReport(published=(), retried=(), quarantined=())
    assert sink.delivered == []
    async with pool.acquire() as connection:
        status = await connection.fetchval("SELECT status FROM qep_outbox_events")
    assert status == "pending"


async def _seed_pending_events(
    pool: asyncpg.Pool,
    *,
    count: int,
    available_at: datetime | None = None,
) -> None:
    now = available_at or datetime(2026, 7, 21, 6, tzinfo=UTC)
    async with pool.acquire() as connection:
        for index in range(count):
            await connection.execute(
                """
                INSERT INTO qep_outbox_events (
                    id, event_id, aggregate_type, aggregate_id, event_type,
                    payload_digest, payload, status, available_at, attempts, created_at
                ) VALUES (
                    $1, $1, 'test_aggregate', $2, 'test.event.v1', $3, $4, 'pending', $5, 0, $5
                )
                """,
                f"event-{index:02d}",
                f"aggregate-{index:02d}",
                "a" * 64,
                json.dumps({"index": index}),
                now,
            )
