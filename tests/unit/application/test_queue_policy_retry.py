"""Observable ordering and atomicity for policy retry publication."""

from dataclasses import replace

import pytest
from tests.fakes.greenfield.run_retry import InMemoryRunRetryGateway
from tests.unit.domain.test_retry_authority_union import d, policy_case

from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.run_retry import (
    RetryBudgetReservation,
    RunRetryMutationSnapshot,
    RunRetryProjection,
    RunRetrySideEffect,
    RunRetryWriteAuthority,
)
from qarunner.application.run_retry import QueuePolicyRetry, QueuePolicyRetryCommand
from qarunner.domain import AttemptExecutionFact
from qarunner.domain.errors import DomainValidationError, IdempotencyConflict, VersionConflict
from qarunner.domain.run_retry_policy import RetryDecision


def setup_case(**changes):
    run, _, intent, decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    fake = InMemoryRunRetryGateway(
        authority=RunRetryWriteAuthority(
            run.id,
            d("a"),
            decision.policy_digest,
            decision.authority_schema,
            decision.authority_id,
            decision.authority_version,
            decision.authority_digest,
            1,
        ),
        mutation_snapshot=RunRetryMutationSnapshot(run, run.attempts[-1].version),
        **changes,
    )
    command = QueuePolicyRetryCommand(
        ("run-policy-retry", intent.id), intent, decision, run.version, run.attempts[-1].version
    )
    return fake, command


def setup_closed_case(**changes):
    fake, command = setup_case(**changes)
    decision = replace(
        command.decision,
        decision=RetryDecision.CLOSED_NO_RETRY,
        retry_intent_digest=None,
    )
    return fake, replace(command, candidate_intent=None, decision=decision)


@pytest.mark.asyncio
async def test_closed_no_retry_publishes_decision_without_retry_participants_or_run_mutation() -> (
    None
):
    fake, command = setup_closed_case()
    result = await QueuePolicyRetry(gateway=fake).execute(command)
    assert result.projection.run is fake.mutation_snapshot.run
    assert result.projection.retry_intent_digest is None
    assert not fake.intent_digests and not fake.reservations
    assert len(fake.decisions) == len(fake.audit_records) == len(fake.semantic_outbox) == 1


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"candidate_intent": None}, "candidate_intent"),
        ({"decision": object()}, "decision"),
        ({"expected_run_version": True}, "expected_run_version"),
        ({"expected_attempt_version": -1}, "expected_attempt_version"),
        ({"identity_scope": ["a", "b"]}, "identity_scope"),
        ({"identity_scope": ("a", "")}, "identity_scope"),
    ],
)
def test_command_rejects_invalid_contract(changes, field) -> None:
    _, command = setup_case()
    with pytest.raises(DomainValidationError) as caught:
        replace(command, **changes)
    assert caught.value.field == field


def test_command_rejects_decision_intent_digest_mismatch() -> None:
    _, command = setup_case()
    with pytest.raises(DomainValidationError, match="decision_binding"):
        replace(command, decision=replace(command.decision, retry_intent_digest=d("e")))


@pytest.mark.asyncio
async def test_policy_authority_digest_mismatch_fails_before_lookup() -> None:
    fake, command = setup_case()
    fake.authority = replace(fake.authority, policy_digest=d("e"))
    with pytest.raises(AuthorityStateConflict, match="retry_authority_superseded"):
        await QueuePolicyRetry(gateway=fake).execute(command)
    assert fake.stored_lookups == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"authority_schema": "other-schema"},
        {"authority_id": "other-id"},
        {"authority_version": 999},
        {"authority_digest": d("e")},
    ],
)
async def test_current_approval_authority_mismatch_fails_before_lookup(changes) -> None:
    fake, command = setup_case()
    fake.authority = replace(fake.authority, **changes)
    with pytest.raises(AuthorityStateConflict, match="retry_authority_superseded"):
        await QueuePolicyRetry(gateway=fake).execute(command)
    assert fake.stored_lookups == 0


@pytest.mark.asyncio
async def test_handler_rejects_expected_attempt_and_run_version_conflicts() -> None:
    fake, command = setup_case()
    with pytest.raises(VersionConflict):
        await QueuePolicyRetry(gateway=fake).execute(
            replace(command, expected_attempt_version=command.expected_attempt_version + 1)
        )
    with pytest.raises(VersionConflict):
        await QueuePolicyRetry(gateway=fake).execute(
            replace(command, expected_run_version=command.expected_run_version + 1)
        )


