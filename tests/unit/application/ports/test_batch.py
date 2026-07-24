"""BATCH-IDEM unit: BatchGateway port + Fake wiring."""

from datetime import UTC, datetime

import pytest
from tests.fakes.greenfield.batch_gateway import InMemoryBatchGateway

from qarunner.application.ports.batch import BatchGateway
from qarunner.application.ports.common import PortContractError
from qarunner.domain import Batch, IdempotencyConflict, Suite, SuiteConflict, canonical_digest

CREATED_AT = datetime(2026, 7, 23, 19, tzinfo=UTC)
REGISTERED_AT = datetime(2026, 7, 23, 16, tzinfo=UTC)
RETIRED_AT = datetime(2026, 7, 23, 18, tzinfo=UTC)


def _digest(label: str):
    return canonical_digest(
        schema_version="qep.test-batch-request.v1",
        payload={"label": label},
    )


def _suite(*, retired: bool = False) -> Suite:
    suite = Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "source-v1"},
        ),
        config_digest=canonical_digest(
            schema_version="qep.test-suite-revision.v1",
            payload={"label": "config-v1"},
        ),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )
    if retired:
        suite = suite.retire(retired_at=RETIRED_AT, expected_version=suite.version)
    return suite


def _open_batch(*, suite: Suite, key: str = "idem-001", label: str = "create-v1") -> Batch:
    return Batch.open_for_suite(
        batch_id=f"batch-{key}",
        suite=suite,
        request_digest=_digest(label),
        idempotency_scope="project:project-001:batch-create",
        idempotency_key=key,
        created_at=CREATED_AT,
    )


def test_in_memory_gateway_satisfies_the_port() -> None:
    assert isinstance(InMemoryBatchGateway(), BatchGateway)


@pytest.mark.asyncio
async def test_create_and_get_round_trip() -> None:
    gateway = InMemoryBatchGateway()
    suite = _suite()
    gateway.seed_suite(suite)
    batch = _open_batch(suite=suite)
    result = await gateway.create(batch=batch)
    assert result.replayed is False
    loaded = await gateway.get_batch(batch_id=batch.id)
    assert loaded is batch
    by_key = await gateway.get_by_idempotency(
        idempotency_scope=batch.idempotency_scope,  # type: ignore[arg-type]
        idempotency_key=batch.idempotency_key,  # type: ignore[arg-type]
    )
    assert by_key is batch


@pytest.mark.asyncio
async def test_create_exact_replay() -> None:
    gateway = InMemoryBatchGateway()
    suite = _suite()
    gateway.seed_suite(suite)
    batch = _open_batch(suite=suite)
    first = await gateway.create(batch=batch)
    second = await gateway.create(batch=batch)
    assert first.replayed is False
    assert second.replayed is True
    assert second.value.id == batch.id


@pytest.mark.asyncio
async def test_create_digest_conflict_is_stable() -> None:
    gateway = InMemoryBatchGateway()
    suite = _suite()
    gateway.seed_suite(suite)
    first = _open_batch(suite=suite, label="create-v1")
    await gateway.create(batch=first)
    conflicting = Batch.open_for_suite(
        batch_id="batch-other",
        suite=suite,
        request_digest=_digest("create-v2"),
        idempotency_scope="project:project-001:batch-create",
        idempotency_key="idem-001",
        created_at=CREATED_AT,
    )
    with pytest.raises(IdempotencyConflict) as caught:
        await gateway.create(batch=conflicting)
    assert caught.value.scope == "project:project-001:batch-create"
    assert caught.value.key == "idem-001"


@pytest.mark.asyncio
async def test_create_rejects_retired_suite() -> None:
    gateway = InMemoryBatchGateway()
    active = _suite()
    gateway.seed_suite(active)
    # Domain open requires active; seed retired after open identity built.
    batch = _open_batch(suite=active)
    gateway.seed_suite(_suite(retired=True))
    with pytest.raises(SuiteConflict) as caught:
        await gateway.create(batch=batch)
    assert caught.value.reason == "suite_retired"


@pytest.mark.asyncio
async def test_create_rejects_lifecycle_only_batch() -> None:
    gateway = InMemoryBatchGateway()
    with pytest.raises(PortContractError) as caught:
        await gateway.create(batch=Batch.create(batch_id="batch-naked"))
    assert caught.value.reason == "missing"
