"""T-M0-MODEL-001 seeded execution sequence acceptance tests."""

import pytest
from tests.model.seeded_execution import (
    CommandKind,
    TraceCommand,
    build_seeded_model_failure,
    generate_trace,
    minimize_failing_trace,
    run_seeded_execution_model,
)

EXPECTED_COMMAND_KINDS = {
    "offer_expired",
    "claim_at_expiry",
    "expire",
    "expire_replay",
    "offer_wrong_authority",
    "offer_stale_cas",
    "offer",
    "claim_wrong_worker",
    "claim",
    "commit_stale_cas",
    "commit_fault",
    "commit",
    "commit_replay",
    "historical_event",
    "event_wrong_fence",
    "event_stale_cas",
    "event",
    "event_conflict",
    "event_replay_superseded",
    "event_replay",
    "phase_provisioning",
    "phase_running",
    "mark_unknown",
    "terminal_event_replay",
    "terminal_event",
    "terminal_phase_regression",
    "adjudicate",
    "queue_retry",
    "queue_retry_replay",
}


@pytest.mark.parametrize("seed", [7, 41, 20260713, 0x5EED])
async def test_seeded_sequences_preserve_execution_authority(seed: int) -> None:
    report = await run_seeded_execution_model(seed=seed, attempt_count=3)

    assert report.attempt_count == 3
    assert report.final_fence == 3
    assert report.trace_digest.startswith("sha256:")
    assert report.trace_digest == report.recompute_trace_digest()
    assert {kind.value for kind in report.command_kinds} == EXPECTED_COMMAND_KINDS
    assert report.rejected_commands > 0
    assert report.commit_faults == 3


def test_seed_changes_materialized_command_order() -> None:
    traces = tuple(generate_trace(seed=seed, attempt_count=3) for seed in (7, 41, 20260713))
    ordered_kinds = {tuple(command.kind for command in trace) for trace in traces}

    assert len(ordered_kinds) == len(traces)


async def test_failure_reducer_is_one_minimal_and_retains_reproduction_evidence() -> None:
    invariant = "ordered-trigger"
    trace = (
        TraceCommand(CommandKind.EVENT_STALE_CAS, 1),
        TraceCommand(CommandKind.OFFER, 1),
        TraceCommand(CommandKind.CLAIM_WRONG_WORKER, 1),
        TraceCommand(CommandKind.EVENT_CONFLICT, 1),
        TraceCommand(CommandKind.COMMIT, 1),
        TraceCommand(CommandKind.TERMINAL_EVENT, 1),
    )

    async def reproduce(candidate: tuple[TraceCommand, ...]) -> str | None:
        kinds = tuple(command.kind for command in candidate)
        if CommandKind.OFFER not in kinds or CommandKind.COMMIT not in kinds:
            return None
        if kinds.index(CommandKind.OFFER) < kinds.index(CommandKind.COMMIT):
            return invariant
        return None

    minimized = await minimize_failing_trace(
        trace,
        invariant=invariant,
        reproduce=reproduce,
    )
    failure = await build_seeded_model_failure(
        seed=73,
        full_trace=trace,
        failing_trace=trace,
        invariant=invariant,
        detail="synthetic ordered failure",
        reproduce=reproduce,
    )

    assert minimized == (
        TraceCommand(CommandKind.OFFER, 1),
        TraceCommand(CommandKind.COMMIT, 1),
    )
    assert failure.minimal_trace == minimized
    for index in range(len(minimized)):
        assert await reproduce(minimized[:index] + minimized[index + 1 :]) is None
    message = str(failure)
    assert "seed=73" in message
    assert f"trace_digest={failure.trace_digest}" in message
    assert "invariant=ordered-trigger" in message
    assert "full_trace=" in message
    assert "minimal_trace=" in message
    assert "docker compose -f docker-compose.dev.yml exec backend" in message
