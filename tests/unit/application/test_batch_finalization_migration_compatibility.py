"""T-M0-STATE-001H H7b: pure finalization migration/rollback decisions."""

from dataclasses import replace

import pytest


def _facts():
    from qarunner.application.batch_finalization_migration_compatibility import (
        FinalizationBasisMigrationFact,
        FinalizationReadinessMigrationFact,
    )
    from qarunner.domain import BatchState

    readiness = FinalizationReadinessMigrationFact(
        "readiness-001", "batch-001", 9, "M0-STATE-V1", 1
    )
    basis = FinalizationBasisMigrationFact(
        "basis-001", "batch-001", 10, readiness.ref, BatchState.SUCCEEDED, "M0-STATE-V1", 1
    )
    return readiness, basis


def _record(**changes):
    from qarunner.application.batch_finalization_migration_compatibility import (
        FinalizationMigrationRecord,
    )
    from qarunner.domain import BatchState

    readiness, basis = _facts()
    values = {
        "record_id": "record-001",
        "batch_id": "batch-001",
        "current_batch_version": 11,
        "legacy_terminal": None,
        "state_model_version": 1,
        "compatibility_epoch": "M0-STATE-V1",
        "v1_state": BatchState.SUCCEEDED,
        "readiness_fact": readiness,
        "basis_fact": basis,
        "outcome": BatchState.SUCCEEDED,
    }
    values.update(changes)
    return FinalizationMigrationRecord(**values)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (_record(), ("trusted_v1", "terminal_facts_complete")),
        (
            _record(
                current_batch_version=10,
                v1_state=__import__(
                    "qarunner.domain", fromlist=["BatchState"]
                ).BatchState.FINALIZING,
                basis_fact=None,
                outcome=None,
            ),
            ("trusted_v1", "readiness_fact_complete"),
        ),
        (
            _record(
                legacy_terminal="succeeded",
                state_model_version=None,
                compatibility_epoch=None,
                v1_state=None,
                readiness_fact=None,
                basis_fact=None,
                outcome=None,
            ),
            ("legacy_unverified", "basisless_legacy_terminal"),
        ),
        (_record(legacy_terminal="failed"), ("conflict", "legacy_v1_terminal_mismatch")),
        (
            _record(compatibility_epoch="OLD"),
            ("fail_closed", "unsupported_compatibility_epoch"),
        ),
    ],
)
def test_record_classification_is_deterministic(record, expected) -> None:
    from qarunner.application.batch_finalization_migration_compatibility import (
        classify_finalization_record,
    )

    assert classify_finalization_record(record) == expected
    assert classify_finalization_record(record) == expected


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"state_model_version": 2}, "unsupported_state_model_version"),
        ({"compatibility_epoch": None}, "missing_compatibility_epoch"),
        ({"basis_fact": None}, "incomplete_terminal_facts"),
        ({"readiness_fact": None}, "incomplete_terminal_facts"),
        ({"outcome": None}, "incomplete_terminal_facts"),
        (
            {"outcome": __import__("qarunner.domain", fromlist=["BatchState"]).BatchState.FAILED},
            "terminal_outcome_mismatch",
        ),
        (
            {
                "basis_fact": replace(
                    _facts()[1],
                    outcome=__import__(
                        "qarunner.domain", fromlist=["BatchState"]
                    ).BatchState.FAILED,
                )
            },
            "basis_outcome_mismatch",
        ),
        (
            {"readiness_fact": replace(_facts()[0], batch_id="batch-other")},
            "terminal_fact_binding_mismatch",
        ),
        (
            {"basis_fact": replace(_facts()[1], readiness_ref="readiness-other")},
            "terminal_fact_binding_mismatch",
        ),
        (
            {
                "basis_fact": replace(_facts()[1], source_batch_version=99),
                "current_batch_version": 100,
            },
            "terminal_fact_binding_mismatch",
        ),
    ],
)
def test_terminal_requires_scope_version_and_readiness_basis_binding(changes, reason) -> None:
    from qarunner.application.batch_finalization_migration_compatibility import (
        classify_finalization_record,
    )

    assert classify_finalization_record(_record(**changes))[1] == reason


def test_finalizing_requires_same_batch_readiness_and_exact_version_step() -> None:
    from qarunner.application.batch_finalization_migration_compatibility import (
        classify_finalization_record,
    )
    from qarunner.domain import BatchState

    values = {
        "current_batch_version": 10,
        "v1_state": BatchState.FINALIZING,
        "basis_fact": None,
        "outcome": None,
    }
    assert classify_finalization_record(_record(**values))[0] == "trusted_v1"
    assert (
        classify_finalization_record(
            _record(**values, readiness_fact=replace(_facts()[0], batch_id="batch-other"))
        )[1]
        == "readiness_binding_mismatch"
    )
    assert classify_finalization_record(_record(**{**values, "current_batch_version": 99}))[1] == (
        "readiness_binding_mismatch"
    )
    assert classify_finalization_record(_record(**values, legacy_terminal="succeeded"))[1] == (
        "legacy_terminal_with_finalizing"
    )


