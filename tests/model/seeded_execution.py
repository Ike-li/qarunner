"""Seeded command/fault model for greenfield execution authority."""

from __future__ import annotations

import enum
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from qarunner.application.ports.facts import FactKey, VersionedFactCommand
from qarunner.domain import (
    AttemptAuthority,
    AttemptEvent,
    AttemptState,
    IdempotencyRecord,
    RetryIntent,
    Run,
    RunState,
    UnknownAdjudication,
    UnknownAdjudicationDecision,
    UnknownObservation,
    UnknownReason,
    UnknownSource,
    WorkerAuthority,
    WorkerGeneration,
    WorkerRef,
    WorkerState,
    canonical_digest,
)
from qarunner.domain.assignment import AssignmentState
from qarunner.domain.errors import (
    AssignmentConflict,
    AttemptEventRejected,
    AttemptUnknownReviewRequired,
    EventConflict,
    InvalidTransition,
    StaleFence,
    StaleGeneration,
    VersionConflict,
    WorkerGenerationConflict,
)
from tests.fakes.greenfield.transactions import (
    InMemoryApplicationState,
    InMemoryApplicationUnitOfWork,
)

_BASE_TIME = datetime(2026, 7, 13, 12, tzinfo=UTC)
_RUN_KEY = FactKey(kind="run", value="run-seeded-model")
_TERMINAL_ATTEMPT_STATES = frozenset(
    {
        AttemptState.PASSED,
        AttemptState.TEST_FAILED,
        AttemptState.INFRA_FAILED,
        AttemptState.CANCELLED,
        AttemptState.ATTEMPT_UNKNOWN,
    }
)


class CommandKind(enum.StrEnum):
    """Commands and injected faults exercised by every generated trace."""

    OFFER_EXPIRED = "offer_expired"
    CLAIM_AT_EXPIRY = "claim_at_expiry"
    EXPIRE = "expire"
    EXPIRE_REPLAY = "expire_replay"
    OFFER_WRONG_AUTHORITY = "offer_wrong_authority"
    OFFER_STALE_CAS = "offer_stale_cas"
    OFFER = "offer"
    CLAIM_WRONG_WORKER = "claim_wrong_worker"
    CLAIM = "claim"
    COMMIT_STALE_CAS = "commit_stale_cas"
    COMMIT_FAULT = "commit_fault"
    COMMIT = "commit"
    COMMIT_REPLAY = "commit_replay"
    HISTORICAL_EVENT = "historical_event"
    EVENT_WRONG_FENCE = "event_wrong_fence"
    EVENT_STALE_CAS = "event_stale_cas"
    EVENT = "event"
    EVENT_CONFLICT = "event_conflict"
    EVENT_REPLAY_SUPERSEDED = "event_replay_superseded"
    EVENT_REPLAY = "event_replay"
    PHASE_PROVISIONING = "phase_provisioning"
    PHASE_RUNNING = "phase_running"
    MARK_UNKNOWN = "mark_unknown"
    TERMINAL_EVENT_REPLAY = "terminal_event_replay"
    TERMINAL_EVENT = "terminal_event"
    TERMINAL_PHASE_REGRESSION = "terminal_phase_regression"
    ADJUDICATE = "adjudicate"
    QUEUE_RETRY = "queue_retry"
    QUEUE_RETRY_REPLAY = "queue_retry_replay"


REQUIRED_COMMAND_KINDS = frozenset(CommandKind)


@dataclass(frozen=True, slots=True)
class TraceCommand:
    """One fully reproducible command in a seeded trace."""

    kind: CommandKind
    cycle: int

    def payload(self) -> dict[str, int | str]:
        return {"kind": self.kind.value, "cycle": self.cycle}


@dataclass(frozen=True, slots=True)
class ModelRunReport:
    """Successful model run plus reproduction metadata."""

    seed: int
    trace: tuple[TraceCommand, ...]
    trace_digest: str
    attempt_count: int
    final_fence: int
    rejected_commands: int
    commit_faults: int

    @property
    def command_kinds(self) -> frozenset[CommandKind]:
        return frozenset(command.kind for command in self.trace)

    def recompute_trace_digest(self) -> str:
        return trace_digest(self.trace)


class ModelInvariantViolation(AssertionError):
    """A stable invariant identity used by failure reduction."""

    def __init__(self, invariant: str, detail: str) -> None:
        self.invariant = invariant
        self.detail = detail
        super().__init__(f"{invariant}: {detail}")


class TraceNotReplayable(RuntimeError):
    """A reduced trace removed a prerequisite command."""


FailureReproducer = Callable[[tuple[TraceCommand, ...]], Awaitable[str | None]]


class SeededModelFailure(AssertionError):
    """Reproduction evidence for one invariant failure."""

    def __init__(
        self,
        *,
        seed: int,
        full_trace: tuple[TraceCommand, ...],
        minimal_trace: tuple[TraceCommand, ...],
        invariant: str,
        detail: str,
    ) -> None:
        self.seed = seed
        self.full_trace = full_trace
        self.minimal_trace = minimal_trace
        self.invariant = invariant
        self.detail = detail
        self.trace_digest = trace_digest(full_trace)
        self.minimal_trace_digest = trace_digest(minimal_trace)
        replay_node = (
            "tests/unit/application/test_seeded_execution_sequences.py::"
            f"test_seeded_sequences_preserve_execution_authority[{seed}]"
        )
        message = "\n".join(
            (
                "seeded execution model invariant failed",
                f"seed={seed}",
                "generator_schema=qep.seeded-execution-trace.v1",
                f"trace_digest={self.trace_digest}",
                f"minimal_trace_digest={self.minimal_trace_digest}",
                f"invariant={invariant}",
                f"detail={detail}",
                f"full_trace={_render_trace(full_trace)}",
                f"minimal_trace={_render_trace(minimal_trace)}",
                "replay_command="
                "docker compose -f docker-compose.dev.yml exec backend "
                f"uv run pytest '{replay_node}' --no-cov -q",
            )
        )
        super().__init__(message)


