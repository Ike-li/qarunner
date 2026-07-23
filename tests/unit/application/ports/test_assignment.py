"""ASGN-DESIGN: the AssignmentGateway port shape + its in-memory Fake are coherent.

Real CAS / one-active-per-run / fence / replay live in the PostgreSQL sub-gates; here we
only prove the port Protocol and the Fake wiring the offer/claim/commit-start/close commands
will drive.
"""

import pytest
from tests.fakes.greenfield.assignment_gateway import InMemoryAssignmentGateway

from qarunner.application.ports.assignment import (
    AssignmentGateway,
    AssignmentMutationSnapshot,
)
from qarunner.application.ports.common import PortContractError
from qarunner.domain import Run
from qarunner.domain.run import RunState


def _queued() -> Run:
    return Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)


def test_in_memory_gateway_satisfies_the_port() -> None:
    assert isinstance(InMemoryAssignmentGateway(), AssignmentGateway)


def test_snapshot_rejects_a_non_run() -> None:
    with pytest.raises(PortContractError):
        AssignmentMutationSnapshot(run=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_for_update_returns_the_seeded_run_and_version() -> None:
    run = _queued()
    gateway = InMemoryAssignmentGateway()
    gateway.seed(run)

    snapshot = await gateway.get_run_for_update(run_id="run-001")

    assert snapshot.run is run
    assert snapshot.run_id == "run-001"
    assert snapshot.version == run.version


@pytest.mark.asyncio
async def test_get_for_update_rejects_an_unknown_run() -> None:
    with pytest.raises(PortContractError):
        await InMemoryAssignmentGateway().get_run_for_update(run_id="missing")


@pytest.mark.asyncio
async def test_publish_offer_persists_and_returns_not_replayed() -> None:
    run = _queued()
    gateway = InMemoryAssignmentGateway()
    gateway.seed(run)
    snapshot = await gateway.get_run_for_update(run_id="run-001")

    result = await gateway.publish_offer(offered=run, expected=snapshot)

    assert result.replayed is False
    assert result.value is run
    assert (await gateway.get_run_for_update(run_id="run-001")).run is run


@pytest.mark.asyncio
async def test_publish_rejects_a_stale_snapshot() -> None:
    draft = Run.create(run_id="run-001")
    queued = draft.transition(RunState.QUEUED, expected_version=0)
    gateway = InMemoryAssignmentGateway()
    gateway.seed(queued)

    with pytest.raises(PortContractError):
        await gateway.publish_offer(offered=queued, expected=AssignmentMutationSnapshot(run=draft))
