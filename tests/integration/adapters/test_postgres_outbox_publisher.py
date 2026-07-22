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


async def test_repair_scan_reclaims_a_lease_stuck_past_its_timeout(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    stuck_since = datetime(2026, 7, 21, 6, tzinfo=UTC)
    await _seed_leased_event(pool, event_id="event-00", leased_at=stuck_since)
    sink = InMemoryOutboxSink()
    publisher = PostgresOutboxPublisher(pool, sink=sink, max_attempts=3, retry_backoff_seconds=30)

    repaired = await publisher.repair_stuck_leases(lease_timeout_seconds=300)

    assert repaired == 1
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT status, available_at FROM qep_outbox_events WHERE event_id = 'event-00'"
        )
    assert row["status"] == "pending"
    assert row["available_at"] <= datetime.now(UTC)

    report = await publisher.publish_pending(limit=10)
    assert report == OutboxPublicationReport(published=("event-00",), retried=(), quarantined=())

    # Idempotent: nothing left to repair once the lease has been reclaimed and delivered.
    again = await publisher.repair_stuck_leases(lease_timeout_seconds=300)
    assert again == 0


async def test_repair_scan_leaves_a_fresh_lease_untouched(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    await _seed_leased_event(pool, event_id="event-00", leased_at=datetime.now(UTC))
    publisher = PostgresOutboxPublisher(
        pool, sink=InMemoryOutboxSink(), max_attempts=3, retry_backoff_seconds=30
    )

    repaired = await publisher.repair_stuck_leases(lease_timeout_seconds=300)

    assert repaired == 0
    async with pool.acquire() as connection:
        status = await connection.fetchval(
            "SELECT status FROM qep_outbox_events WHERE event_id = 'event-00'"
        )
    assert status == "leased"


async def test_publish_pending_drains_a_30k_backlog_with_a_poison_flood_at_bounded_cost(
    outbox_store: PostgresStore,
) -> None:
    pool = outbox_store._require_pool()
    normal_count = 29_700
    poison_ids = tuple(f"poison-{i:04d}" for i in range(300))
    total = normal_count + len(poison_ids)
    await _seed_backlog(pool, normal_count=normal_count, poison_ids=poison_ids)

    sink = InMemoryOutboxSink(fail_event_ids=frozenset(poison_ids))
    publisher = PostgresOutboxPublisher(pool, sink=sink, max_attempts=2, retry_backoff_seconds=0)
    batch_limit = 2_000
    # A pathologically unbounded implementation (one call draining the whole backlog, or one
    # call per row) would need either 1 or `total` calls; a bounded-batch implementation needs
    # roughly total/limit calls plus a small number of extra passes for poison-row retries. This
    # cap is generous but rules out both pathological shapes.
    max_calls = (total // batch_limit) + 5

    calls = 0
    total_published: list[str] = []
    total_retried: list[str] = []
    total_quarantined: list[str] = []
    while calls < max_calls:
        report = await publisher.publish_pending(limit=batch_limit)
        calls += 1
        touched = len(report.published) + len(report.retried) + len(report.quarantined)
        # The single load-bearing assertion: no call ever touches more than `limit` rows,
        # regardless of how large the remaining backlog is.
        assert touched <= batch_limit
        total_published.extend(report.published)
        total_retried.extend(report.retried)
        total_quarantined.extend(report.quarantined)
        if touched == 0:
            break

    assert calls < max_calls, "backlog did not drain within the expected bounded call budget"
    assert len(total_published) == normal_count
    assert set(total_quarantined) == set(poison_ids)
    assert len(total_quarantined) == len(poison_ids)

    async with pool.acquire() as connection:
        status_rows = await connection.fetch(
            "SELECT status, count(*) AS count FROM qep_outbox_events GROUP BY status"
        )
    status_counts = {row["status"]: row["count"] for row in status_rows}
    assert status_counts == {"published": normal_count, "quarantined": len(poison_ids)}

    async with pool.acquire() as connection:
        quarantined_attempts = await connection.fetch(
            "SELECT attempts FROM qep_outbox_events WHERE status = 'quarantined'"
        )
    # Every poison row must be quarantined at exactly max_attempts -- never fewer (premature
    # quarantine) and never more (a bug would let it loop past the configured limit).
    assert [row["attempts"] for row in quarantined_attempts] == [2] * len(poison_ids)

    # Poison rows must never re-enter the pending pool once quarantined.
    final = await publisher.publish_pending(limit=batch_limit)
    assert final == OutboxPublicationReport(published=(), retried=(), quarantined=())


async def _seed_backlog(
    pool: asyncpg.Pool,
    *,
    normal_count: int,
    poison_ids: tuple[str, ...],
) -> None:
    available_at = datetime(2026, 7, 21, 6, tzinfo=UTC)
    rows = [
        (
            f"bulk-{index:06d}",
            f"bulk-{index:06d}",
            "test_aggregate",
            f"aggregate-bulk-{index:06d}",
            "test.event.v1",
            "a" * 64,
            json.dumps({"index": index}),
            "pending",
            available_at,
            0,
            available_at,
        )
        for index in range(normal_count)
    ]
    rows.extend(
        (
            poison_id,
            poison_id,
            "test_aggregate",
            f"aggregate-{poison_id}",
            "test.event.v1",
            "b" * 64,
            json.dumps({"poison": True}),
            "pending",
            available_at,
            0,
            available_at,
        )
        for poison_id in poison_ids
    )
    async with pool.acquire() as connection:
        await connection.executemany(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, status, available_at, attempts, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            rows,
        )


async def _seed_leased_event(
    pool: asyncpg.Pool,
    *,
    event_id: str,
    leased_at: datetime,
) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO qep_outbox_events (
                id, event_id, aggregate_type, aggregate_id, event_type,
                payload_digest, payload, status, available_at, attempts, leased_at, created_at
            ) VALUES (
                $1, $1, 'test_aggregate', $2, 'test.event.v1', $3, $4, 'leased', $5, 0, $5, $5
            )
            """,
            event_id,
            f"aggregate-{event_id}",
            "a" * 64,
            json.dumps({"event_id": event_id}),
            leased_at,
        )


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
