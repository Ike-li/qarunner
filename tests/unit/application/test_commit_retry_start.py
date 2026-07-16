"""Retry commit-start revalidates current authority before any replay."""

from dataclasses import replace

import pytest
from tests.fakes.greenfield.retry_commit_start import InMemoryRetryCommitStartGateway
from tests.unit.application.test_queue_adjudicated_retry import setup_case
from tests.unit.application.test_queue_policy_retry import setup_case as setup_policy_case
from tests.unit.domain.test_unknown_adjudicated_retry import (
    RETRY_CLAIMED_AT,
    RETRY_COMMITTED_AT,
    RETRY_EXPIRES_AT,
    RETRY_OFFERED_AT,
    _digest,
    _ready_worker,
)

from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
from qarunner.application.ports.common import PortContractError
from qarunner.application.ports.retry_commit_start import (
    RetryCommitMutationSnapshot,
    RetryCommitPublication,
    RetryCommitSideEffect,
    RetryCommitStartGateway,
    RetryQueueReceipt,
    StoredRetryCommit,
)
from qarunner.application.ports.run_retry import (
    RetryBudgetReservation,
    RetryQueuePublicationResult,
    RunRetryProjection,
)
from qarunner.application.retry_commit_start import (
    CommitRetryStart,
    CommitRetryStartCommand,
    _authority_matches,
)
from qarunner.domain import IdempotencyConflict, VersionConflict
from qarunner.domain.errors import DomainValidationError


def setup_commit_case(*, duplicate=False, **fake_changes):
    queue_fake, queue_command = setup_case(duplicate=duplicate)
    queued = queue_fake.unknown_mutation_snapshot.run.queue_adjudicated_retry(
        retry_intent=queue_command.candidate_intent,
        expected_version=queue_command.expected_run_version,
    )
    worker, worker_authority = _ready_worker(generation=4)
    offered = queued.offer_assignment(
        assignment_id="assignment-002",
        worker=worker,
        worker_authority=worker_authority,
        spec_digest=queue_command.candidate_intent.execution_spec_digest,
        offered_at=RETRY_OFFERED_AT,
        expires_at=RETRY_EXPIRES_AT,
        expected_version=queued.version,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-002",
        worker=worker.ref,
        observed_at=RETRY_CLAIMED_AT,
        expected_version=offered.version,
    )
    consumption = None
    if duplicate:
        request = queue_command.acceptance_request
        from qarunner.domain import consume_duplicate_risk_acceptance

        consumption = consume_duplicate_risk_acceptance(request=request, prior=None)
    receipt = RetryQueueReceipt(
        intent=queue_command.candidate_intent,
        reservation=None,
        duplicate_acceptance_consumption=consumption,
        queue_decision_digest=queue_fake.unknown_authority.adjudication_digest,
        queue_identity_scope=queue_command.identity_scope,
        queued_run_version=queued.version,
        queue_writer_digest=queue_fake.unknown_authority.writer_digest,
        queue_write_epoch=queue_fake.unknown_authority.write_epoch,
    )
    snapshot = RetryCommitMutationSnapshot(claimed, claimed.attempts[-1].version)
    fake = InMemoryRetryCommitStartGateway(
        authority=queue_fake.unknown_authority,
        mutation_snapshot=snapshot,
        queue_receipts={receipt.intent.digest: receipt},
        **fake_changes,
    )
    command = CommitRetryStartCommand(
        identity_scope=("retry-commit-start", "commit-002"),
        receipt=receipt,
        assignment_id="assignment-002",
        worker=worker.ref,
        start_commit_key="commit-002",
        spec_digest=receipt.intent.execution_spec_digest,
        new_attempt_id="attempt-002",
        observed_at=RETRY_COMMITTED_AT,
        expected_run_version=claimed.version,
        expected_attempt_version=claimed.attempts[-1].version,
    )
    return fake, command


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_commit_creates_exactly_one_attempt_from_external_receipt(duplicate) -> None:
    fake, command = setup_commit_case(duplicate=duplicate)
    result = await CommitRetryStart(gateway=fake).execute(command)
    assert result.commit.attempt.attempt_no == 2
    assert (
        result.commit.attempt.retry_provenance.retry_intent_digest == command.receipt.intent.digest
    )
    assert result.commit.run.pending_retry_intent is None
    assert result.replayed is False
    assert len(fake.audit_records) == len(fake.semantic_outbox) == 1


