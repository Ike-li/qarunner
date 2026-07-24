"""SUITE-PERSIST unit: SuiteGateway port + Fake wiring."""

from datetime import UTC, datetime

import pytest
from tests.fakes.greenfield.suite_gateway import InMemorySuiteGateway

from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.suite import SuiteGateway, SuiteMutationSnapshot
from qarunner.domain import Suite, VersionConflict, canonical_digest

REGISTERED_AT = datetime(2026, 7, 23, 16, tzinfo=UTC)
UPDATED_AT = datetime(2026, 7, 23, 17, tzinfo=UTC)
RETIRED_AT = datetime(2026, 7, 23, 18, tzinfo=UTC)


def _digest(label: str):
    return canonical_digest(
        schema_version="qep.test-suite-revision.v1",
        payload={"label": label},
    )


def _registered() -> Suite:
    return Suite.register(
        suite_id="suite-001",
        project_id="project-001",
        name="shop-regression",
        revision_id="suite-revision-001",
        source_spec_digest=_digest("source-v1"),
        config_digest=_digest("config-v1"),
        framework="pytest",
        resource_profile_id="profile-default",
        registered_at=REGISTERED_AT,
    )


def test_in_memory_gateway_satisfies_the_port() -> None:
    assert isinstance(InMemorySuiteGateway(), SuiteGateway)


def test_snapshot_rejects_a_non_suite() -> None:
    with pytest.raises(PortContractError):
        SuiteMutationSnapshot(suite=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_register_and_get_round_trip() -> None:
    gateway = InMemorySuiteGateway()
    suite = _registered()
    result = await gateway.register(suite=suite)
    assert result.replayed is False
    snapshot = await gateway.get_suite_for_update(suite_id="suite-001")
    assert snapshot.suite is suite
    assert snapshot.version == 0


@pytest.mark.asyncio
async def test_register_exact_replay() -> None:
    gateway = InMemorySuiteGateway()
    suite = _registered()
    await gateway.register(suite=suite)
    replay = await gateway.register(suite=suite)
    assert replay.replayed is True


@pytest.mark.asyncio
async def test_publish_revision_and_retire() -> None:
    gateway = InMemorySuiteGateway()
    suite = _registered()
    await gateway.register(suite=suite)
    snapshot = await gateway.get_suite_for_update(suite_id="suite-001")
    updated = snapshot.suite.publish_revision(
        revision_id="suite-revision-002",
        source_spec_digest=_digest("source-v2"),
        config_digest=_digest("config-v2"),
        framework="pytest",
        resource_profile_id="profile-default",
        created_at=UPDATED_AT,
        expected_version=snapshot.version,
    )
    published = await gateway.publish_revision(suite=updated, expected=snapshot)
    assert published.replayed is False
    assert published.value.version == 1

    snapshot2 = await gateway.get_suite_for_update(suite_id="suite-001")
    retired = snapshot2.suite.retire(retired_at=RETIRED_AT, expected_version=snapshot2.version)
    result = await gateway.publish_retire(suite=retired, expected=snapshot2)
    assert result.value.accepts_new_batch is False


@pytest.mark.asyncio
async def test_publish_rejects_stale_snapshot() -> None:
    gateway = InMemorySuiteGateway()
    suite = _registered()
    await gateway.register(suite=suite)
    snapshot = await gateway.get_suite_for_update(suite_id="suite-001")
    updated = snapshot.suite.publish_revision(
        revision_id="suite-revision-002",
        source_spec_digest=_digest("source-v2"),
        config_digest=_digest("config-v2"),
        framework="pytest",
        resource_profile_id="profile-default",
        created_at=UPDATED_AT,
        expected_version=snapshot.version,
    )
    await gateway.publish_revision(suite=updated, expected=snapshot)
    with pytest.raises(VersionConflict):
        await gateway.publish_revision(suite=updated, expected=snapshot)
