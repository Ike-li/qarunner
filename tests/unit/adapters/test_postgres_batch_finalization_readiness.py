"""Lifecycle contracts for the PostgreSQL Batch-readiness unit of work."""

from typing import cast

import asyncpg
import pytest

from qarunner.adapters.postgres_batch_finalization_readiness import (
    PostgresBatchFinalizationReadinessUnitOfWork,
    _stored_projection,
)
from qarunner.application.ports.batch_finalization_readiness import (
    BatchFinalizationReadinessGateway,
)
from qarunner.application.ports.common import PortContractError
from tests.fakes.greenfield.postgres_transactions import RecordingPool, StartFailingPool
from tests.unit.application.test_begin_batch_finalization import _values


@pytest.mark.asyncio
async def test_uow_implements_gateway_and_releases_on_transaction_start_failure() -> None:
    assert issubclass(
        PostgresBatchFinalizationReadinessUnitOfWork, BatchFinalizationReadinessGateway
    )
    failure = RuntimeError("transaction start unavailable")
    pool = StartFailingPool(failure)
    unit_of_work = PostgresBatchFinalizationReadinessUnitOfWork(
        cast(asyncpg.Pool, pool),
        authority=_values()[1],
    )

    with pytest.raises(RuntimeError, match="transaction start unavailable"):
        await unit_of_work.__aenter__()

    assert (pool.acquires, pool.releases) == (1, 1)
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()


@pytest.mark.asyncio
async def test_lifecycle_is_one_shot_and_rolls_back_on_context_error() -> None:
    pool = RecordingPool()
    unit_of_work = PostgresBatchFinalizationReadinessUnitOfWork(
        cast(asyncpg.Pool, pool),
        authority=_values()[1],
    )

    with pytest.raises(PortContractError, match="not_active"):
        await unit_of_work.require_readiness_authority(batch_id="batch-1")
    await unit_of_work.__aenter__()
    with pytest.raises(PortContractError, match="already_active"):
        await unit_of_work.__aenter__()
    await unit_of_work.__aexit__(RuntimeError, RuntimeError("rollback"), None)

    assert pool.connection.transaction_value.rollbacks == 1
    assert pool.connection.transaction_value.commits == 0
    with pytest.raises(PortContractError, match="closed"):
        await unit_of_work.__aenter__()
    with pytest.raises(PortContractError, match="not_active"):
        await unit_of_work.__aexit__(None, None, None)


@pytest.mark.parametrize("kind", ["digest", "payload"])
def test_stored_projection_rejects_integrity_corruption(kind: str) -> None:
    import json

    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
    from qarunner.domain import canonical_digest

    payload = {"batch_id": "batch-1", "source_batch_version": 9}
    digest = canonical_digest(
        schema_version="qep.batch-finalization-readiness.v1", payload=payload
    )
    row = {
        "batch_id": "batch-1",
        "ref": "readiness-1",
        "finalization_readiness_ref": "readiness-1",
        "source_batch_version": 9,
        "batch_version": 10,
        "state": "finalizing",
        "version": 10,
        "readiness_digest": (
            "f" * 64 if kind == "digest" else digest.value.removeprefix("sha256:")
        ),
        "payload": "[]" if kind == "payload" else json.dumps(payload),
    }
    with pytest.raises(AuthorityStateConflict, match="stored_integrity"):
        _stored_projection(batch_id="batch-1", row=row)
