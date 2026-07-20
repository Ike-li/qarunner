"""Lifecycle coverage for the PostgreSQL aggregate application UoW."""

from typing import cast

import asyncpg
import pytest

from qarunner.adapters.postgres_application_uow import PostgresApplicationUnitOfWork
from qarunner.application.ports.common import PortContractError
from tests.fakes.greenfield.postgres_transactions import (
    CommitFailingPool,
    RecordingPool,
    StartFailingPool,
    TransactionFactoryFailingPool,
)


@pytest.mark.asyncio
async def test_unit_of_work_enforces_its_one_shot_lifecycle() -> None:
    pool = RecordingPool()
    unit_of_work = PostgresApplicationUnitOfWork(
        cast(asyncpg.Pool, pool),
        fact_codecs={},
    )

    with pytest.raises(PortContractError, match="not_active"):
        _ = unit_of_work.facts
    with pytest.raises(PortContractError, match="not_active"):
        await unit_of_work.rollback()

    await unit_of_work.__aenter__()
    with pytest.raises(PortContractError, match="already_active"):
        await unit_of_work.__aenter__()
    await unit_of_work.rollback()
    await unit_of_work.rollback()

    assert (pool.acquires, pool.releases) == (1, 1)
    assert pool.connection.transaction_value.rollbacks == 1
    with pytest.raises(PortContractError, match="closed"):
        _ = unit_of_work.audit
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.commit()


@pytest.mark.asyncio
async def test_start_failure_releases_the_connection_and_closes() -> None:
    failure = RuntimeError("transaction start unavailable")
    pool = StartFailingPool(failure)
    unit_of_work = PostgresApplicationUnitOfWork(
        cast(asyncpg.Pool, pool),
        fact_codecs={},
    )

    with pytest.raises(RuntimeError, match="transaction start unavailable"):
        await unit_of_work.__aenter__()

    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()


@pytest.mark.asyncio
async def test_transaction_factory_failure_releases_the_connection_and_closes() -> None:
    failure = RuntimeError("transaction factory unavailable")
    pool = TransactionFactoryFailingPool(failure)
    unit_of_work = PostgresApplicationUnitOfWork(
        cast(asyncpg.Pool, pool),
        fact_codecs={},
    )

    with pytest.raises(RuntimeError, match="transaction factory unavailable"):
        await unit_of_work.__aenter__()

    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()


@pytest.mark.asyncio
async def test_body_exception_rolls_back_releases_and_propagates() -> None:
    pool = RecordingPool()
    unit_of_work = PostgresApplicationUnitOfWork(
        cast(asyncpg.Pool, pool),
        fact_codecs={},
    )

    with pytest.raises(ValueError, match="application failure"):
        async with unit_of_work:
            raise ValueError("application failure")

    assert pool.connection.transaction_value.rollbacks == 1
    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        _ = unit_of_work.evidence


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_releases_and_closes() -> None:
    failure = RuntimeError("transaction commit unavailable")
    pool = CommitFailingPool(failure)
    unit_of_work = PostgresApplicationUnitOfWork(
        cast(asyncpg.Pool, pool),
        fact_codecs={},
    )
    await unit_of_work.__aenter__()

    with pytest.raises(RuntimeError, match="transaction commit unavailable"):
        await unit_of_work.commit()

    transaction = pool.connection.transaction_value
    assert (transaction.commits, transaction.rollbacks) == (1, 1)
    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()