def trace_digest(trace: tuple[TraceCommand, ...]) -> str:
    """Return a canonical digest that changes with command order."""
    return canonical_digest(
        schema_version="qep.seeded-execution-trace.v1",
        payload={"commands": [command.payload() for command in trace]},
    ).value


async def minimize_failing_trace(
    trace: tuple[TraceCommand, ...],
    *,
    invariant: str,
    reproduce: FailureReproducer,
) -> tuple[TraceCommand, ...]:
    """Delete arbitrary chunks, then prove the same failure is 1-minimal."""
    if await reproduce(trace) != invariant:
        raise ValueError("the supplied trace does not reproduce the invariant")
    current = trace
    granularity = 2
    while len(current) >= 2:
        chunk_size = (len(current) + granularity - 1) // granularity
        reduced = False
        for start in range(0, len(current), chunk_size):
            candidate = current[:start] + current[start + chunk_size :]
            if await reproduce(candidate) == invariant:
                current = candidate
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        if granularity >= len(current):
            break
        granularity = min(len(current), granularity * 2)

    index = 0
    while index < len(current):
        candidate = current[:index] + current[index + 1 :]
        if await reproduce(candidate) == invariant:
            current = candidate
            index = 0
            continue
        index += 1
    return current


async def build_seeded_model_failure(
    *,
    seed: int,
    full_trace: tuple[TraceCommand, ...],
    failing_trace: tuple[TraceCommand, ...],
    invariant: str,
    detail: str,
    reproduce: FailureReproducer,
) -> SeededModelFailure:
    """Minimize one materialized trace without regenerating from its seed."""
    minimal_trace = await minimize_failing_trace(
        failing_trace,
        invariant=invariant,
        reproduce=reproduce,
    )
    return SeededModelFailure(
        seed=seed,
        full_trace=full_trace,
        minimal_trace=minimal_trace,
        invariant=invariant,
        detail=detail,
    )