@pytest.mark.asyncio
async def test_current_authority_precedes_historical_replay() -> None:
    fake, command = setup_commit_case()
    await CommitRetryStart(gateway=fake).execute(command)
    fake.authority_current = False
    with pytest.raises(AuthorityStateConflict):
        await CommitRetryStart(gateway=fake).execute(command)
    assert fake.authority_checks == 2


@pytest.mark.asyncio
async def test_exact_replay_does_not_duplicate_attempt_or_side_effects() -> None:
    fake, command = setup_commit_case(duplicate=True)
    first = await CommitRetryStart(gateway=fake).execute(command)
    effects = (fake.audit_records, fake.semantic_outbox)
    replay = await CommitRetryStart(gateway=fake).execute(command)
    assert replay.replayed and replay.commit.attempt == first.commit.attempt
    assert effects == (fake.audit_records, fake.semantic_outbox)


@pytest.mark.asyncio
async def test_temporary_or_stored_receipt_poison_is_rejected() -> None:
    fake, command = setup_commit_case()
    forged = replace(command.receipt, queue_writer_digest=_digest("forged-writer"))
    with pytest.raises(AuthorityStateConflict, match="receipt_mismatch"):
        await CommitRetryStart(gateway=fake).execute(replace(command, receipt=forged))
    assert not fake.projections

    await CommitRetryStart(gateway=fake).execute(command)
    fake.receipt_digests[command.identity_scope] = _digest("poison-receipt")
    with pytest.raises(IdempotencyConflict):
        await CommitRetryStart(gateway=fake).execute(command)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["attempt", "run", "audit", "outbox", "commit"])
async def test_faults_and_cancel_leave_zero_partial_writes(fault) -> None:
    fake, command = setup_commit_case(fault_at=fault)
    with pytest.raises(RuntimeError):
        await CommitRetryStart(gateway=fake).execute(command)
    assert not fake.projections and not fake.audit_records and not fake.semantic_outbox


@pytest.mark.asyncio
async def test_snapshot_drift_blocks_attempt_creation() -> None:
    fake, command = setup_commit_case()
    fake.cancel_at_publish = True
    with pytest.raises(VersionConflict):
        await CommitRetryStart(gateway=fake).execute(command)
    assert not fake.projections


@pytest.mark.asyncio
async def test_queue_publication_atomically_returns_receipt_used_by_commit() -> None:
    queue_fake, queue_command = setup_case(duplicate=True)
    from qarunner.application.run_retry import QueueAdjudicatedRetry

    queued = await QueueAdjudicatedRetry(gateway=queue_fake).execute(queue_command)
    assert queued.receipt is queue_fake.receipts[queue_command.identity_scope]
    assert queued.receipt.duplicate_acceptance_consumption is not None
    _, commit = setup_commit_case(duplicate=True)
    assert replace(commit, receipt=queued.receipt).receipt == queued.receipt


@pytest.mark.asyncio
async def test_attempt_and_run_version_conflicts_precede_mutation() -> None:
    fake, command = setup_commit_case()
    with pytest.raises(VersionConflict, match="attempt"):
        await CommitRetryStart(gateway=fake).execute(
            replace(command, expected_attempt_version=command.expected_attempt_version + 1)
        )
    with pytest.raises(VersionConflict, match="run"):
        await CommitRetryStart(gateway=fake).execute(
            replace(command, expected_run_version=command.expected_run_version + 1)
        )
    assert not fake.projections


@pytest.mark.asyncio
async def test_authority_drift_and_poison_replay_are_rejected() -> None:
    fake, command = setup_commit_case()
    fake.authority = replace(fake.authority, attempt_fence=fake.authority.attempt_fence + 1)
    with pytest.raises(AuthorityStateConflict):
        await CommitRetryStart(gateway=fake).execute(command)
    fake, command = setup_commit_case()
    await CommitRetryStart(gateway=fake).execute(command)
    stored = fake.projections[command.identity_scope]
    fake.projections[command.identity_scope] = replace(
        stored,
        attempt=replace(
            stored.attempt,
            retry_provenance=replace(
                stored.attempt.retry_provenance, retry_intent_digest=_digest("poison")
            ),
        ),
    )
    with pytest.raises(IdempotencyConflict):
        await CommitRetryStart(gateway=fake).execute(command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("new_attempt_id", "other-attempt"),
        ("assignment_id", "other-assignment"),
        ("start_commit_key", "other-key"),
        ("spec_digest", _digest("other-spec")),
    ],
)
async def test_semantically_different_commit_replay_is_poison(field, value) -> None:
    fake, command = setup_commit_case()
    await CommitRetryStart(gateway=fake).execute(command)
    with pytest.raises(IdempotencyConflict):
        await CommitRetryStart(gateway=fake).execute(replace(command, **{field: value}))


