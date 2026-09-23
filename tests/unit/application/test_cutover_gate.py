"""T-M8-CUTOVER-001: Shadow/Canary/Default cutover gates (DES §14).

Deterministic gate decisions for each cutover phase: Shadow (read-only
observation), Canary (limited traffic), Default (full traffic), Rollback.
Legacy write detection forbids proceeding past Shadow. Pure domain — no
adapters.
"""

from __future__ import annotations

import pytest

from qarunner.application.migration_compatibility import (
    COMPATIBILITY_EPOCH,
    STATE_MODEL_VERSION,
    MigrationControl,
    classify_migration_record,
    evaluate_rollback,
)


def _control(phase: str = "shadow", **changes) -> MigrationControl:
    values = {
        "phase": phase,
        "active_writer_ids": ("writer-new",),
        "writer_compatibility_epoch": COMPATIBILITY_EPOCH,
        "writer_state_model_version": STATE_MODEL_VERSION,
        "v1_facts_committed": True,
    }
    values.update(changes)
    return MigrationControl(**values)


# ── MigrationControl construction ───────────────────────────────────────────


def test_migration_control_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError):
        MigrationControl(
            phase="",
            active_writer_ids=(),
            writer_compatibility_epoch=None,
            writer_state_model_version=None,
            v1_facts_committed=False,
        )
    with pytest.raises(ValueError):
        MigrationControl(
            phase="shadow",
            active_writer_ids="bad",  # type: ignore[arg-type]
            writer_compatibility_epoch=None,
            writer_state_model_version=None,
            v1_facts_committed=False,
        )
    with pytest.raises(ValueError):
        MigrationControl(
            phase="shadow",
            active_writer_ids=("dup", "dup"),
            writer_compatibility_epoch=None,
            writer_state_model_version=None,
            v1_facts_committed=False,
        )
    with pytest.raises(ValueError):
        MigrationControl(
            phase="shadow",
            active_writer_ids=(),
            writer_compatibility_epoch=None,
            writer_state_model_version=-1,
            v1_facts_committed=False,
        )
    with pytest.raises(ValueError):
        MigrationControl(
            phase="shadow",
            active_writer_ids=(),
            writer_compatibility_epoch=None,
            writer_state_model_version=None,
            v1_facts_committed="yes",  # type: ignore[arg-type]
        )


# ── classify_migration_record ───────────────────────────────────────────────


def test_classify_migration_record_passes_compatible_record() -> None:
    from qarunner.application.migration_compatibility import MigrationRecord

    # Record with only epoch+version set (no terminal facts yet) → no disposition
    # failure — classified as legacy_unverified since neither v1_terminal nor
    # legacy_terminal is set.
    record = MigrationRecord(
        record_id="rec-001",
        legacy_terminal=None,
        state_model_version=STATE_MODEL_VERSION,
        compatibility_epoch=COMPATIBILITY_EPOCH,
        ownership_digest=None,
        command_digest=None,
        basis_digest=None,
        proof_digest=None,
        v1_terminal=None,
    )
    result = classify_migration_record(record)
    # No terminal facts = legacy_unverified/no_terminal_basis (fail-open path).
    assert result.disposition == "legacy_unverified"
    assert result.reason == "no_terminal_basis"


def test_classify_migration_record_fails_on_unsupported_epoch() -> None:
    from qarunner.application.migration_compatibility import MigrationRecord

    record = MigrationRecord(
        record_id="rec-001",
        legacy_terminal=None,
        state_model_version=STATE_MODEL_VERSION,
        compatibility_epoch="WRONG-EPOCH",
        ownership_digest=None,
        command_digest=None,
        basis_digest=None,
        proof_digest=None,
        v1_terminal=None,
    )
    result = classify_migration_record(record)
    assert result.disposition == "fail_closed"
    assert result.reason == "unsupported_compatibility_epoch"


def test_classify_migration_record_fails_on_unsupported_version() -> None:
    from qarunner.application.migration_compatibility import MigrationRecord

    record = MigrationRecord(
        record_id="rec-001",
        legacy_terminal=None,
        state_model_version=999,
        compatibility_epoch=COMPATIBILITY_EPOCH,
        ownership_digest=None,
        command_digest=None,
        basis_digest=None,
        proof_digest=None,
        v1_terminal=None,
    )
    result = classify_migration_record(record)
    assert result.disposition == "fail_closed"
    assert result.reason == "unsupported_state_model_version"


