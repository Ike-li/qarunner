"""M0 pure compatibility contract for Batch pre-execution migration."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "greenfield" / "batch_preexecution_migration"


def _cases(name: str) -> list[dict[str, object]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases("compatibility_cases.json"), ids=lambda case: case["id"])
def test_fixture_classifies_legacy_and_v1_records_without_side_effects(case) -> None:
    from qarunner.application.migration_compatibility import (
        MigrationRecord,
        classify_migration_record,
    )

    record = MigrationRecord(**case["record"])
    before = asdict(record)

    first = classify_migration_record(record)
    replay = classify_migration_record(record)

    assert asdict(first) == case["expected"]
    assert replay == first
    assert asdict(record) == before


@pytest.mark.parametrize("case", _cases("control_cases.json"), ids=lambda case: case["id"])
def test_fixture_enforces_single_writer_and_forward_only_rollback(case) -> None:
    from qarunner.application.migration_compatibility import (
        MigrationControl,
        evaluate_activation,
        evaluate_rollback,
    )

    control = MigrationControl(**case["control"])
    before = asdict(control)
    decision = (
        evaluate_rollback(control)
        if case["id"] in {"preactivate-rollback", "postactivate-forward-only"}
        else evaluate_activation(control)
    )

    assert asdict(decision) == case["expected"]
    assert asdict(control) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_id", ""),
        ("state_model_version", True),
        ("state_model_version", -1),
        ("compatibility_epoch", ""),
    ],
)
def test_record_rejects_malformed_contract_values(field: str, value: object) -> None:
    from qarunner.application.migration_compatibility import MigrationRecord

    values = dict(_cases("compatibility_cases.json")[0]["record"])
    values[field] = value

    with pytest.raises(ValueError, match=field):
        MigrationRecord(**values)


def test_control_rejects_duplicate_writer_identity() -> None:
    from qarunner.application.migration_compatibility import MigrationControl

    with pytest.raises(ValueError, match="active_writer_ids"):
        MigrationControl(
            phase="activate",
            active_writer_ids=("writer-1", "writer-1"),
            writer_compatibility_epoch="M0-STATE-V1",
            writer_state_model_version=1,
            v1_facts_committed=False,
        )


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"active_writer_ids": "writer-1"}, "active_writer_ids"),
        ({"active_writer_ids": ("",)}, "active_writer_ids"),
        ({"writer_state_model_version": True}, "writer_state_model_version"),
        ({"writer_state_model_version": -1}, "writer_state_model_version"),
        ({"v1_facts_committed": 1}, "v1_facts_committed"),
    ],
)
def test_control_rejects_malformed_values(changes: dict[str, object], field: str) -> None:
    from qarunner.application.migration_compatibility import MigrationControl

    values: dict[str, object] = {
        "phase": "activate",
        "active_writer_ids": ("batch-preexecution-v1",),
        "writer_compatibility_epoch": "M0-STATE-V1",
        "writer_state_model_version": 1,
        "v1_facts_committed": False,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=field):
        MigrationControl(**values)  # type: ignore[arg-type]


def test_record_rejects_blank_optional_digest() -> None:
    from qarunner.application.migration_compatibility import MigrationRecord

    values = dict(_cases("compatibility_cases.json")[0]["record"])
    values["basis_digest"] = " "

    with pytest.raises(ValueError, match="basis_digest"):
        MigrationRecord(**values)
