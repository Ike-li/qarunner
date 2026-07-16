"""T-M0-STATE-001H H4: immutable Batch finalization basis."""

from dataclasses import replace

import pytest


def test_terminal_run_ref_is_derived_from_closed_immutable_run_basis() -> None:
    from qarunner.domain import BatchTerminalRunRef
    from tests.unit.application.test_finalize_run import bound_basis
    from tests.unit.domain.test_run_finalization_basis import resolution_set

    resolved = resolution_set()
    basis = bound_basis(resolved)
    ref = BatchTerminalRunRef.from_basis(basis=basis)

    assert ref.canonical_payload() == {
        "run_id": resolved.run_id,
        "source_run_version": resolved.source_run_version,
        "run_basis_digest": basis.basis_digest.value,
        "run_outcome": "passed",
        "run_item_set_digest": resolved.run_item_set_digest.value,
        "original_resolution_set_digest": resolved.original_resolution_set_digest.value,
        "effective_resolution_set_digest": resolved.effective_resolution_set_digest.value,
        "item_resolution_set_digest": resolved.resolution_set_digest.value,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", ""),
        ("source_run_version", True),
        ("source_run_version", -1),
        ("run_basis_digest", object()),
        ("run_outcome", "passed"),
        ("run_item_set_digest", object()),
        ("original_resolution_set_digest", object()),
        ("effective_resolution_set_digest", object()),
        ("item_resolution_set_digest", object()),
    ],
)
def test_terminal_run_ref_rejects_invalid_immutable_fields(field, value) -> None:
    from qarunner.domain import BatchTerminalRunRef
    from tests.unit.application.test_finalize_run import bound_basis

    ref = BatchTerminalRunRef.from_basis(basis=bound_basis())
    with pytest.raises(ValueError) as caught:
        replace(ref, **{field: value})
    assert caught.value.field == field


def test_terminal_run_ref_rejects_untyped_basis() -> None:
    from qarunner.domain import BatchTerminalRunRef

    with pytest.raises(ValueError) as caught:
        BatchTerminalRunRef.from_basis(basis=object())
    assert caught.value.field == "basis"
