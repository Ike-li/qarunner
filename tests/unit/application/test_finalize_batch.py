"""T-M0-STATE-001H H5: deterministic Batch finalization application UoW."""

import pytest


@pytest.mark.asyncio
async def test_exact_replay_returns_stored_projection_before_stale_batch_cas() -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationProjection,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    stored = BatchFinalizationProjection(
        batch_id=candidate.batch_id,
        source_batch_version=candidate.source_batch_version,
        batch_version=candidate.source_batch_version + 1,
        outcome=candidate.batch_outcome,
        basis_digest=candidate.basis_digest,
    )
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(
            source_snapshot=BatchFinalizationSourceSnapshot.from_basis(candidate),
            authority_digest=_basis_inputs()["resolution_set"].resolution_set_digest,
            write_epoch=1,
        ),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            batch_id=candidate.batch_id,
            batch_version=candidate.source_batch_version,
            state=__import__("qarunner.domain", fromlist=["BatchState"]).BatchState.FINALIZING,
            source_snapshot=BatchFinalizationSourceSnapshot.from_basis(candidate),
        ),
        projection=stored,
        basis=candidate,
    )

    result = await FinalizeBatch(gateway=fake).execute(
        FinalizeBatchCommand(candidate_basis=candidate, expected_batch_version=0)
    )

    assert result.replayed and result.projection == stored
    assert fake.mutation_snapshot_reads == 0
    assert (fake.basis_count, fake.audit_count, fake.outbox_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_new_finalization_atomically_publishes_basis_terminal_audit_and_outbox() -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(
            source_snapshot=BatchFinalizationSourceSnapshot.from_basis(candidate),
            authority_digest=candidate.completeness_proof_digest,
            write_epoch=1,
        ),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            batch_id=candidate.batch_id,
            batch_version=candidate.source_batch_version,
            state=__import__("qarunner.domain", fromlist=["BatchState"]).BatchState.FINALIZING,
            source_snapshot=BatchFinalizationSourceSnapshot.from_basis(candidate),
        ),
    )

    result = await FinalizeBatch(gateway=fake).execute(
        FinalizeBatchCommand(
            candidate_basis=candidate,
            expected_batch_version=candidate.source_batch_version,
        )
    )

    assert not result.replayed
    assert result.projection.outcome is candidate.batch_outcome
    assert result.projection.basis_digest == candidate.basis_digest
    assert next(iter(fake.bases.values())) == candidate
    assert (fake.publication_commits, fake.basis_count, fake.audit_count, fake.outbox_count) == (
        1,
        1,
        1,
        1,
    )


@pytest.mark.asyncio
async def test_source_authority_mismatch_is_rejected_before_stored_replay_lookup() -> None:
    from dataclasses import replace

    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis, BatchState

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(candidate)
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(
            source_snapshot=replace(sources, success_policy_digest=_digest("forged-policy")),
            authority_digest=_digest("authority"),
            write_epoch=1,
        ),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            batch_id=candidate.batch_id,
            batch_version=candidate.source_batch_version,
            state=BatchState.FINALIZING,
            source_snapshot=sources,
        ),
    )

    with pytest.raises(ValueError, match="source_snapshot"):
        await FinalizeBatch(gateway=fake).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )
    assert fake.stored_lookups == 0


@pytest.mark.asyncio
async def test_locked_source_ref_drift_discards_candidate_before_publication() -> None:
    from dataclasses import replace

    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis, BatchState

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(candidate)
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(sources, _digest("authority"), 1),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            batch_id=candidate.batch_id,
            batch_version=candidate.source_batch_version,
            state=BatchState.FINALIZING,
            source_snapshot=replace(sources, shard_plan_digest=_digest("drifted-plan")),
        ),
    )

    with pytest.raises(ValueError, match="source_snapshot"):
        await FinalizeBatch(gateway=fake).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )
    assert fake.publication_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["basis", "projection", "audit", "outbox", "commit"])
