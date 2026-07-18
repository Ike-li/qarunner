"""Lifecycle failures for the PostgreSQL Batch-cancellation unit of work."""

from datetime import UTC, datetime, timedelta
from typing import cast

import asyncpg
import pytest

from qarunner.adapters.postgres_batch_cancellation import (
    PostgresBatchCancellationUnitOfWork,
)
from qarunner.application.ports.batch_preexecution import (
    AuthorityProjectionStamp,
    BatchCancellationAuthority,
)
from qarunner.application.ports.common import PortContractError
from qarunner.domain import (
    BatchCancellationScope,
    BatchCancellationScopeKind,
    CancellationSource,
    canonical_digest,
)
from tests.fakes.greenfield.postgres_transactions import StartFailingPool


@pytest.mark.asyncio
async def test_transaction_start_failure_releases_connection_and_closes_unit_of_work() -> None:
    failure = RuntimeError("transaction start unavailable")
    pool = StartFailingPool(failure)
    unit_of_work = PostgresBatchCancellationUnitOfWork(
        cast(asyncpg.Pool, pool),
        authority=_authority(),
    )

    with pytest.raises(RuntimeError, match="transaction start unavailable"):
        await unit_of_work.__aenter__()

    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()


def _authority() -> BatchCancellationAuthority:
    recorded_at = datetime(2026, 7, 18, 10, tzinfo=UTC)
    return BatchCancellationAuthority(
        batch_id="batch-001",
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        actor_id="user-001",
        source=CancellationSource.USER_REQUEST,
        authorization_digest=canonical_digest(
            schema_version="qep.test-batch-authority.v1",
            payload={"batch_id": "batch-001"},
        ),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=canonical_digest(
                schema_version="qep.test-batch-scope.v1",
                payload={"batch_id": "batch-001"},
            ),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        projection=AuthorityProjectionStamp(
            source="local-authority-projection",
            projection_version=1,
            revocation_watermark=1,
            expires_at=recorded_at + timedelta(minutes=1),
        ),
        recorded_at=recorded_at,
    )
