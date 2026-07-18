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
    BatchClosureAuthority,
)
from qarunner.application.ports.common import PortContractError
from qarunner.domain import (
    BatchCancellationScope,
    BatchCancellationScopeKind,
    CancellationSource,
    canonical_digest,
)
from tests.fakes.greenfield.postgres_transactions import RecordingPool, StartFailingPool


@pytest.mark.parametrize("mode", ["missing", "ambiguous"])
def test_constructor_requires_exactly_one_authority_mode(mode: str) -> None:
    values: dict[str, object] = {}
    if mode == "ambiguous":
        values = {
            "authority": _authority(),
            "closure_authority": _closure_authority(),
            "closure_reconciler_id": "reconciler-001",
            "closure_checked_at": datetime(2026, 7, 18, 10, tzinfo=UTC),
        }

    with pytest.raises(PortContractError) as invalid:
        PostgresBatchCancellationUnitOfWork(
            cast(asyncpg.Pool, object()),
            **values,  # type: ignore[arg-type]
        )

    assert invalid.value.reason == "authority_mode_invalid"


@pytest.mark.parametrize("mode", ["cancel_with_closure_fields", "incomplete_closure"])
def test_constructor_requires_a_complete_closure_authority_binding(mode: str) -> None:
    values: dict[str, object]
    if mode == "cancel_with_closure_fields":
        values = {
            "authority": _authority(),
            "closure_reconciler_id": "reconciler-001",
            "closure_checked_at": datetime(2026, 7, 18, 10, tzinfo=UTC),
        }
    else:
        values = {"closure_authority": _closure_authority()}

    with pytest.raises(PortContractError) as invalid:
        PostgresBatchCancellationUnitOfWork(
            cast(asyncpg.Pool, object()),
            **values,  # type: ignore[arg-type]
        )

    assert invalid.value.reason == "closure_authority_binding_invalid"


@pytest.mark.asyncio
async def test_each_authority_mode_rejects_the_other_mode_port() -> None:
    closure_pool = RecordingPool()
    async with PostgresBatchCancellationUnitOfWork(
        cast(asyncpg.Pool, closure_pool),
        closure_authority=_closure_authority(),
        closure_reconciler_id="reconciler-001",
        closure_checked_at=datetime(2026, 7, 18, 10, tzinfo=UTC),
    ) as unit_of_work:
        with pytest.raises(PortContractError) as cancel_port:
            await unit_of_work.require_cancel_authority(batch_id="batch-001")

    cancel_pool = RecordingPool()
    async with PostgresBatchCancellationUnitOfWork(
        cast(asyncpg.Pool, cancel_pool),
        authority=_authority(),
    ) as unit_of_work:
        with pytest.raises(PortContractError) as closure_port:
            await unit_of_work.require_closure_authority(
                batch_id="batch-001",
                reconciler_id="reconciler-001",
                closure_epoch=1,
            )

    assert cancel_port.value.reason == "cancel_authority_not_configured"
    assert closure_port.value.reason == "closure_authority_not_configured"
    assert closure_pool.connection.transaction_value.commits == 1
    assert cancel_pool.connection.transaction_value.commits == 1


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


def _closure_authority() -> BatchClosureAuthority:
    cancel_authority = _authority()
    return BatchClosureAuthority(
        batch_id=cancel_authority.batch_id,
        project_id=cancel_authority.project_id,
        suite_revision_id=cancel_authority.suite_revision_id,
        source_batch_version=4,
        authority_digest=canonical_digest(
            schema_version="qep.test-closure-authority.v1",
            payload={"batch_id": "batch-001", "write_epoch": 1},
        ),
        scope=cancel_authority.scope,
        projection=cancel_authority.projection,
        write_epoch=1,
    )