def test_detached_facts_without_v1_state_never_upgrade_legacy() -> None:
    from qarunner.application.batch_finalization_migration_compatibility import (
        classify_finalization_record,
    )

    detached = _record(v1_state=None, outcome=None, basis_fact=None)
    assert classify_finalization_record(detached) == ("conflict", "detached_v1_facts")
    pending = replace(detached, readiness_fact=None)
    assert classify_finalization_record(pending) == ("legacy_pending", "no_terminal_fact")


def test_v1_state_requires_compatibility_and_supported_finalization_shape() -> None:
    from qarunner.application.batch_finalization_migration_compatibility import (
        classify_finalization_record,
    )
    from qarunner.domain import BatchState

    assert (
        classify_finalization_record(_record(compatibility_epoch=None, state_model_version=None))[
            1
        ]
        == "missing_v1_compatibility_binding"
    )
    assert (
        classify_finalization_record(
            _record(
                current_batch_version=10,
                v1_state=BatchState.FINALIZING,
                basis_fact=_facts()[1],
                outcome=None,
            )
        )[1]
        == "invalid_finalizing_facts"
    )
    assert classify_finalization_record(_record(v1_state=BatchState.RUNNING))[1] == (
        "unsupported_v1_state"
    )


def _control(*, legacy=False, incompatible_attempt=False, committed=()):
    from qarunner.application.batch_finalization_migration_compatibility import (
        FinalizationMigrationControl,
        FinalizationWriter,
        FinalizationWriterRole,
    )

    writers = [
        FinalizationWriter(
            "reconciler-v1", FinalizationWriterRole.BATCH_RECONCILER, "M0-STATE-V1", 1
        ),
        FinalizationWriter(
            "attempt-v1",
            FinalizationWriterRole.ATTEMPT_CREATOR,
            "OLD" if incompatible_attempt else "M0-STATE-V1",
            1,
        ),
    ]
    if legacy:
        writers.append(
            FinalizationWriter("legacy", FinalizationWriterRole.LEGACY_BATCH_TERMINAL, None, None)
        )
    return FinalizationMigrationControl(tuple(writers), committed)


def test_activation_and_rollback_fail_closed_on_declared_inventory() -> None:
    from qarunner.application.batch_finalization_migration_compatibility import (
        FinalizationMigrationControl,
        evaluate_finalization_activation,
        evaluate_finalization_rollback,
    )

    control = _control()
    assert evaluate_finalization_activation(control) == (True, "compatible_writer_set")
    assert (
        evaluate_finalization_activation(_control(legacy=True))[1]
        == "legacy_terminal_writer_active"
    )
    assert (
        evaluate_finalization_activation(_control(incompatible_attempt=True))[1]
        == "attempt_writer_epoch_mismatch"
    )
    assert (
        evaluate_finalization_activation(FinalizationMigrationControl((control.writers[1],), ()))[
            1
        ]
        == "single_reconciler_required"
    )
    incompatible = replace(control.writers[0], compatibility_epoch="OLD")
    assert (
        evaluate_finalization_activation(FinalizationMigrationControl((incompatible,), ()))[1]
        == "reconciler_epoch_mismatch"
    )
    assert evaluate_finalization_rollback(control) == (True, "precommit_rollback")
    assert (
        evaluate_finalization_rollback(_control(committed=("readiness-001",)))[1]
        == "forward_only_v1_facts"
    )


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("readiness", "ref", ""),
        ("readiness", "source_batch_version", True),
        ("readiness", "source_batch_version", None),
        ("basis", "readiness_ref", ""),
        (
            "basis",
            "outcome",
            __import__("qarunner.domain", fromlist=["BatchState"]).BatchState.FINALIZING,
        ),
        ("record", "record_id", ""),
        ("record", "current_batch_version", True),
        ("record", "readiness_fact", object()),
        ("record", "basis_fact", object()),
        ("record", "v1_state", "succeeded"),
        (
            "record",
            "outcome",
            __import__("qarunner.domain", fromlist=["BatchState"]).BatchState.FINALIZING,
        ),
        ("writer", "role", "batch_reconciler"),
        ("control", "writers", (object(),)),
        ("control", "committed_v1_fact_refs", object()),
        ("control", "committed_v1_fact_refs", ("",)),
        ("control", "committed_v1_fact_refs", ("z", "a")),
    ],
)
def test_contract_values_reject_untyped_or_ambiguous_inputs(target, field, value) -> None:
    from qarunner.application.batch_finalization_migration_compatibility import (
        FinalizationWriter,
        FinalizationWriterRole,
    )

    readiness, basis = _facts()
    values = {
        "readiness": readiness,
        "basis": basis,
        "record": _record(),
        "writer": FinalizationWriter(
            "writer", FinalizationWriterRole.BATCH_RECONCILER, "M0-STATE-V1", 1
        ),
        "control": _control(),
    }
    with pytest.raises(ValueError, match=field):
        replace(values[target], **{field: value})


def test_control_rejects_duplicate_writer_identity() -> None:
    control = _control()
    with pytest.raises(ValueError, match="writers"):
        replace(control, writers=(control.writers[0], control.writers[0]))