@pytest.mark.asyncio
async def test_different_worker_commit_replay_is_poison() -> None:
    fake, command = setup_commit_case()
    await CommitRetryStart(gateway=fake).execute(command)
    other, _ = _ready_worker(generation=5)
    with pytest.raises(IdempotencyConflict):
        await CommitRetryStart(gateway=fake).execute(replace(command, worker=other.ref))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity_scope", ("", "key")),
        ("receipt", object()),
        ("assignment_id", ""),
        ("worker", object()),
        ("start_commit_key", ""),
        ("spec_digest", object()),
        ("new_attempt_id", ""),
        ("observed_at", object()),
        ("expected_run_version", True),
        ("expected_attempt_version", -1),
    ],
)
def test_command_rejects_invalid_contract(field, value) -> None:
    _, command = setup_commit_case()
    with pytest.raises(DomainValidationError) as caught:
        replace(command, **{field: value})
    assert caught.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run", object()),
        ("source_attempt_version", True),
    ],
)
def test_snapshot_rejects_invalid_contract(field, value) -> None:
    fake, _ = setup_commit_case()
    with pytest.raises(PortContractError) as caught:
        replace(fake.mutation_snapshot, **{field: value})
    assert caught.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", ""),
        ("run_id", " "),
        ("run_id", object()),
        ("retry_intent_digest", object()),
        ("attempt_id", ""),
        ("attempt_id", " "),
        ("attempt_id", object()),
    ],
)
def test_retry_commit_side_effect_rejects_invalid_contract(field, value) -> None:
    fake, command = setup_commit_case()
    side_effect = RetryCommitSideEffect(
        command.receipt.intent.run_id,
        command.receipt.intent.digest,
        command.new_attempt_id,
    )
    with pytest.raises(PortContractError) as caught:
        replace(side_effect, **{field: value})
    assert caught.value.field == field


@pytest.mark.parametrize(("field", "value"), [("commit", object()), ("receipt_digest", object())])
def test_stored_retry_commit_rejects_invalid_contract(field, value) -> None:
    fake, command = setup_commit_case()
    commit = fake.mutation_snapshot.run.commit_start(
        assignment_id=command.assignment_id,
        worker=command.worker,
        start_commit_key=command.start_commit_key,
        spec_digest=command.spec_digest,
        new_attempt_id=command.new_attempt_id,
        observed_at=command.observed_at,
        expected_version=command.expected_run_version,
    )
    stored = StoredRetryCommit(commit, command.receipt.digest)
    with pytest.raises(PortContractError) as caught:
        replace(stored, **{field: value})
    assert caught.value.field == field


def test_retry_commit_start_gateway_protocol_is_runtime_checkable() -> None:
    class Stub:
        async def require_current_authority(self, *, receipt):
            return None

        async def lookup_stored(self, *, identity_scope):
            return None

        async def require_receipt_for_update(self, *, retry_intent_digest):
            return None

        async def get_snapshot_for_update(self, *, run_id):
            return None

        async def publish(self, *, publication):
            return None

    assert isinstance(Stub(), RetryCommitStartGateway)


@pytest.mark.parametrize(
    "field",
    ["identity_scope", "authority", "expected_snapshot", "receipt", "commit", "side_effect"],
)
def test_publication_rejects_invalid_participants(field) -> None:
    fake, command = setup_commit_case()
    snapshot = fake.mutation_snapshot
    commit = snapshot.run.commit_start(
        assignment_id=command.assignment_id,
        worker=command.worker,
        start_commit_key=command.start_commit_key,
        spec_digest=command.spec_digest,
        new_attempt_id=command.new_attempt_id,
        observed_at=command.observed_at,
        expected_version=command.expected_run_version,
    )
    publication = RetryCommitPublication(
        command.identity_scope,
        fake.authority,
        snapshot,
        command.receipt,
        commit,
        RetryCommitSideEffect(
            command.receipt.intent.run_id, command.receipt.intent.digest, commit.attempt.id
        ),
    )
    value = ("", "key") if field == "identity_scope" else object()
    with pytest.raises(PortContractError):
        replace(publication, **{field: value})


