"""Constructor validation for the PostgreSQL outbox publisher."""

from typing import cast

import asyncpg
import pytest

from qarunner.adapters.postgres_outbox_publisher import PostgresOutboxPublisher
from tests.fakes.greenfield.outbox_sink import InMemoryOutboxSink


def test_rejects_a_non_positive_max_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        PostgresOutboxPublisher(
            cast(asyncpg.Pool, object()),
            sink=InMemoryOutboxSink(),
            max_attempts=0,
            retry_backoff_seconds=1,
        )


def test_rejects_a_negative_retry_backoff() -> None:
    with pytest.raises(ValueError, match="retry_backoff_seconds"):
        PostgresOutboxPublisher(
            cast(asyncpg.Pool, object()),
            sink=InMemoryOutboxSink(),
            max_attempts=1,
            retry_backoff_seconds=-1,
        )
