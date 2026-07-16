"""Value and binding contracts for deterministic Batch finalization ports."""

from dataclasses import replace

import pytest


def _values():
    from tests.unit.domain.test_batch_finalization_basis import _basis_inputs, _digest

    from qarunner.application.ports.batch_finalization import (
        BatchFinalizationMutationSnapshot,
        BatchFinalizationProjection,
        BatchFinalizationPublication,
        BatchFinalizationSideEffect,
        BatchFinalizationSourceSnapshot,
        FinalizeBatchAuthority,
    )
    from qarunner.domain import BatchFinalizationBasis, BatchState

    basis = BatchFinalizationBasis.build(**_basis_inputs())
    sources = BatchFinalizationSourceSnapshot.from_basis(basis)
    authority = FinalizeBatchAuthority(sources, _digest("authority"), 1)
    snapshot = BatchFinalizationMutationSnapshot(
        basis.batch_id, basis.source_batch_version, BatchState.FINALIZING, sources
    )
    projection = BatchFinalizationProjection(
        basis.batch_id,
        basis.source_batch_version,
        basis.source_batch_version + 1,
        basis.batch_outcome,
        basis.basis_digest,
    )
    side_effect = BatchFinalizationSideEffect(
        basis.batch_id, basis.basis_digest, basis.batch_outcome
    )
    publication = BatchFinalizationPublication(authority, snapshot, basis, projection, side_effect)
    return basis, sources, authority, snapshot, projection, side_effect, publication


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_id", ""),
        ("source_batch_version", True),
        ("item_count", 0),
        ("success_policy_version", 0),
        ("manifest_digest", object()),
        ("batch_cancellation_intent_digest", object()),
        ("terminal_run_refs", object()),
        ("non_run_refs", (object(),)),
        ("unknown_fact_refs", object()),
    ],
)
def test_source_snapshot_rejects_invalid_values(field, value) -> None:
    _, sources, *_ = _values()
    with pytest.raises(ValueError, match=field):
        replace(sources, **{field: value})


def test_source_snapshot_rejects_untyped_basis_and_supports_no_cancel_branch() -> None:
    from tests.unit.domain.test_batch_finalization_basis import _unknown_basis_inputs

    from qarunner.application.ports.batch_finalization import BatchFinalizationSourceSnapshot
    from qarunner.domain import BatchFinalizationBasis

    with pytest.raises(ValueError, match="basis"):
        BatchFinalizationSourceSnapshot.from_basis(object())
    basis = BatchFinalizationBasis.build(**_unknown_basis_inputs())
    assert (
        BatchFinalizationSourceSnapshot.from_basis(basis).batch_cancellation_intent_digest is None
    )


@pytest.mark.parametrize(
    ("index", "field", "value"),
    [
        (2, "source_snapshot", object()),
        (2, "authority_digest", object()),
        (2, "write_epoch", 0),
        (4, "batch_id", ""),
        (4, "source_batch_version", True),
        (4, "batch_version", 0),
        (4, "outcome", object()),
        (4, "basis_digest", object()),
        (3, "batch_id", ""),
        (3, "batch_version", True),
        (3, "state", object()),
        (3, "source_snapshot", object()),
        (5, "batch_id", ""),
        (5, "basis_digest", object()),
        (5, "outcome", object()),
    ],
)
def test_port_values_reject_invalid_contract(index, field, value) -> None:
    values = _values()
    with pytest.raises(ValueError, match=field):
        replace(values[index], **{field: value})


@pytest.mark.parametrize(
    "field", ["authority", "expected_snapshot", "basis", "projection", "side_effect"]
)
def test_publication_rejects_untyped_participants(field) -> None:
    publication = _values()[-1]
    with pytest.raises(ValueError, match=field):
        replace(publication, **{field: object()})


def test_publication_rejects_cross_participant_binding_drift() -> None:
    publication = _values()[-1]
    with pytest.raises(ValueError, match="binding"):
        replace(
            publication,
            projection=replace(publication.projection, batch_version=99),
        )


def test_publication_rejects_joint_authority_and_locked_source_rebinding() -> None:
    from tests.unit.domain.test_batch_finalization_basis import _digest

    publication = _values()[-1]
    forged_sources = replace(
        publication.authority.source_snapshot,
        success_policy_digest=_digest("forged-policy"),
    )
    forged_authority = replace(publication.authority, source_snapshot=forged_sources)
    forged_snapshot = replace(publication.expected_snapshot, source_snapshot=forged_sources)

    with pytest.raises(ValueError, match="binding"):
        replace(
            publication,
            authority=forged_authority,
            expected_snapshot=forged_snapshot,
        )


def test_locked_snapshot_rejects_batch_identity_or_version_detached_from_sources() -> None:
    snapshot = _values()[3]
    with pytest.raises(ValueError, match="batch_id"):
        replace(snapshot, batch_id="batch-other")
    with pytest.raises(ValueError, match="batch_version"):
        replace(snapshot, batch_version=snapshot.batch_version + 1)


def test_command_rejects_invalid_candidate_and_expected_version() -> None:
    from qarunner.application.batch_finalization import FinalizeBatchCommand

    basis = _values()[0]
    with pytest.raises(ValueError, match="candidate_basis"):
        FinalizeBatchCommand(object(), 0)
    with pytest.raises(ValueError, match="expected_batch_version"):
        FinalizeBatchCommand(basis, True)


@pytest.mark.asyncio
async def test_fake_rejects_wrong_batch_for_authority_and_locked_snapshot_reads() -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway

    from qarunner.application.ports import BatchFinalizationGateway
    from qarunner.application.ports.batch_preexecution import AuthorityPermissionDenied

    _, _, authority, snapshot, *_ = _values()
    fake = InMemoryBatchFinalizationGateway(
        authority=authority,
        mutation_snapshot=snapshot,
    )
    assert isinstance(fake, BatchFinalizationGateway)
    with pytest.raises(AuthorityPermissionDenied):
        await fake.require_finalization_authority(batch_id="batch-other")
    with pytest.raises(KeyError, match="batch-other"):
        await fake.get_mutation_snapshot_for_update(batch_id="batch-other")


def test_fake_rejects_seeded_projection_detached_from_authority_or_basis() -> None:
    from tests.fakes.greenfield import InMemoryBatchFinalizationGateway

    basis, _, authority, snapshot, projection, *_ = _values()
    with pytest.raises(ValueError, match="authority scope"):
        InMemoryBatchFinalizationGateway(
            authority=authority,
            mutation_snapshot=snapshot,
            projection=replace(projection, batch_id="batch-other"),
            basis=basis,
        )
    with pytest.raises(ValueError, match="basis mismatch"):
        InMemoryBatchFinalizationGateway(
            authority=authority,
            mutation_snapshot=snapshot,
            projection=replace(projection, basis_digest=authority.authority_digest),
            basis=basis,
        )