def _render_trace(trace: tuple[TraceCommand, ...]) -> str:
    return json.dumps(
        [command.payload() for command in trace],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def generate_trace(*, seed: int, attempt_count: int) -> tuple[TraceCommand, ...]:
    """Generate valid progress spines with seed-shuffled rejection/fault probes."""
    if attempt_count < 2:
        raise ValueError("attempt_count must exercise at least one adjudicated retry")
    rng = random.Random(seed)
    trace: list[TraceCommand] = []

    def append(kind: CommandKind, cycle: int) -> None:
        trace.append(TraceCommand(kind=kind, cycle=cycle))

    def append_shuffled(kinds: list[CommandKind], cycle: int) -> None:
        rng.shuffle(kinds)
        for kind in kinds:
            append(kind, cycle)

    for cycle in range(1, attempt_count + 1):
        append(CommandKind.OFFER_EXPIRED, cycle)
        append(CommandKind.CLAIM_AT_EXPIRY, cycle)
        append(CommandKind.EXPIRE, cycle)
        append(CommandKind.EXPIRE_REPLAY, cycle)
        append_shuffled(
            [CommandKind.OFFER_WRONG_AUTHORITY, CommandKind.OFFER_STALE_CAS],
            cycle,
        )
        append(CommandKind.OFFER, cycle)
        append(CommandKind.CLAIM_WRONG_WORKER, cycle)
        append(CommandKind.CLAIM, cycle)
        append_shuffled(
            [CommandKind.COMMIT_STALE_CAS, CommandKind.COMMIT_FAULT],
            cycle,
        )
        append(CommandKind.COMMIT, cycle)
        append(CommandKind.COMMIT_REPLAY, cycle)
        pre_event = [CommandKind.EVENT_WRONG_FENCE, CommandKind.EVENT_STALE_CAS]
        if cycle > 1:
            pre_event.append(CommandKind.HISTORICAL_EVENT)
        append_shuffled(pre_event, cycle)
        append(CommandKind.EVENT, cycle)
        append_shuffled(
            [CommandKind.EVENT_CONFLICT, CommandKind.EVENT_REPLAY_SUPERSEDED],
            cycle,
        )
        append(CommandKind.EVENT_REPLAY, cycle)
        append(CommandKind.PHASE_PROVISIONING, cycle)
        append(CommandKind.PHASE_RUNNING, cycle)
        append(CommandKind.MARK_UNKNOWN, cycle)
        append_shuffled(
            [
                CommandKind.TERMINAL_EVENT_REPLAY,
                CommandKind.TERMINAL_EVENT,
                CommandKind.TERMINAL_PHASE_REGRESSION,
            ],
            cycle,
        )
        if cycle < attempt_count:
            append(CommandKind.ADJUDICATE, cycle)
            append(CommandKind.QUEUE_RETRY, cycle)
            append(CommandKind.QUEUE_RETRY_REPLAY, cycle)
    return tuple(trace)


async def run_seeded_execution_model(
    *,
    seed: int,
    attempt_count: int,
) -> ModelRunReport:
    """Execute a generated trace and check authoritative facts after every command."""
    trace = generate_trace(seed=seed, attempt_count=attempt_count)
    runner = await _SeededExecutionRunner.create(seed=seed)
    for index, command in enumerate(trace):
        try:
            await runner.execute(command)
        except ModelInvariantViolation as violation:
            failing_trace = trace[: index + 1]

            async def reproduce(candidate: tuple[TraceCommand, ...]) -> str | None:
                replay = await _SeededExecutionRunner.create(seed=seed)
                try:
                    for replayed_command in candidate:
                        await replay.execute(replayed_command)
                except ModelInvariantViolation as replayed_violation:
                    return replayed_violation.invariant
                except Exception:
                    return None
                return None

            failure = await build_seeded_model_failure(
                seed=seed,
                full_trace=trace,
                failing_trace=failing_trace,
                invariant=violation.invariant,
                detail=violation.detail,
                reproduce=reproduce,
            )
            raise failure from violation
    return ModelRunReport(
        seed=seed,
        trace=trace,
        trace_digest=trace_digest(trace),
        attempt_count=len(runner.run.attempts),
        final_fence=runner.run.current_fence,
        rejected_commands=runner.rejected_commands,
        commit_faults=runner.commit_faults,
    )


class _SeededExecutionRunner:
    def __init__(self, *, seed: int) -> None:
        self.seed = seed
        self.state = InMemoryApplicationState()
        self.run = Run.create(run_id=_RUN_KEY.value)
        self.rejected_commands = 0
        self.commit_faults = 0
        self._fact_commands: dict[str, VersionedFactCommand[Run]] = {}
        self._terminal_states: dict[str, AttemptState] = {}

    @classmethod
    async def create(cls, *, seed: int) -> _SeededExecutionRunner:
        runner = cls(seed=seed)
        await runner._publish_initial()
        queued = runner.run.transition(RunState.QUEUED, expected_version=runner.run.version)
        await runner._publish_new(queued, operation="queue", cycle=0)
        await runner._assert_invariants(previous=runner.run)
        return runner

    async def execute(self, command: TraceCommand) -> None:
        previous = self.run
        try:
            await self._dispatch(command)
        except (ModelInvariantViolation, TraceNotReplayable):
            raise
        except Exception as error:
            reason = getattr(error, "reason", "none")
            raise ModelInvariantViolation(
                (f"command-error:{command.kind.value}:{type(error).__name__}:{reason}"),
                repr(error),
            ) from error
        await self._assert_invariants(previous=previous)

    async def _dispatch(self, command: TraceCommand) -> None:
        kind = command.kind
        cycle = command.cycle

        if kind is CommandKind.OFFER_EXPIRED:
            await self._offer(cycle=cycle, expired=True)
        elif kind is CommandKind.CLAIM_AT_EXPIRY:
            await self._claim_at_expiry(command)
        elif kind is CommandKind.EXPIRE:
            await self._expire(cycle=cycle)
        elif kind is CommandKind.EXPIRE_REPLAY:
            await self._expire_replay(cycle=cycle)
        elif kind is CommandKind.OFFER_WRONG_AUTHORITY:
            await self._offer_wrong_authority(command)
        elif kind is CommandKind.OFFER_STALE_CAS:
            await self._offer_stale_cas(command)
        elif kind is CommandKind.OFFER:
            await self._offer(cycle=cycle, expired=False)
        elif kind is CommandKind.CLAIM_WRONG_WORKER:
            await self._claim_wrong_worker(command)
        elif kind is CommandKind.CLAIM:
            await self._claim(cycle=cycle)
        elif kind is CommandKind.COMMIT_STALE_CAS:
            await self._commit_stale_cas(command)
        elif kind is CommandKind.COMMIT_FAULT:
            await self._commit(cycle=cycle, inject_fault=True)
        elif kind is CommandKind.COMMIT:
            await self._commit(cycle=cycle, inject_fault=False)
        elif kind is CommandKind.COMMIT_REPLAY:
            await self._commit_replay(cycle=cycle)
        elif kind is CommandKind.HISTORICAL_EVENT:
            await self._historical_event(command)
        elif kind is CommandKind.EVENT_WRONG_FENCE:
            await self._event_wrong_fence(command)
        elif kind is CommandKind.EVENT_STALE_CAS:
            await self._event_stale_cas(command)
        elif kind is CommandKind.EVENT:
            await self._record_event(cycle=cycle)
        elif kind is CommandKind.EVENT_CONFLICT:
            await self._event_conflict(command)
        elif kind is CommandKind.EVENT_REPLAY_SUPERSEDED:
            await self._event_replay_superseded(command)
        elif kind is CommandKind.EVENT_REPLAY:
            await self._event_replay(cycle=cycle)
        elif kind is CommandKind.PHASE_PROVISIONING:
            await self._transition_attempt(cycle=cycle, target=AttemptState.PROVISIONING)
        elif kind is CommandKind.PHASE_RUNNING:
            await self._transition_attempt(cycle=cycle, target=AttemptState.RUNNING)
        elif kind is CommandKind.MARK_UNKNOWN:
            await self._mark_unknown(cycle=cycle)
        elif kind is CommandKind.TERMINAL_EVENT_REPLAY:
            await self._terminal_event_replay(cycle=cycle)
        elif kind is CommandKind.TERMINAL_EVENT:
            await self._terminal_event(command)
        elif kind is CommandKind.TERMINAL_PHASE_REGRESSION:
            await self._terminal_phase_regression(command)
        elif kind is CommandKind.ADJUDICATE:
            await self._adjudicate(cycle=cycle)
        elif kind is CommandKind.QUEUE_RETRY:
            await self._queue_retry(cycle=cycle)
        elif kind is CommandKind.QUEUE_RETRY_REPLAY:
            await self._queue_retry_replay(cycle=cycle)
        else:  # pragma: no cover - enum exhaustiveness guard
            raise TraceNotReplayable(f"unsupported command: {kind}")

    async def _publish_initial(self) -> None:
        command = self._fact_command(
            fact=self.run,
            expected_version=None,
            operation="create",
            cycle=0,
        )
        async with InMemoryApplicationUnitOfWork(self.state) as unit_of_work:
            result = await unit_of_work.facts.commit(command)
            await unit_of_work.commit()
        if result.replayed:
            self._violate("initial-create", "new model state replayed its create")

    async def _publish_new(
        self,
        proposed: Run,
        *,
        operation: str,
        cycle: int,
        inject_fault: bool = False,
    ) -> None:
        if proposed.version != self.run.version + 1:
            self._violate(
                "version-step",
                f"{operation} proposed {proposed.version} after {self.run.version}",
            )
        command = self._fact_commands.get(operation)
        if command is None:
            command = self._fact_command(
                fact=proposed,
                expected_version=self.run.version,
                operation=operation,
                cycle=cycle,
            )
        elif command.fact != proposed:
            self._violate("fault-retry", f"{operation} changed after an aborted commit")

        commit_error = RuntimeError("seeded commit fault") if inject_fault else None
        try:
            async with InMemoryApplicationUnitOfWork(
                self.state,
                commit_error=commit_error,
            ) as unit_of_work:
                result = await unit_of_work.facts.commit(command)
                await unit_of_work.commit()
        except RuntimeError as error:
            if not inject_fault or str(error) != "seeded commit fault":
                raise
            self._fact_commands[operation] = command
            self.commit_faults += 1
            await self._assert_durable_current()
            return
        if inject_fault:
            self._violate("commit-fault", f"{operation} unexpectedly committed")
        if result.replayed:
            self._violate("fact-replay", f"first durable {operation} reported replay")
        self._fact_commands[operation] = command
        self.run = proposed

    async def _publish_replay(self, *, operation: str) -> None:
        command = self._fact_commands.get(operation)
        if command is None:
            raise TraceNotReplayable(f"missing original fact command: {operation}")
        async with InMemoryApplicationUnitOfWork(self.state) as unit_of_work:
            result = await unit_of_work.facts.commit(command)
            await unit_of_work.commit()
        if not result.replayed:
            self._violate("fact-replay", f"{operation} did not replay")
        await self._assert_durable_current()

    def _fact_command(
        self,
        *,
        fact: Run,
        expected_version: int | None,
        operation: str,
        cycle: int,
    ) -> VersionedFactCommand[Run]:
        return VersionedFactCommand(
            key=_RUN_KEY,
            expected_version=expected_version,
            fact=fact,
            idempotency=IdempotencyRecord.create(
                scope=f"run:{self.run.id}:seeded-model",
                key=operation,
                request_digest=canonical_digest(
                    schema_version="qep.seeded-execution-command.v1",
                    payload={"operation": operation, "cycle": cycle},
                ),
                response_status=200,
                response_ref=fact.id,
            ),
        )

    async def _offer(self, *, cycle: int, expired: bool) -> None:
        worker, authority = _ready_worker(cycle)
        offered_at, expires_at = (
            _expired_window(cycle) if expired else _execution_window(cycle)[:2]
        )
        assignment_id = _assignment_id(cycle, expired=expired)
        proposed = self.run.offer_assignment(
            assignment_id=assignment_id,
            worker=worker,
            worker_authority=authority,
            spec_digest=_spec_digest(),
            offered_at=offered_at,
            expires_at=expires_at,
            expected_version=self.run.version,
        )
        operation = f"offer-expired-{cycle}" if expired else f"offer-{cycle}"
        await self._publish_new(proposed, operation=operation, cycle=cycle)

    async def _claim_at_expiry(self, command: TraceCommand) -> None:
        _, expires_at = _expired_window(command.cycle)
        await self._expect_rejection(
            command,
            AssignmentConflict,
            lambda: self.run.claim_assignment(
                assignment_id=_assignment_id(command.cycle, expired=True),
                worker=_worker_ref(command.cycle),
                observed_at=expires_at,
                expected_version=self.run.version,
            ),
            reason="assignment_expired",
        )

    async def _expire(self, *, cycle: int) -> None:
        _, expires_at = _expired_window(cycle)
        proposed = self.run.expire_precommit_assignment(
            assignment_id=_assignment_id(cycle, expired=True),
            expiry_key=f"expiry-{cycle}",
            observed_at=expires_at,
            expected_version=self.run.version,
        )
        await self._publish_new(proposed, operation=f"expire-{cycle}", cycle=cycle)

    async def _expire_replay(self, *, cycle: int) -> None:
        _, expires_at = _expired_window(cycle)
        replay = self.run.expire_precommit_assignment(
            assignment_id=_assignment_id(cycle, expired=True),
            expiry_key=f"expiry-{cycle}",
            observed_at=expires_at,
            expected_version=self.run.version - 1,
        )
        if replay is not self.run:
            self._violate("domain-replay", f"expiry cycle {cycle} advanced state")
        await self._publish_replay(operation=f"expire-{cycle}")

    async def _offer_wrong_authority(self, command: TraceCommand) -> None:
        worker, _ = _ready_worker(command.cycle)
        offered_at, expires_at, _, _ = _execution_window(command.cycle)
        wrong_authority = WorkerAuthority(
            current_ref=WorkerRef(
                worker_id=worker.ref.worker_id,
                generation=worker.ref.generation + 100,
            )
        )
        await self._expect_rejection(
            command,
            WorkerGenerationConflict,
            lambda: self.run.offer_assignment(
                assignment_id=_assignment_id(command.cycle, expired=False),
                worker=worker,
                worker_authority=wrong_authority,
                spec_digest=_spec_digest(),
                offered_at=offered_at,
                expires_at=expires_at,
                expected_version=self.run.version,
            ),
            reason="generation_not_current",
        )

    async def _offer_stale_cas(self, command: TraceCommand) -> None:
        worker, authority = _ready_worker(command.cycle)
        offered_at, expires_at, _, _ = _execution_window(command.cycle)
        await self._expect_rejection(
            command,
            VersionConflict,
            lambda: self.run.offer_assignment(
                assignment_id=_assignment_id(command.cycle, expired=False),
                worker=worker,
                worker_authority=authority,
                spec_digest=_spec_digest(),
                offered_at=offered_at,
                expires_at=expires_at,
                expected_version=self.run.version - 1,
            ),
        )

    async def _claim_wrong_worker(self, command: TraceCommand) -> None:
        _, _, claimed_at, _ = _execution_window(command.cycle)
        await self._expect_rejection(
            command,
            AssignmentConflict,
            lambda: self.run.claim_assignment(
                assignment_id=_assignment_id(command.cycle, expired=False),
                worker=WorkerRef(worker_id="worker-model", generation=command.cycle + 100),
                observed_at=claimed_at,
                expected_version=self.run.version,
            ),
            reason="offer_mismatch",
        )

    async def _claim(self, *, cycle: int) -> None:
        _, _, claimed_at, _ = _execution_window(cycle)
        proposed = self.run.claim_assignment(
            assignment_id=_assignment_id(cycle, expired=False),
            worker=_worker_ref(cycle),
            observed_at=claimed_at,
            expected_version=self.run.version,
        )
        await self._publish_new(proposed, operation=f"claim-{cycle}", cycle=cycle)

    async def _commit_stale_cas(self, command: TraceCommand) -> None:
        _, _, _, committed_at = _execution_window(command.cycle)
        await self._expect_rejection(
            command,
            VersionConflict,
            lambda: self.run.commit_start(
                assignment_id=_assignment_id(command.cycle, expired=False),
                worker=_worker_ref(command.cycle),
                start_commit_key=f"commit-{command.cycle}",
                spec_digest=_spec_digest(),
                new_attempt_id=_attempt_id(command.cycle),
                observed_at=committed_at,
                expected_version=self.run.version - 1,
            ),
        )

    async def _commit(self, *, cycle: int, inject_fault: bool) -> None:
        _, _, _, committed_at = _execution_window(cycle)
        result = self.run.commit_start(
            assignment_id=_assignment_id(cycle, expired=False),
            worker=_worker_ref(cycle),
            start_commit_key=f"commit-{cycle}",
            spec_digest=_spec_digest(),
            new_attempt_id=_attempt_id(cycle),
            observed_at=committed_at,
            expected_version=self.run.version,
        )
        if result.replayed:
            self._violate("domain-replay", f"first commit cycle {cycle} replayed")
        await self._publish_new(
            result.run,
            operation=f"commit-{cycle}",
            cycle=cycle,
            inject_fault=inject_fault,
        )

    async def _commit_replay(self, *, cycle: int) -> None:
        _, _, _, committed_at = _execution_window(cycle)
        result = self.run.commit_start(
            assignment_id=_assignment_id(cycle, expired=False),
            worker=_worker_ref(cycle),
            start_commit_key=f"commit-{cycle}",
            spec_digest=_spec_digest(),
            new_attempt_id=_attempt_id(cycle),
            observed_at=committed_at,
            expected_version=self.run.version - 1,
        )
        if not result.replayed or result.run is not self.run:
            self._violate("domain-replay", f"commit cycle {cycle} was not exact")
        await self._publish_replay(operation=f"commit-{cycle}")

    async def _historical_event(self, command: TraceCommand) -> None:
        if len(self.run.attempts) < 2:
            raise TraceNotReplayable("historical event requires two attempts")
        historical = self.run.attempts[-2]
        current = self.run.attempts[-1]
        await self._expect_rejection(
            command,
            AttemptUnknownReviewRequired,
            lambda: self.run.record_current_attempt_event(
                attempt_id=historical.id,
                event=_event(command.cycle, suffix="historical"),
                authority=_attempt_authority(current),
                worker=current.worker,
                fence=current.fence,
                expected_version=self.run.version,
                expected_attempt_version=historical.version,
            ),
            reason="source_attempt_not_current",
        )

    async def _event_wrong_fence(self, command: TraceCommand) -> None:
        current = self._current_attempt()
        await self._expect_rejection(
            command,
            StaleFence,
            lambda: self.run.record_current_attempt_event(
                attempt_id=current.id,
                event=_event(command.cycle, suffix="wrong-fence"),
                authority=_attempt_authority(current),
                worker=current.worker,
                fence=current.fence - 1,
                expected_version=self.run.version,
                expected_attempt_version=current.version,
            ),
        )

    async def _event_stale_cas(self, command: TraceCommand) -> None:
        current = self._current_attempt()
        await self._expect_rejection(
            command,
            VersionConflict,
            lambda: self.run.record_current_attempt_event(
                attempt_id=current.id,
                event=_event(command.cycle, suffix="stale-cas"),
                authority=_attempt_authority(current),
                worker=current.worker,
                fence=current.fence,
                expected_version=self.run.version,
                expected_attempt_version=current.version + 1,
            ),
        )

    async def _record_event(self, *, cycle: int) -> None:
        current = self._current_attempt()
        proposed = self.run.record_current_attempt_event(
            attempt_id=current.id,
            event=_event(cycle),
            authority=_attempt_authority(current),
            worker=current.worker,
            fence=current.fence,
            expected_version=self.run.version,
            expected_attempt_version=current.version,
        )
        await self._publish_new(proposed, operation=f"event-{cycle}", cycle=cycle)

    async def _event_conflict(self, command: TraceCommand) -> None:
        current = self._current_attempt()
        await self._expect_rejection(
            command,
            EventConflict,
            lambda: self.run.record_current_attempt_event(
                attempt_id=current.id,
                event=_event(command.cycle, conflict=True),
                authority=_attempt_authority(current),
                worker=current.worker,
                fence=current.fence,
                expected_version=self.run.version,
                expected_attempt_version=current.version,
            ),
        )

    async def _event_replay_superseded(self, command: TraceCommand) -> None:
        current = self._current_attempt()
        superseded = AttemptAuthority(
            current_fence=current.fence,
            current_worker=WorkerRef(
                worker_id=current.worker.worker_id,
                generation=current.worker.generation + 100,
            ),
        )
        await self._expect_rejection(
            command,
            StaleGeneration,
            lambda: self.run.record_current_attempt_event(
                attempt_id=current.id,
                event=_event(command.cycle),
                authority=superseded,
                worker=current.worker,
                fence=current.fence,
                expected_version=0,
                expected_attempt_version=0,
            ),
        )

    async def _event_replay(self, *, cycle: int) -> None:
        current = self._current_attempt()
        replay = self.run.record_current_attempt_event(
            attempt_id=current.id,
            event=_event(cycle),
            authority=_attempt_authority(current),
            worker=current.worker,
            fence=current.fence,
            expected_version=0,
            expected_attempt_version=0,
        )
        if replay is not self.run:
            self._violate("domain-replay", f"event cycle {cycle} advanced state")
        await self._publish_replay(operation=f"event-{cycle}")

    async def _transition_attempt(self, *, cycle: int, target: AttemptState) -> None:
        current = self._current_attempt()
        proposed = self.run.transition_current_attempt(
            attempt_id=current.id,
            target=target,
            expected_version=self.run.version,
            expected_attempt_version=current.version,
        )
        operation = f"phase-{target.value}-{cycle}"
        await self._publish_new(proposed, operation=operation, cycle=cycle)

    async def _mark_unknown(self, *, cycle: int) -> None:
        current = self._current_attempt()
        proposed = self.run.mark_current_attempt_unknown(
            attempt_id=current.id,
            observation=_unknown_observation(cycle),
            expected_version=self.run.version,
            expected_attempt_version=current.version,
        )
        await self._publish_new(proposed, operation=f"unknown-{cycle}", cycle=cycle)

    async def _terminal_event_replay(self, *, cycle: int) -> None:
        current = self._current_attempt()
        replay = self.run.record_current_attempt_event(
            attempt_id=current.id,
            event=_event(cycle),
            authority=_attempt_authority(current),
            worker=current.worker,
            fence=current.fence,
            expected_version=0,
            expected_attempt_version=0,
        )
        if replay is not self.run:
            self._violate("terminal-replay", f"terminal cycle {cycle} advanced state")
        await self._publish_replay(operation=f"event-{cycle}")

    async def _terminal_event(self, command: TraceCommand) -> None:
        current = self._current_attempt()
        await self._expect_rejection(
            command,
            AttemptEventRejected,
            lambda: self.run.record_current_attempt_event(
                attempt_id=current.id,
                event=_event(command.cycle, suffix="terminal-new"),
                authority=_attempt_authority(current),
                worker=current.worker,
                fence=current.fence,
                expected_version=self.run.version,
                expected_attempt_version=current.version,
            ),
            reason="attempt_terminal",
        )

    async def _terminal_phase_regression(self, command: TraceCommand) -> None:
        current = self._current_attempt()
        await self._expect_rejection(
            command,
            InvalidTransition,
            lambda: self.run.transition_current_attempt(
                attempt_id=current.id,
                target=AttemptState.PROVISIONING,
                expected_version=self.run.version,
                expected_attempt_version=current.version,
            ),
        )

    async def _adjudicate(self, *, cycle: int) -> None:
        current = self._current_attempt()
        proposed = self.run.append_current_unknown_adjudication(
            attempt_id=current.id,
            adjudication=_adjudication(cycle),
            expected_version=self.run.version,
            expected_attempt_version=current.version,
        )
        await self._publish_new(proposed, operation=f"adjudicate-{cycle}", cycle=cycle)

    async def _queue_retry(self, *, cycle: int) -> None:
        proposed = self.run.queue_adjudicated_retry(
            retry_intent=_retry_intent(cycle),
            expected_version=self.run.version,
        )
        await self._publish_new(proposed, operation=f"retry-{cycle}", cycle=cycle)

    async def _queue_retry_replay(self, *, cycle: int) -> None:
        replay = self.run.queue_adjudicated_retry(
            retry_intent=_retry_intent(cycle),
            expected_version=self.run.version - 1,
        )
        if replay is not self.run:
            self._violate("domain-replay", f"retry cycle {cycle} advanced state")
        await self._publish_replay(operation=f"retry-{cycle}")

    async def _expect_rejection(
        self,
        command: TraceCommand,
        expected_type: type[Exception],
        action: Callable[[], object],
        *,
        reason: str | None = None,
    ) -> None:
        before = self.run
        try:
            action()
        except expected_type as error:
            if reason is not None and getattr(error, "reason", None) != reason:
                self._violate(
                    "rejection-reason",
                    f"{command.kind.value} expected {reason}, got {error!r}",
                )
        except Exception as error:
            self._violate(
                "rejection-type",
                f"{command.kind.value} expected {expected_type.__name__}, got {error!r}",
            )
        else:
            self._violate(
                "rejection-missing",
                f"{command.kind.value} unexpectedly succeeded",
            )
        if self.run is not before:
            self._violate("rejection-mutation", command.kind.value)
        self.rejected_commands += 1
        await self._assert_durable_current()

    def _current_attempt(self):
        if not self.run.attempts:
            raise TraceNotReplayable("current Attempt is missing")
        return self.run.attempts[-1]

    async def _assert_durable_current(self) -> None:
        durable = await self.state.facts.get(_RUN_KEY)
        if durable != self.run:
            self._violate(
                "durable-model-divergence",
                f"durable={durable!r}, model={self.run!r}",
            )

    async def _assert_invariants(self, *, previous: Run) -> None:
        await self._assert_durable_current()
        run = self.run
        if run.version < previous.version:
            self._violate("run-version-regression", f"{previous.version}->{run.version}")
        if run.current_fence < previous.current_fence:
            self._violate(
                "fence-regression",
                f"{previous.current_fence}->{run.current_fence}",
            )

        old_attempts = {attempt.id: attempt for attempt in previous.attempts}
        for attempt in run.attempts:
            old = old_attempts.get(attempt.id)
            if old is not None and attempt.version < old.version:
                self._violate(
                    "attempt-version-regression",
                    f"{attempt.id}: {old.version}->{attempt.version}",
                )
            if (
                old is not None
                and old.state in _TERMINAL_ATTEMPT_STATES
                and attempt.state is not old.state
            ):
                self._violate(
                    "terminal-regression",
                    f"{attempt.id}: {old.state.value}->{attempt.state.value}",
                )

        attempt_ids = tuple(attempt.id for attempt in run.attempts)
        if len(attempt_ids) != len(set(attempt_ids)):
            self._violate("dual-attempt-identity", repr(attempt_ids))
        expected_numbers = tuple(range(1, len(run.attempts) + 1))
        if tuple(attempt.attempt_no for attempt in run.attempts) != expected_numbers:
            self._violate("attempt-number-regression", repr(expected_numbers))
        if tuple(attempt.fence for attempt in run.attempts) != expected_numbers:
            self._violate("attempt-fence-regression", repr(expected_numbers))
        if run.current_fence != len(run.attempts):
            self._violate(
                "current-fence-mismatch",
                f"fence={run.current_fence}, attempts={len(run.attempts)}",
            )

        active_attempts = tuple(
            attempt for attempt in run.attempts if attempt.state not in _TERMINAL_ATTEMPT_STATES
        )
        if len(active_attempts) > 1:
            self._violate(
                "dual-current-attempt",
                repr(tuple(attempt.id for attempt in active_attempts)),
            )
        if active_attempts and active_attempts[0] is not run.attempts[-1]:
            self._violate("historical-attempt-active", active_attempts[0].id)
        if any(attempt.state not in _TERMINAL_ATTEMPT_STATES for attempt in run.attempts[:-1]):
            self._violate("historical-attempt-active", repr(attempt_ids))

        committed_assignments = tuple(
            assignment
            for assignment in run.assignments
            if assignment.state is AssignmentState.COMMITTED
        )
        if tuple(assignment.id for assignment in committed_assignments) != tuple(
            attempt.assignment_id for attempt in run.attempts
        ):
            self._violate("attempt-assignment-authority", repr(attempt_ids))
        if run.state is RunState.ASSIGNED:
            if run.assignment is None or run.assignment.state not in {
                AssignmentState.OFFERED,
                AssignmentState.CLAIMED,
            }:
                self._violate("current-assignment-missing", run.state.value)
        elif run.state is RunState.RUNNING:
            if (
                run.assignment is None
                or run.assignment.state is not AssignmentState.COMMITTED
                or not run.attempts
                or run.assignment.id != run.attempts[-1].assignment_id
            ):
                self._violate("current-execution-authority", repr(run.assignment))
        elif run.assignment is not None:
            self._violate("dual-current-assignment", run.state.value)

        worker_generations = tuple(attempt.worker.generation for attempt in run.attempts)
        if worker_generations != expected_numbers:
            self._violate("worker-generation-regression", repr(worker_generations))
        for attempt in run.attempts:
            if attempt.state in _TERMINAL_ATTEMPT_STATES:
                stored = self._terminal_states.setdefault(attempt.id, attempt.state)
                if stored is not attempt.state:
                    self._violate(
                        "terminal-regression",
                        f"{attempt.id}: {stored.value}->{attempt.state.value}",
                    )

    @staticmethod
    def _violate(invariant: str, detail: str) -> None:
        raise ModelInvariantViolation(invariant, detail)


def _cycle_time(cycle: int) -> datetime:
    return _BASE_TIME + timedelta(days=cycle - 1)


def _expired_window(cycle: int) -> tuple[datetime, datetime]:
    offered_at = _cycle_time(cycle)
    return offered_at, offered_at + timedelta(minutes=5)


def _execution_window(cycle: int) -> tuple[datetime, datetime, datetime, datetime]:
    offered_at = _cycle_time(cycle) + timedelta(minutes=10)
    return (
        offered_at,
        offered_at + timedelta(hours=1),
        offered_at + timedelta(minutes=1),
        offered_at + timedelta(minutes=2),
    )


def _worker_ref(cycle: int) -> WorkerRef:
    return WorkerRef(worker_id="worker-model", generation=cycle)


def _ready_worker(cycle: int) -> tuple[WorkerGeneration, WorkerAuthority]:
    registered_at = _cycle_time(cycle) - timedelta(hours=1)
    worker = WorkerGeneration.register(
        ref=_worker_ref(cycle),
        host_id="host-model",
        pool_id="pool-model",
        cert_serial=f"cert-{cycle}",
        agent_version="1.0.0",
        capabilities_digest=canonical_digest(
            schema_version="qep.seeded-worker-capabilities.v1",
            payload={"cycle": cycle},
        ),
        registered_at=registered_at,
    )
    authority = WorkerAuthority(current_ref=worker.ref)
    ready = worker.transition(
        WorkerState.READY,
        authority=authority,
        expected_version=worker.version,
        occurred_at=registered_at + timedelta(seconds=1),
    )
    return ready, authority


def _spec_digest():
    return canonical_digest(
        schema_version="qep.seeded-execution-spec.v1",
        payload={"runner": "pytest", "suite": "seeded-model"},
    )


def _assignment_id(cycle: int, *, expired: bool) -> str:
    suffix = "expired" if expired else "active"
    return f"assignment-{cycle}-{suffix}"


def _attempt_id(cycle: int) -> str:
    return f"attempt-{cycle}"


def _event(cycle: int, *, suffix: str = "accepted", conflict: bool = False) -> AttemptEvent:
    identity_suffix = "accepted" if conflict else suffix
    return AttemptEvent(
        event_id=f"event-{cycle}-{identity_suffix}",
        event_seq=1 if identity_suffix == "accepted" else 100 + cycle,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.seeded-attempt-event.v1",
            payload={"cycle": cycle, "suffix": suffix, "conflict": conflict},
        ),
    )


