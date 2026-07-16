"""Application ordering and atomicity for terminal Run finalization."""

from dataclasses import replace

import pytest
from tests.fakes.greenfield import InMemoryRunFinalizationGateway
from tests.unit.domain.test_run_finalization_basis import basis, d, resolution_set

from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
)
from qarunner.application.ports.run_finalization import (
    FinalizeRunAuthority,
    RunFinalizationMutationSnapshot,
)
from qarunner.application.run_finalization import FinalizeRun, FinalizeRunCommand


def gateway(**changes):
    values = dict(
        authority=FinalizeRunAuthority("run-1", "batch-1", 0, d("f"), 1),
        mutation_snapshot=RunFinalizationMutationSnapshot("run-1", 0, "attempt-1", 0),
    )
    values.update(changes)
    return InMemoryRunFinalizationGateway(**values)


def bound_basis(resolved=None, **changes):
    resolved = resolved or resolution_set()
    template = basis(**changes)
    values = {field: getattr(template, field) for field in template.__dataclass_fields__}
    for field in (
        "run_id",
        "batch_id",
        "source_run_version",
        "manifest_digest",
        "shard_plan_digest",
        "run_item_set_digest",
        "attempt_chain_digest",
        "retry_chain_digest",
        "adjudication_chain_digest",
        "item_resolution_set_digest",
        "original_resolution_set_digest",
        "effective_resolution_set_digest",
        "item_count",
    ):
        values.pop(field)
    return template.build(
        resolution_set=resolved,
        manifest_digest=resolved.manifest_digest,
        shard_plan_digest=resolved.shard_plan_digest,
        run_item_set_digest=resolved.run_item_set_digest,
        source_run_version=resolved.source_run_version,
        **values,
    )


def command(**changes):
    resolved = changes.pop("resolution_set", resolution_set())
    candidate = (
        changes.pop("candidate_basis") if "candidate_basis" in changes else bound_basis(resolved)
    )
    values = dict(
        candidate_basis=candidate,
        resolution_set=resolved,
        expected_run_version=0,
        expected_attempt_version=0,
    )
    values.update(changes)
    return FinalizeRunCommand(**values)


@pytest.mark.asyncio
async def test_new_finalization_atomically_publishes_once() -> None:
    fake = gateway()
    result = await FinalizeRun(gateway=fake).execute(command())
    assert not result.replayed
    assert (fake.authority_checks, fake.stored_lookups, fake.mutation_snapshot_reads) == (1, 1, 1)
    assert (fake.publication_commits, fake.audit_count, fake.outbox_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_exact_replay_precedes_stale_cas_and_does_not_duplicate_outbox() -> None:
    fake = gateway()
    handler = FinalizeRun(gateway=fake)
    first = await handler.execute(command())
    replay = await handler.execute(command(expected_run_version=99, expected_attempt_version=99))
    assert replay.replayed and replay.projection == first.projection
    assert fake.mutation_snapshot_reads == 1
    assert (fake.audit_count, fake.outbox_count) == (1, 1)


@pytest.mark.asyncio
async def test_same_identity_different_digest_conflicts_before_cas() -> None:
    from qarunner.domain import IdempotencyConflict

    fake = gateway()
    handler = FinalizeRun(gateway=fake)
    await handler.execute(command())
    changed = replace(bound_basis(), terminal_rule_digest=d("e"))
    with pytest.raises(IdempotencyConflict):
        await handler.execute(command(candidate_basis=changed, expected_run_version=99))
    assert fake.mutation_snapshot_reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"authority_available": False}, AuthorityProjectionUnavailable),
        ({"authority_allowed": False}, AuthorityPermissionDenied),
        ({"authority_current": False}, AuthorityStateConflict),
    ],
)
async def test_authority_failure_precedes_stored_lookup(changes, error) -> None:
    fake = gateway(**changes)
    with pytest.raises(error):
        await FinalizeRun(gateway=fake).execute(command())
    assert fake.authority_checks == 1
    assert (fake.stored_lookups, fake.mutation_snapshot_reads, fake.publication_attempts) == (
        0,
        0,
        0,
    )


@pytest.mark.asyncio
async def test_new_mutation_checks_attempt_before_run_cas() -> None:
    from qarunner.domain import VersionConflict

    fake = gateway()
    handler = FinalizeRun(gateway=fake)
    with pytest.raises(VersionConflict) as attempt_error:
        await handler.execute(command(expected_attempt_version=9, expected_run_version=9))
    assert attempt_error.value.entity_type == "attempt"
    with pytest.raises(VersionConflict) as run_error:
        await handler.execute(command(expected_run_version=9))
    assert run_error.value.entity_type == "run"
    assert fake.publication_attempts == 0


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"expected_run_version": True}, "expected_run_version"),
        ({"expected_attempt_version": -1}, "expected_attempt_version"),
        ({"candidate_basis": object()}, "candidate_basis"),
        ({"resolution_set": object(), "candidate_basis": bound_basis()}, "resolution_set"),
    ],
)
def test_command_rejects_invalid_inputs(changes, field) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError, match=field):
        command(**changes)


def test_command_rejects_basis_resolution_and_attempt_mismatch() -> None:
    from qarunner.domain import DomainValidationError

    resolved = resolution_set()
    with pytest.raises(DomainValidationError, match="basis_mismatch"):
        command(
            resolution_set=resolved,
            candidate_basis=replace(bound_basis(resolved), item_resolution_set_digest=d("e")),
        )
    with pytest.raises(DomainValidationError, match="attempt_mismatch"):
        command(expected_attempt_version=None)


@pytest.mark.asyncio
async def test_authority_scope_mismatch_precedes_stored_lookup() -> None:
    from qarunner.domain import VersionConflict

    fake = gateway(authority=FinalizeRunAuthority("run-1", "other-batch", 0, d("f"), 1))
    with pytest.raises(VersionConflict):
        await FinalizeRun(gateway=fake).execute(command())
    assert (fake.stored_lookups, fake.mutation_snapshot_reads) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point", ["basis", "resolution_set", "projection", "handoff", "audit", "outbox", "commit"]
)
async def test_publication_fault_rolls_back_every_participant(point: str) -> None:
    fake = gateway(fault_at=point)
    with pytest.raises(RuntimeError, match=point):
        await FinalizeRun(gateway=fake).execute(command())
    assert not fake.bases and not fake.resolution_sets and not fake.projections
    assert not fake.run_closed_handoffs
    assert (fake.audit_count, fake.outbox_count, fake.publication_commits) == (0, 0, 0)
