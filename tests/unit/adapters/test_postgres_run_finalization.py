"""Lifecycle failures for the PostgreSQL Run-finalization unit of work."""

from typing import cast

import asyncpg
import pytest

from qarunner.adapters.postgres_run_finalization import PostgresRunFinalizationUnitOfWork
from qarunner.application.ports.common import PortContractError
from tests.fakes.greenfield.postgres_transactions import StartFailingPool


@pytest.mark.asyncio
async def test_transaction_start_failure_releases_connection_and_closes_unit_of_work() -> None:
    failure = RuntimeError("transaction start unavailable")
    pool = StartFailingPool(failure)
    unit_of_work = PostgresRunFinalizationUnitOfWork(cast(asyncpg.Pool, pool))

    with pytest.raises(RuntimeError, match="transaction start unavailable"):
        await unit_of_work.__aenter__()

    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()
