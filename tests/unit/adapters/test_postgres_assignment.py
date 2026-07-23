"""Lifecycle failures for the PostgreSQL Assignment gateway."""

from typing import cast

import asyncpg
import pytest

from qarunner.adapters.postgres_assignment import PostgresAssignmentGateway
from qarunner.application.ports.common import PortContractError
from tests.fakes.greenfield.postgres_transactions import StartFailingPool

OFFER_TOKEN_HASH = "d" * 64


@pytest.mark.asyncio
async def test_transaction_start_failure_releases_connection_and_closes_gateway() -> None:
    failure = RuntimeError("transaction start unavailable")
    pool = StartFailingPool(failure)
    gateway = PostgresAssignmentGateway(
        cast(asyncpg.Pool, pool), offer_token_hash=OFFER_TOKEN_HASH
    )

    with pytest.raises(RuntimeError, match="transaction start unavailable"):
        await gateway.__aenter__()

    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await gateway.__aenter__()


@pytest.mark.asyncio
async def test_aexit_without_active_transaction_is_not_active() -> None:
    pool = StartFailingPool(RuntimeError("unused"))
    gateway = PostgresAssignmentGateway(
        cast(asyncpg.Pool, pool), offer_token_hash=OFFER_TOKEN_HASH
    )
    with pytest.raises(PortContractError, match="not_active"):
        await gateway.__aexit__(None, None, None)
