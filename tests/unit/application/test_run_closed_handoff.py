"""001G immutable Run-closed handoff contract."""

from dataclasses import replace

import pytest
from tests.fakes.greenfield.run_closed_handoff import (
    InMemoryRunClosedFactProvider,
    InMemoryRunClosedHandoffConsumer,
)
from tests.unit.application.test_finalize_run import command, gateway
from tests.unit.domain.test_run_finalization_basis import d

from qarunner.application.ports.run_closed_handoff import (
    RunClosedDeliveryMetadata,
    RunClosedHandoffBlocked,
    RunClosedHandoffConflict,
)
from qarunner.application.run_closed_handoff import build_run_closed_handoff
from qarunner.application.run_finalization import FinalizeRun


def test_public_facades_and_runtime_protocols():
    from qarunner.application import RunClosedHandoff
    from qarunner.application import build_run_closed_handoff as public_builder
    from qarunner.application.ports import RunClosedFactProvider, RunClosedHandoffConsumer

    assert RunClosedHandoff and public_builder is build_run_closed_handoff
    assert isinstance(InMemoryRunClosedHandoffConsumer(), RunClosedHandoffConsumer)
    assert isinstance(InMemoryRunClosedFactProvider(gateway()), RunClosedFactProvider)


@pytest.mark.asyncio
async def test_finalization_atomically_publishes_canonical_handoff_and_provider_facts():
    fake = gateway()
    result = await FinalizeRun(gateway=fake).execute(command())
    handoff = next(iter(fake.run_closed_handoffs.values()))
    assert handoff.run_basis_digest == result.projection.basis_digest
    provider = InMemoryRunClosedFactProvider(fake)
    basis = await provider.get_basis(
        run_id=handoff.run_id,
        source_run_version=handoff.source_run_version,
        basis_digest=handoff.run_basis_digest,
    )
    resolved = await provider.get_resolution_set(digest=handoff.item_resolution_set_digest)
    assert basis.basis_digest == handoff.run_basis_digest
    assert resolved.resolution_set_digest == handoff.item_resolution_set_digest


def test_builder_rejects_parallel_resolution_source():
    cmd = command()
    with pytest.raises(ValueError, match="binding_mismatch"):
        build_run_closed_handoff(
            basis=cmd.candidate_basis,
            resolution_set=replace(cmd.resolution_set, run_id="other"),
            authority_digest=d("a"),
            write_epoch=1,
        )


@pytest.mark.asyncio
async def test_consumer_exact_replay_poison_blocked_and_no_downstream_work():
    fake = gateway()
    await FinalizeRun(gateway=fake).execute(command())
    handoff = next(iter(fake.run_closed_handoffs.values()))
    consumer = InMemoryRunClosedHandoffConsumer()
    first = await consumer.consume(
        handoff=handoff, publisher_metadata=RunClosedDeliveryMetadata("c1", 1, "claimed")
    )
    replay = await consumer.consume(
        handoff=handoff, publisher_metadata=RunClosedDeliveryMetadata("c2", 2, "redelivered")
    )
    assert not first.replayed and replay.replayed
    cmd = command()
    poisoned = build_run_closed_handoff(
        basis=cmd.candidate_basis,
        resolution_set=cmd.resolution_set,
        authority_digest=d("e"),
        write_epoch=1,
    )
    with pytest.raises(RunClosedHandoffConflict) as conflict:
        await consumer.consume(handoff=poisoned)
    assert conflict.value.event_id == handoff.event_id
    assert consumer.quarantined_event_ids == (handoff.event_id,)
    assert consumer.alerts[0].severity == "high"
    with pytest.raises(RunClosedHandoffBlocked) as blocked:
        await consumer.consume(handoff=handoff)
    assert blocked.value.event_id == handoff.event_id
    assert consumer.batch_finalizations == ()