async def test_publication_fault_rolls_back_every_participant(point: str) -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis, BatchState

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(candidate)
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(sources, _digest("authority"), 1),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            candidate.batch_id,
            candidate.source_batch_version,
            BatchState.FINALIZING,
            sources,
        ),
        fault_at=point,
    )

    with pytest.raises(RuntimeError, match=point):
        await FinalizeBatch(gateway=fake).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )
    assert (fake.basis_count, fake.audit_count, fake.outbox_count) == (0, 0, 0)
    assert fake.publication_commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "error_name"),
    [
        ({"authority_available": False}, "AuthorityProjectionUnavailable"),
        ({"authority_allowed": False}, "AuthorityPermissionDenied"),
        ({"authority_current": False}, "AuthorityStateConflict"),
    ],
)
async def test_live_authority_failure_precedes_stored_replay(changes, error_name) -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports import batch_preexecution as authority_errors
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis, BatchState

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(candidate)
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(sources, _digest("authority"), 1),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            candidate.batch_id,
            candidate.source_batch_version,
            BatchState.FINALIZING,
            sources,
        ),
        **changes,
    )
    with pytest.raises(getattr(authority_errors, error_name)):
        await FinalizeBatch(gateway=fake).execute(
            FinalizeBatchCommand(candidate, candidate.source_batch_version)
        )
    assert fake.stored_lookups == 0


@pytest.mark.asyncio
async def test_same_identity_different_digest_conflicts_before_batch_cas() -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationProjection,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis, BatchState

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(candidate)
    stored = BatchFinalizationProjection(
        candidate.batch_id,
        candidate.source_batch_version,
        candidate.source_batch_version + 1,
        candidate.batch_outcome,
        _digest("different-basis"),
    )
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(sources, _digest("authority"), 1),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            candidate.batch_id,
            candidate.source_batch_version,
            BatchState.FINALIZING,
            sources,
        ),
    )
    fake.projections[fake.authority.identity_scope] = stored
    fake.basis_digests[fake.authority.identity_scope] = stored.basis_digest

    with pytest.raises(ValueError, match="idempotency"):
        await FinalizeBatch(gateway=fake).execute(FinalizeBatchCommand(candidate, 0))
    assert fake.mutation_snapshot_reads == 0


@pytest.mark.asyncio
async def test_new_mutation_rejects_stale_batch_cas_before_publication() -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis, BatchState

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(candidate)
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(sources, _digest("authority"), 1),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            candidate.batch_id,
            candidate.source_batch_version,
            BatchState.FINALIZING,
            sources,
        ),
    )
    with pytest.raises(ValueError, match="version"):
        await FinalizeBatch(gateway=fake).execute(FinalizeBatchCommand(candidate, 99))
    assert fake.publication_attempts == 0


@pytest.mark.asyncio
async def test_publish_defends_response_loss_replay_conflict_and_snapshot_drift() -> None:
    from dataclasses import replace

    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.batch_finalization import FinalizeBatch, FinalizeBatchCommand
    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.application.ports.batch_preexecution import AuthorityStateConflict
    from qarunner.domain import BatchFinalizationBasis, BatchState

    candidate = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(candidate)
    fake = InMemoryBatchFinalizationGateway(
        authority=FinalizeBatchAuthority(sources, _digest("authority"), 1),
        mutation_snapshot=BatchFinalizationMutationSnapshot(
            candidate.batch_id,
            candidate.source_batch_version,
            BatchState.FINALIZING,
            sources,
        ),
    )
    await FinalizeBatch(gateway=fake).execute(
        FinalizeBatchCommand(candidate, candidate.source_batch_version)
    )
    publication = fake.last_publication
    assert publication is not None
    replay = await fake.publish_finalization(publication=publication)
    assert replay.replayed

    fake.basis_digests[publication.identity_scope] = _digest("conflicting-stored-basis")
    with pytest.raises(ValueError, match="idempotency"):
        await fake.publish_finalization(publication=publication)

    fake.basis_digests.clear()
    fake.projections.clear()
    fake.mutation_snapshot = replace(
        fake.mutation_snapshot,
        source_snapshot=replace(fake.mutation_snapshot.source_snapshot, source_batch_version=99),
        batch_version=99,
    )
    with pytest.raises(ValueError, match="version"):
        await fake.publish_finalization(publication=publication)

    fake.mutation_snapshot = publication.expected_snapshot
    fake.authority = replace(fake.authority, write_epoch=2)
    with pytest.raises(AuthorityStateConflict, match="superseded"):
        await fake.publish_finalization(publication=publication)
