"""Deterministic application publication for unknown-adjudicated retries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.fakes.greenfield.run_retry import InMemoryRunRetryGateway
from tests.unit.domain.test_retry_authority_union import policy_case
from tests.unit.domain.test_unknown_adjudicated_retry import (
    _adjudication,
    _digest,
    _retry_intent,
    _unknown_run,
)

from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.run_retry import (
    RunRetryProjection,
    UnknownRetryMutationBinding,
    UnknownRetryMutationSnapshot,
    UnknownRetryWriteAuthority,
)
from qarunner.application.run_retry import QueueAdjudicatedRetry, QueueAdjudicatedRetryCommand
from qarunner.domain import (
    AttemptExecutionFact,
    DomainValidationError,
    DuplicateRiskAcceptance,
    DuplicateRiskAcceptanceBasis,
    DuplicateRiskAcceptanceRequest,
    IdempotencyConflict,
    UnknownAdjudicationDecision,
    VersionConflict,
)

NOW = datetime(2026, 7, 12, 21, 30, tzinfo=UTC)


def setup_case(*, duplicate: bool = False, **fake_changes):
    unknown, _, _ = _unknown_run()
    decision = "ACCEPT_DUPLICATE_RISK_THEN_RETRY" if duplicate else "CONFIRM_STOPPED_THEN_RETRY"
    basis = DuplicateRiskAcceptanceBasis(
        id="acceptance-1",
        run_id=unknown.id,
        source_attempt_id="attempt-001",
        source_attempt_no=1,
        source_fence=1,
        run_item_set_digest=_digest("items"),
        sut_identity="sut-1",
        sut_digest=_digest("sut"),
        target_grant_identity="grant-1",
        target_grant_version=1,
        target_grant_digest=_digest("grant"),
        suite_owner_id="owner-1",
        reviewer_id="reviewer-1",
        original_executor_id="worker-1",
        original_trigger_actor_id="trigger-1",
        accepted_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    adjudication = _adjudication(decision_name=decision)
    if duplicate:
        adjudication = replace(adjudication, risk_acceptance_digest=basis.digest)
    run = unknown.append_current_unknown_adjudication(
        attempt_id="attempt-001",
        adjudication=adjudication,
        expected_version=unknown.version,
        expected_attempt_version=unknown.attempts[-1].version,
    )
    intent = _retry_intent(adjudication=adjudication)
    request = None
    if duplicate:
        request = DuplicateRiskAcceptanceRequest(
            "unknown-retry-acceptance",
            "acceptance-1",
            DuplicateRiskAcceptance(basis, intent.digest),
            NOW + timedelta(minutes=1),
        )
    authority = UnknownRetryWriteAuthority(
        run.id,
        "attempt-001",
        run.attempts[-1].version,
        1,
        adjudication.id,
        adjudication.digest,
        adjudication.decision,
        adjudication.proof_digest,
        basis.digest if duplicate else None,
        _digest("writer"),
        1,
    )
    snapshot = UnknownRetryMutationSnapshot(
        run,
        run.attempts[-1].version,
        UnknownRetryMutationBinding(
            basis.run_item_set_digest,
            basis.sut_identity,
            basis.sut_digest,
            basis.target_grant_identity,
            basis.target_grant_version,
            basis.target_grant_digest,
        ),
    )
    fake = InMemoryRunRetryGateway(
        unknown_authority=authority, unknown_mutation_snapshot=snapshot, **fake_changes
    )
    command = QueueAdjudicatedRetryCommand(
        ("unknown-run-retry", intent.id),
        intent,
        run.version,
        run.attempts[-1].version,
        request,
    )
    return fake, command


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_unknown_retry_publishes_atomically_without_attempt_or_budget(duplicate) -> None:
    fake, command = setup_case(duplicate=duplicate)
    before_attempts = command.candidate_intent.source_attempt_no
    result = await QueueAdjudicatedRetry(gateway=fake).execute(command)

    assert result.projection.run.pending_retry_intent == command.candidate_intent
    assert len(result.projection.run.attempts) == before_attempts
    assert not fake.reservations
    assert len(fake.audit_records) == len(fake.semantic_outbox) == 1
    assert len(fake.acceptance_consumptions) == int(duplicate)


@pytest.mark.asyncio
async def test_authority_precedes_replay_and_exact_replay_has_no_new_effects() -> None:
    fake, command = setup_case(duplicate=True)
    first = await QueueAdjudicatedRetry(gateway=fake).execute(command)
    effects = (fake.acceptance_consumptions.copy(), fake.audit_records, fake.semantic_outbox)
    replay = await QueueAdjudicatedRetry(gateway=fake).execute(command)
    assert first.replayed is False and replay.replayed is True
    assert effects == (fake.acceptance_consumptions, fake.audit_records, fake.semantic_outbox)
    assert fake.unknown_authority_checks == 2
    fake.authority_current = False
    with pytest.raises(AuthorityStateConflict):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)


@pytest.mark.asyncio
async def test_versions_binding_and_nonretry_fail_closed() -> None:
    fake, command = setup_case(duplicate=True)
    with pytest.raises(AuthorityStateConflict):
        await QueueAdjudicatedRetry(gateway=fake).execute(
            replace(command, expected_attempt_version=command.expected_attempt_version + 1)
        )
    with pytest.raises(VersionConflict):
        await QueueAdjudicatedRetry(gateway=fake).execute(
            replace(command, expected_run_version=command.expected_run_version + 1)
        )
    fake, command = setup_case(duplicate=True)
    fake.unknown_mutation_snapshot = replace(
        fake.unknown_mutation_snapshot,
        binding=replace(fake.unknown_mutation_snapshot.binding, sut_digest=_digest("changed")),
    )
    with pytest.raises(PortContractError, match="acceptance_binding"):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)
    fake, command = setup_case()
    fake.unknown_mutation_snapshot = replace(
        fake.unknown_mutation_snapshot,
        attempt_version=fake.unknown_mutation_snapshot.attempt_version + 1,
    )
    with pytest.raises(VersionConflict):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)

    _, stopped = setup_case()
    with pytest.raises(DomainValidationError):
        replace(stopped, acceptance_request=setup_case(duplicate=True)[1].acceptance_request)
    nonretry = replace(
        stopped.candidate_intent,
        authority=replace(
            stopped.candidate_intent.authority,
            decision=UnknownAdjudicationDecision.MARK_INFRA_FAILED_NO_RETRY,
        ),
    )
    with pytest.raises(DomainValidationError):
        replace(stopped, candidate_intent=nonretry)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["intent", "acceptance", "run", "audit", "outbox", "commit"])
async def test_faults_leave_zero_partial_writes(fault) -> None:
    fake, command = setup_case(duplicate=True, fault_at=fault)
    with pytest.raises(RuntimeError):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)
    assert not fake.projections and not fake.intent_digests
    assert not fake.acceptance_consumptions and not fake.audit_records and not fake.semantic_outbox


@pytest.mark.asyncio
async def test_cancel_race_at_publish_leaves_zero_partial_writes() -> None:
    fake, command = setup_case(duplicate=True)
    fake.cancel_at_publish = True
    with pytest.raises(VersionConflict, match="snapshot"):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)
    assert not fake.projections and not fake.acceptance_consumptions


@pytest.mark.asyncio
async def test_unknown_snapshot_lookup_rejects_wrong_run() -> None:
    fake, _ = setup_case()
    with pytest.raises(KeyError):
        await fake.get_unknown_mutation_snapshot_for_update(run_id="other-run")


@pytest.mark.asyncio
async def test_acceptance_exact_replay_does_not_create_second_consumption() -> None:
    fake, command = setup_case(duplicate=True)

    class Capture(type(fake)):
        publication = None

        async def publish_retry(self, *, publication):
            self.publication = publication
            return await super().publish_retry(publication=publication)

    capture = Capture(
        unknown_authority=fake.unknown_authority,
        unknown_mutation_snapshot=fake.unknown_mutation_snapshot,
    )
    await QueueAdjudicatedRetry(gateway=capture).execute(command)
    prior = capture.acceptance_consumptions.copy()
    capture.projections.clear()
    replay_publication = replace(
        capture.publication,
        identity_scope=(capture.publication.identity_scope[0], "acceptance-replay"),
    )

    await capture.publish_retry(publication=replay_publication)

    assert capture.acceptance_consumptions == prior


@pytest.mark.asyncio
async def test_poison_replay_is_rejected() -> None:
    fake, command = setup_case()
    await QueueAdjudicatedRetry(gateway=fake).execute(command)
    fake.projections[command.identity_scope] = replace(
        fake.projections[command.identity_scope], retry_intent_digest=_digest("poison")
    )
    with pytest.raises(IdempotencyConflict):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)


def test_unknown_port_value_contracts() -> None:
    fake, command = setup_case(duplicate=True)
    for changes in (
        {"run_id": ""},
        {"adjudication_digest": "bad"},
        {"decision": "bad"},
        {"writer_digest": "bad"},
        {"write_epoch": True},
    ):
        with pytest.raises(PortContractError):
            replace(fake.unknown_authority, **changes)
    stopped_authority = setup_case()[0].unknown_authority
    duplicate_authority = fake.unknown_authority
    for authority, changes in (
        (stopped_authority, {"risk_acceptance_basis_digest": _digest("forbidden")}),
        (duplicate_authority, {"proof_digest": _digest("forbidden")}),
        (
            stopped_authority,
            {"decision": UnknownAdjudicationDecision.MARK_INFRA_FAILED_NO_RETRY},
        ),
    ):
        with pytest.raises(PortContractError):
            replace(authority, **changes)
    for changes in (
        {"run_item_set_digest": "bad"},
        {"sut_identity": ""},
        {"sut_digest": "bad"},
        {"target_grant_identity": ""},
        {"target_grant_version": True},
        {"target_grant_digest": "bad"},
    ):
        with pytest.raises(PortContractError):
            replace(fake.unknown_mutation_snapshot.binding, **changes)
    with pytest.raises(PortContractError):
        UnknownRetryMutationSnapshot(object(), 0, fake.unknown_mutation_snapshot.binding)
    with pytest.raises(PortContractError):
        replace(fake.unknown_mutation_snapshot, binding=object())
    with pytest.raises(PortContractError):
        replace(fake.unknown_mutation_snapshot, attempt_version=-1)


@pytest.mark.asyncio
async def test_publication_contract_and_current_authority_mismatch() -> None:
    fake, command = setup_case(duplicate=True)

    class Capture(type(fake)):
        publication = None

        async def publish_retry(self, *, publication):
            self.publication = publication
            return await super().publish_retry(publication=publication)

    capture = Capture(
        unknown_authority=fake.unknown_authority,
        unknown_mutation_snapshot=fake.unknown_mutation_snapshot,
    )
    await QueueAdjudicatedRetry(gateway=capture).execute(command)
    publication = capture.publication
    for changes in (
        {"identity_scope": ["a", "b"]},
        {"authority": object()},
        {"expected_snapshot": object()},
        {"intent": object()},
        {"projection": object()},
        {"side_effect": object()},
        {"acceptance_request": None},
        {
            "projection": RunRetryProjection(
                publication.projection.run,
                _digest("wrong"),
                publication.intent.digest,
            )
        },
    ):
        with pytest.raises(PortContractError):
            replace(publication, **changes)
    bad_acceptance = replace(
        publication.acceptance_request.acceptance,
        retry_intent_digest=_digest("wrong-intent"),
    )
    with pytest.raises(PortContractError, match="acceptance_binding"):
        replace(
            publication,
            acceptance_request=replace(publication.acceptance_request, acceptance=bad_acceptance),
        )
    _, _, policy_intent, _ = policy_case(AttemptExecutionFact.TEST_FAILED)
    with pytest.raises(PortContractError, match="not_unknown"):
        replace(publication, intent=policy_intent)

    stopped_fake, stopped = setup_case()
    stopped_capture = Capture(
        unknown_authority=stopped_fake.unknown_authority,
        unknown_mutation_snapshot=stopped_fake.unknown_mutation_snapshot,
    )
    await QueueAdjudicatedRetry(gateway=stopped_capture).execute(stopped)
    with pytest.raises(PortContractError):
        replace(
            stopped_capture.publication,
            acceptance_request=command.acceptance_request,
        )

    fake, command = setup_case()
    fake.unknown_authority = replace(fake.unknown_authority, adjudication_id="superseded")
    with pytest.raises(AuthorityStateConflict):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)
    assert fake.stored_lookups == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_id": "other-attempt"},
        {"attempt_version": 999},
        {"attempt_fence": 999},
        {"adjudication_digest": _digest("superseded")},
    ],
)
async def test_source_authority_drift_fails_before_stored_lookup(changes) -> None:
    fake, command = setup_case()
    fake.unknown_authority = replace(fake.unknown_authority, **changes)
    with pytest.raises(AuthorityStateConflict):
        await QueueAdjudicatedRetry(gateway=fake).execute(command)
    assert fake.stored_lookups == 0


def test_command_rejects_invalid_identity_scope() -> None:
    _, command = setup_case()
    with pytest.raises(DomainValidationError, match="identity_scope"):
        replace(command, identity_scope=("", "key"))