@pytest.mark.asyncio
async def test_provider_missing_and_poison_fail_closed():
    fake = gateway()
    await FinalizeRun(gateway=fake).execute(command())
    handoff = next(iter(fake.run_closed_handoffs.values()))
    provider = InMemoryRunClosedFactProvider(fake)
    with pytest.raises(KeyError):
        await provider.get_basis(
            run_id="missing", source_run_version=0, basis_digest=handoff.run_basis_digest
        )
    from qarunner.domain import IdempotencyConflict

    with pytest.raises(IdempotencyConflict):
        await provider.get_basis(run_id=handoff.run_id, source_run_version=0, basis_digest=d("e"))
    with pytest.raises(KeyError):
        await provider.get_resolution_set(digest=d("e"))
    scope, resolved = next(iter(fake.resolution_sets.items()))
    fake.resolution_sets[(scope[0], "other", scope[2])] = resolved
    with pytest.raises(RuntimeError, match="duplicate_resolution_digest"):
        await provider.get_resolution_set(digest=resolved.resolution_set_digest)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "bad"},
        {"identity_algorithm_version": "bad"},
        {"destination": "bad"},
        {"event_id": ""},
        {"source_run_version": True},
        {"item_count": 0},
        {"write_epoch": 0},
        {"payload_digest": "bad"},
        {"retry_chain_digest": "bad"},
        {"run_outcome": "passed"},
    ],
)
def test_handoff_rejects_invalid_constructed_values(changes):
    from qarunner.domain import DomainValidationError

    cmd = command()
    handoff = build_run_closed_handoff(
        basis=cmd.candidate_basis,
        resolution_set=cmd.resolution_set,
        authority_digest=d("a"),
        write_epoch=1,
    )
    with pytest.raises(DomainValidationError):
        replace(handoff, **changes)


@pytest.mark.asyncio
async def test_consumer_revalidates_forged_first_value_even_if_constructor_was_bypassed():
    import copy

    from qarunner.domain import DomainValidationError

    fake = gateway()
    await FinalizeRun(gateway=fake).execute(command())
    forged = copy.copy(next(iter(fake.run_closed_handoffs.values())))
    object.__setattr__(forged, "payload_digest", d("e"))
    consumer = InMemoryRunClosedHandoffConsumer()
    with pytest.raises(DomainValidationError, match="self_consistency"):
        await consumer.consume(handoff=forged)
    assert not consumer.accepted


@pytest.mark.parametrize(
    "changes",
    [
        {"semantic_trigger_key": d("e")},
        {"handoff_id": "run-closed-handoff-forged"},
        {"event_id": "run-closed-event-forged"},
        {"payload_digest": d("e")},
        {"manifest_digest": d("e")},
        {"write_epoch": 2},
    ],
)
def test_type_valid_forged_first_delivery_fails_self_consistency(changes):
    from qarunner.domain import DomainValidationError

    cmd = command()
    handoff = build_run_closed_handoff(
        basis=cmd.candidate_basis,
        resolution_set=cmd.resolution_set,
        authority_digest=d("a"),
        write_epoch=1,
    )
    with pytest.raises(DomainValidationError, match="self_consistency"):
        replace(handoff, **changes)


@pytest.mark.asyncio
async def test_publication_rejects_canonical_handoff_field_drift():
    from qarunner.application.ports.common import PortContractError

    class Capture(type(gateway())):
        publication = None

        async def publish_finalization(self, *, publication):
            self.publication = publication
            return await super().publish_finalization(publication=publication)

    base = gateway()
    fake = Capture(authority=base.authority, mutation_snapshot=base.mutation_snapshot)
    await FinalizeRun(gateway=fake).execute(command())
    with pytest.raises(PortContractError, match="binding"):
        drifted = build_run_closed_handoff(
            basis=fake.publication.basis,
            resolution_set=fake.publication.resolution_set,
            authority_digest=d("e"),
            write_epoch=fake.publication.authority.write_epoch,
        )
        replace(fake.publication, handoff=drifted)
