"""Lifecycle failures for the PostgreSQL Batch-finalization unit of work."""

from typing import cast

import asyncpg
import pytest

from qarunner.adapters.postgres_batch_finalization import (
    PostgresBatchFinalizationUnitOfWork,
)
from qarunner.application.ports.batch_finalization import (
    BatchFinalizationSourceSnapshot,
    FinalizeBatchAuthority,
)
from qarunner.application.ports.common import PortContractError
from qarunner.domain import BatchFinalizationBasis, canonical_digest
from tests.fakes.greenfield.postgres_transactions import StartFailingPool
from tests.unit.domain.test_batch_finalization_basis import _basis_inputs


@pytest.mark.asyncio
async def test_transaction_start_failure_releases_connection_and_closes_unit_of_work() -> None:
    failure = RuntimeError("transaction start unavailable")
    pool = StartFailingPool(failure)
    unit_of_work = PostgresBatchFinalizationUnitOfWork(
        cast(asyncpg.Pool, pool),
        authority=_authority(),
    )

    with pytest.raises(RuntimeError, match="transaction start unavailable"):
        await unit_of_work.__aenter__()

    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()


def _authority() -> FinalizeBatchAuthority:
    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    return FinalizeBatchAuthority(
        source_snapshot=BatchFinalizationSourceSnapshot.from_basis(candidate),
        authority_digest=canonical_digest(
            schema_version="qep.test-batch-finalization-authority.v1",
            payload={"batch_id": candidate.batch_id},
        ),
        write_epoch=1,
    )