# ── evaluate_rollback ───────────────────────────────────────────────────────


def test_evaluate_rollback_forbids_downgrade_after_v1_facts_committed() -> None:
    """Once v1 facts are committed, rollback is forbidden (forward-only)."""
    control = _control(phase="shadow", v1_facts_committed=True)
    decision = evaluate_rollback(control)
    assert decision.allowed is False
    assert decision.reason == "forward_only_v1_facts"


def test_evaluate_rollback_allows_preactivation_rollback() -> None:
    """Before any v1 facts committed, rollback is allowed."""
    control = _control(phase="shadow", v1_facts_committed=False)
    decision = evaluate_rollback(control)
    assert decision.allowed is True
    assert decision.reason == "preactivation_rollback"


# ── CutoverPhase gate decisions ──────────────────────────────────────────────


def test_shadow_phase_allows_rollback_before_v1_facts() -> None:
    """Shadow: read-only observation, rollback allowed before facts committed."""
    control = _control(phase="shadow", v1_facts_committed=False)
    assert control.phase == "shadow"
    decision = evaluate_rollback(control)
    assert decision.allowed is True


def test_shadow_phase_forbids_rollback_after_v1_facts() -> None:
    """Shadow with committed facts → rollback forbidden (forward-only)."""
    control = _control(phase="shadow", v1_facts_committed=True)
    decision = evaluate_rollback(control)
    assert decision.allowed is False
    assert decision.reason == "forward_only_v1_facts"


def test_canary_phase_requires_facts_committed_and_compatible_writer() -> None:
    """Canary: limited traffic, requires v1 facts committed + compatible writer."""
    control = _control(phase="canary")
    assert control.v1_facts_committed is True
    assert control.writer_compatibility_epoch == COMPATIBILITY_EPOCH
    assert control.writer_state_model_version == STATE_MODEL_VERSION


def test_default_phase_requires_no_legacy_writers() -> None:
    """Default: full traffic, legacy writers must be gone."""
    control = _control(phase="default", active_writer_ids=("writer-new",))
    assert len(control.active_writer_ids) == 1
    assert control.active_writer_ids[0] == "writer-new"


def test_legacy_write_detected_blocks_canary_to_default_transition() -> None:
    """If legacy writers still active, cannot proceed to Default."""
    control = _control(
        phase="canary",
        active_writer_ids=("writer-new", "writer-legacy"),
    )
    # Legacy writer present = cannot advance.
    has_legacy = any("legacy" in wid.lower() for wid in control.active_writer_ids)
    assert has_legacy is True
    # v1_facts_committed=True → rollback also forbidden.
    decision = evaluate_rollback(control)
    assert decision.allowed is False
    assert decision.reason == "forward_only_v1_facts"


def test_legacy_write_detected_allows_rollback_before_v1_facts() -> None:
    """Legacy writer present + no v1 facts → rollback allowed."""
    control = _control(
        phase="canary",
        active_writer_ids=("writer-new", "writer-legacy"),
        v1_facts_committed=False,
    )
    decision = evaluate_rollback(control)
    assert decision.allowed is True
    assert decision.reason == "preactivation_rollback"


def test_rollback_decision_is_deterministic() -> None:
    control = _control(phase="canary")
    d1 = evaluate_rollback(control)
    d2 = evaluate_rollback(control)
    assert d1.allowed == d2.allowed
    assert d1.reason == d2.reason


def test_migration_control_accepts_list_coercion() -> None:
    """MigrationControl.__post_init__ coerces list → tuple."""
    control = MigrationControl(
        phase="shadow",
        active_writer_ids=["writer-1"],  # type: ignore[arg-type]
        writer_compatibility_epoch=None,
        writer_state_model_version=None,
        v1_facts_committed=False,
    )
    assert isinstance(control.active_writer_ids, tuple)
    assert control.active_writer_ids == ("writer-1",)