@pytest.mark.asyncio
async def test_stored_intent_digest_mismatch_is_poison() -> None:
    fake, command = setup_case()
    await QueuePolicyRetry(gateway=fake).execute(command)
    fake.projections[command.identity_scope] = replace(
        fake.projections[command.identity_scope], retry_intent_digest=d("e")
    )
    with pytest.raises(IdempotencyConflict):
        await QueuePolicyRetry(gateway=fake).execute(command)


@pytest.mark.asyncio
async def test_fake_revalidates_publication_authority_and_run_id() -> None:
    fake, command = setup_case()

    class CapturingGateway(InMemoryRunRetryGateway):
        publication = None

        async def publish_retry(self, *, publication):
            self.publication = publication
            return await super().publish_retry(publication=publication)

    capture = CapturingGateway(authority=fake.authority, mutation_snapshot=fake.mutation_snapshot)
    await QueuePolicyRetry(gateway=capture).execute(command)
    publication = capture.publication
    fresh, _ = setup_case()
    fresh.authority_current = False
    with pytest.raises(AuthorityStateConflict):
        await fresh.publish_retry(publication=publication)
    with pytest.raises(AuthorityStateConflict):
        await fresh.require_retry_authority(run_id="other")
    with pytest.raises(KeyError):
        await fresh.get_mutation_snapshot_for_update(run_id="other")


@pytest.mark.asyncio
async def test_fake_publish_exact_replay_and_poison_are_atomic() -> None:
    fake, command = setup_case()

    class Capture(InMemoryRunRetryGateway):
        publication = None

        async def publish_retry(self, *, publication):
            self.publication = publication
            return await super().publish_retry(publication=publication)

    capture = Capture(authority=fake.authority, mutation_snapshot=fake.mutation_snapshot)
    await QueuePolicyRetry(gateway=capture).execute(command)
    replay = await capture.publish_retry(publication=capture.publication)
    assert replay.replayed
    capture.projections[command.identity_scope] = replace(
        capture.projections[command.identity_scope], decision_digest=d("e")
    )
    with pytest.raises(IdempotencyConflict):
        await capture.publish_retry(publication=capture.publication)


def test_port_value_contracts_and_publication_bindings() -> None:
    fake, command = setup_case()
    for changes in (
        {"run_id": ""},
        {"writer_digest": "bad"},
        {"policy_digest": "bad"},
        {"authority_schema": ""},
        {"authority_id": ""},
        {"authority_version": True},
        {"authority_digest": "bad"},
        {"write_epoch": True},
    ):
        with pytest.raises(PortContractError):
            replace(fake.authority, **changes)
    with pytest.raises(PortContractError):
        RunRetryMutationSnapshot(object(), 0)
    with pytest.raises(PortContractError):
        RunRetryMutationSnapshot(fake.mutation_snapshot.run, -1)
    with pytest.raises(PortContractError):
        RunRetryProjection(object(), d("d"), None)
    with pytest.raises(PortContractError):
        RunRetryProjection(fake.mutation_snapshot.run, "bad", None)
    with pytest.raises(PortContractError):
        RunRetryProjection(fake.mutation_snapshot.run, d("d"), "bad")
    closed = replace(
        command.decision, decision=RetryDecision.CLOSED_NO_RETRY, retry_intent_digest=None
    )
    with pytest.raises(PortContractError):
        RetryBudgetReservation.from_decision(closed)
    reservation = RetryBudgetReservation.from_decision(command.decision)
    for changes in (
        {"retry_intent_digest": "bad"},
        {"decision_digest": "bad"},
        {"configured": object()},
        {"usage": object()},
        {"requested": object()},
    ):
        with pytest.raises(PortContractError):
            replace(reservation, **changes)
    side_effect = RunRetrySideEffect(
        command.decision.source.run_id,
        command.decision.decision_digest,
        command.decision.retry_intent_digest,
    )
    for changes in (
        {"run_id": ""},
        {"decision_digest": "bad"},
        {"retry_intent_digest": "bad"},
    ):
        with pytest.raises(PortContractError):
            replace(side_effect, **changes)


