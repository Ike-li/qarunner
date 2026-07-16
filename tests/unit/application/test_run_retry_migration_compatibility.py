"""Fixture-driven compatibility for immutable Run retry chains."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "greenfield" / "run_retry_migration"


def cases(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", cases("records.json"), ids=lambda value: value["id"])
def test_record_fixture_is_deterministic_and_non_mutating(case) -> None:
    from qarunner.application.run_retry_migration_compatibility import (
        RunRetryMigrationRecord,
        classify_run_retry_record,
    )

    record = RunRetryMigrationRecord(**case["record"])
    before = asdict(record)
    assert asdict(classify_run_retry_record(record)) == case["expected"]
    assert asdict(classify_run_retry_record(record)) == case["expected"]
    assert asdict(record) == before


@pytest.mark.parametrize("case", cases("controls.json"), ids=lambda value: value["id"])
def test_control_fixture_enforces_activation_and_forward_only(case) -> None:
    from qarunner.application.run_retry_migration_compatibility import (
        RunRetryMigrationControl,
        evaluate_run_retry_activation,
        evaluate_run_retry_rollback,
    )

    control = RunRetryMigrationControl(**case["control"])
    result = (
        evaluate_run_retry_activation(control)
        if case["operation"] == "activate"
        else evaluate_run_retry_rollback(control)
    )
    assert asdict(result) == case["expected"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_id", ""),
        ("intent_digest", " "),
        ("state_model_version", True),
        ("state_model_version", -1),
        ("retry_kind", "other"),
        ("retry_kind", object()),
        ("queued_run_version", True),
        ("queue_write_epoch", 0),
        ("queue_write_epoch", True),
        ("queue_write_epoch", -1),
    ],
)
def test_record_rejects_invalid_values(field, value) -> None:
    from qarunner.application.run_retry_migration_compatibility import RunRetryMigrationRecord

    values = dict(cases("records.json")[0]["record"])
    values[field] = value
    with pytest.raises(ValueError, match=field):
        RunRetryMigrationRecord(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"active_writer_ids": "w1"},
        {"active_writer_ids": ("",)},
        {"active_writer_ids": ("w1", "w1")},
        {"writer_compatibility_epoch": ""},
        {"writer_state_model_version": True},
        {"v1_retry_facts_committed": 1},
    ],
)
def test_control_rejects_invalid_values(changes) -> None:
    from qarunner.application.run_retry_migration_compatibility import RunRetryMigrationControl

    values = dict(cases("controls.json")[3]["control"])
    values.update(changes)
    with pytest.raises(ValueError):
        RunRetryMigrationControl(**values)


def test_control_normalizes_json_writer_list_to_tuple() -> None:
    from qarunner.application.run_retry_migration_compatibility import RunRetryMigrationControl

    control = RunRetryMigrationControl(**cases("controls.json")[3]["control"])
    assert control.active_writer_ids == ("w1",)