def _attempt_authority(attempt) -> AttemptAuthority:
    return AttemptAuthority(
        current_fence=attempt.fence,
        current_worker=attempt.worker,
    )


def _unknown_observation(cycle: int) -> UnknownObservation:
    return UnknownObservation(
        id=f"unknown-{cycle}",
        reason=UnknownReason.EXECUTION_STOP_UNPROVEN,
        source=UnknownSource.RECONCILER,
        review_basis_digest=canonical_digest(
            schema_version="qep.seeded-unknown-basis.v1",
            payload={"cycle": cycle},
        ),
        recorded_at=_cycle_time(cycle) + timedelta(minutes=30),
    )


def _adjudication(cycle: int) -> UnknownAdjudication:
    observation = _unknown_observation(cycle)
    return UnknownAdjudication(
        id=f"adjudication-{cycle}",
        attempt_id=_attempt_id(cycle),
        unknown_observation_digest=observation.digest,
        decision=UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY,
        actor_id="reviewer-model",
        reason="seeded execution stop proof reviewed",
        occurred_at=_cycle_time(cycle) + timedelta(minutes=31),
        proof_digest=canonical_digest(
            schema_version="qep.seeded-stop-proof.v1",
            payload={"cycle": cycle},
        ),
        risk_approver_id=None,
        risk_acceptance_digest=None,
        evidence_root_digest=None,
    )


def _retry_intent(cycle: int) -> RetryIntent:
    adjudication = _adjudication(cycle)
    return RetryIntent.from_unknown_adjudication(
        id=f"retry-{cycle}",
        run_id=_RUN_KEY.value,
        source_attempt_id=_attempt_id(cycle),
        source_attempt_no=cycle,
        source_fence=cycle,
        adjudication_id=adjudication.id,
        adjudication_digest=adjudication.digest,
        decision=adjudication.decision,
        execution_spec_digest=_spec_digest(),
        created_at=_cycle_time(cycle) + timedelta(minutes=32),
    )