def test_publication_rejects_binding_mismatch() -> None:
    fake, command = setup_commit_case()
    snapshot = fake.mutation_snapshot
    commit = snapshot.run.commit_start(
        assignment_id=command.assignment_id,
        worker=command.worker,
        start_commit_key=command.start_commit_key,
        spec_digest=command.spec_digest,
        new_attempt_id=command.new_attempt_id,
        observed_at=command.observed_at,
        expected_version=command.expected_run_version,
    )
    with pytest.raises(PortContractError, match="binding"):
        RetryCommitPublication(
            command.identity_scope,
            fake.authority,
            snapshot,
            command.receipt,
            commit,
            RetryCommitSideEffect(
                command.receipt.intent.run_id, command.receipt.intent.digest, "wrong"
            ),
        )


def test_receipt_matrix_rejects_missing_or_wrong_duplicate_consumption() -> None:
    _, duplicate = setup_commit_case(duplicate=True)
    with pytest.raises(PortContractError):
        replace(duplicate.receipt, duplicate_acceptance_consumption=None)
    _, stopped = setup_commit_case()
    with pytest.raises(PortContractError):
        replace(
            stopped.receipt,
            duplicate_acceptance_consumption=duplicate.receipt.duplicate_acceptance_consumption,
        )


def test_policy_receipt_and_authority_union() -> None:
    fake, command = setup_policy_case()
    reservation = RetryBudgetReservation.from_decision(command.decision)
    receipt = RetryQueueReceipt(
        command.candidate_intent,
        reservation,
        None,
        command.decision.decision_digest,
        command.identity_scope,
        fake.mutation_snapshot.run.version + 1,
        fake.authority.writer_digest,
        fake.authority.write_epoch,
    )
    assert _authority_matches(fake.authority, receipt)
    assert not _authority_matches(replace(fake.authority, authority_version=999), receipt)
    assert receipt.digest


def test_duplicate_authority_rejects_consumption_bound_to_other_basis() -> None:
    fake, command = setup_commit_case(duplicate=True)
    assert not _authority_matches(
        replace(fake.authority, risk_acceptance_basis_digest=_digest("other")),
        command.receipt,
    )


def test_receipt_and_queue_result_reject_invalid_contracts() -> None:
    _, stopped = setup_commit_case()
    _, duplicate = setup_commit_case(duplicate=True)
    policy_fake, policy_command = setup_policy_case()
    reservation = RetryBudgetReservation.from_decision(policy_command.decision)
    policy_receipt = RetryQueueReceipt(
        policy_command.candidate_intent,
        reservation,
        None,
        policy_command.decision.decision_digest,
        policy_command.identity_scope,
        policy_fake.mutation_snapshot.run.version + 1,
        policy_fake.authority.writer_digest,
        policy_fake.authority.write_epoch,
    )
    invalid_receipts = [
        {"intent": object()},
        {"queue_decision_digest": object()},
        {"queue_identity_scope": ("", "key")},
        {"reservation": reservation},
        {"duplicate_acceptance_consumption": duplicate.receipt.duplicate_acceptance_consumption},
    ]
    for changes in invalid_receipts:
        with pytest.raises(PortContractError):
            replace(stopped.receipt, **changes)
    with pytest.raises(PortContractError):
        replace(policy_receipt, reservation=None)
    with pytest.raises(PortContractError):
        replace(policy_receipt, queue_decision_digest=_digest("other-decision"))
    with pytest.raises(PortContractError):
        replace(
            policy_receipt,
            duplicate_acceptance_consumption=duplicate.receipt.duplicate_acceptance_consumption,
        )
    with pytest.raises(PortContractError):
        replace(
            policy_receipt,
            reservation=replace(reservation, retry_intent_digest=_digest("other")),
        )
    with pytest.raises(PortContractError):
        RetryQueuePublicationResult(object(), None)
    with pytest.raises(PortContractError):
        RetryQueuePublicationResult(
            RunRetryProjection(
                policy_fake.mutation_snapshot.run,
                policy_command.decision.decision_digest,
                policy_command.candidate_intent.digest,
            ),
            None,
        )