@pytest.mark.asyncio
async def test_publication_rejects_invalid_participants_and_bindings() -> None:
    fake, command = setup_case()

    class Capture(InMemoryRunRetryGateway):
        publication = None

        async def publish_retry(self, *, publication):
            self.publication = publication
            return await super().publish_retry(publication=publication)

    capture = Capture(authority=fake.authority, mutation_snapshot=fake.mutation_snapshot)
    await QueuePolicyRetry(gateway=capture).execute(command)
    publication = capture.publication
    for changes in (
        {"identity_scope": ("", "x")},
        {"authority": object()},
        {"expected_snapshot": object()},
        {"decision": object()},
        {"projection": object()},
        {"side_effect": object()},
        {"intent": None},
        {"reservation": None},
        {"authority": replace(publication.authority, run_id="other")},
        {"authority": replace(publication.authority, authority_schema="other")},
        {"authority": replace(publication.authority, authority_id="other")},
        {"authority": replace(publication.authority, authority_version=999)},
        {"authority": replace(publication.authority, authority_digest=d("e"))},
        {"projection": replace(publication.projection, retry_intent_digest=d("e"))},
        {"reservation": replace(publication.reservation, decision_digest=d("e"))},
        {
            "reservation": replace(
                publication.reservation, configured=replace(command.decision.budget, max_retries=0)
            )
        },
        {
            "reservation": replace(
                publication.reservation, usage=replace(command.decision.usage, retry_count=99)
            )
        },
        {
            "reservation": replace(
                publication.reservation,
                requested=replace(command.decision.requested, execution_seconds=99),
            )
        },
        {"side_effect": replace(publication.side_effect, run_id="other")},
    ):
        with pytest.raises(PortContractError):
            replace(publication, **changes)


@pytest.mark.asyncio
async def test_authority_denial_precedes_stored_lookup() -> None:
    fake, command = setup_case(authority_current=False)
    with pytest.raises(AuthorityStateConflict):
        await QueuePolicyRetry(gateway=fake).execute(command)
    assert fake.authority_checks == 1
    assert fake.stored_lookups == fake.snapshot_reads == fake.publication_attempts == 0


@pytest.mark.asyncio
async def test_exact_replay_does_not_repeat_reservation_audit_or_outbox() -> None:
    fake, command = setup_case()
    handler = QueuePolicyRetry(gateway=fake)
    first = await handler.execute(command)
    replay = await handler.execute(command)
    assert not first.replayed and replay.replayed
    assert len(fake.reservations) == len(fake.audit_records) == len(fake.semantic_outbox) == 1
    assert fake.publication_commits == 1


@pytest.mark.asyncio
async def test_same_semantic_identity_with_different_decision_is_poison() -> None:
    from dataclasses import replace

    from qarunner.domain import IdempotencyConflict

    fake, command = setup_case()
    handler = QueuePolicyRetry(gateway=fake)
    await handler.execute(command)
    changed = replace(command.decision, reason_code="changed")
    poisoned = replace(command, decision=changed)
    with pytest.raises(IdempotencyConflict):
        await handler.execute(poisoned)


@pytest.mark.asyncio
async def test_publish_revalidates_locked_run_and_attempt_snapshot() -> None:
    class ConcurrentMutationGateway(InMemoryRunRetryGateway):
        async def publish_retry(self, *, publication):
            self.mutation_snapshot = RunRetryMutationSnapshot(
                self.mutation_snapshot.run, self.mutation_snapshot.attempt_version + 1
            )
            return await super().publish_retry(publication=publication)

    fake, command = setup_case()
    concurrent = ConcurrentMutationGateway(
        authority=fake.authority, mutation_snapshot=fake.mutation_snapshot
    )
    with pytest.raises(VersionConflict):
        await QueuePolicyRetry(gateway=concurrent).execute(command)
    assert concurrent.publication_commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point", ["intent", "decision", "run", "reservation", "audit", "outbox", "commit"]
)
async def test_publication_fault_has_zero_partial_writes(point: str) -> None:
    fake, command = setup_case(fault_at=point)
    with pytest.raises(RuntimeError, match=point):
        await QueuePolicyRetry(gateway=fake).execute(command)
    assert not fake.intent_digests and not fake.decisions and not fake.projections
    assert not fake.reservations and not fake.audit_records and not fake.semantic_outbox
    assert fake.publication_commits == 0
