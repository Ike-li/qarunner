"""First-attempt Assignment CAS gateway (offer/claim/commit-start/close).

Sibling of `RetryCommitStartGateway` for the non-retry path. Each publish takes the
domain-produced result of the corresponding `Run` method plus the pre-mutation snapshot,
and persists it under one short transaction with optimistic CAS on the Run version,
returning a `ReplayResult` (`replayed=True` when the transition was already durably applied).

M1 single-worker scope (single-ECS same-host MVP; physical second host deferred): worker
identity is a seeded fixture and `offer_token_hash` is a deterministic fixture digest; the
real Worker registration, offer-token issuance/verification, `renew`, generation rotation,
mTLS transport, and the physical second host all defer to M3-protocol/E1.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import Run
from qarunner.domain.run import CommitStartResult


@dataclass(frozen=True, slots=True)
class AssignmentMutationSnapshot:
    """The Run aggregate loaded under `FOR UPDATE`, carrying its optimistic-CAS version."""

    run: Run

    def __post_init__(self) -> None:
        if not isinstance(self.run, Run):
            raise PortContractError(
                resource="assignment_mutation_snapshot", field="run", reason="not_run"
            )

    @property
    def run_id(self) -> str:
        return self.run.id

    @property
    def version(self) -> int:
        return self.run.version


@runtime_checkable
class AssignmentGateway(Protocol):
    """Load-and-persist the Run's first-attempt Assignment transitions.

    The command layer loads the Run via `get_run_for_update`, calls the corresponding
    `Run` domain method (`offer_assignment`/`claim_assignment`/`commit_start`/
    `expire_precommit_assignment`/`release_precommit_assignment`), and hands the result to
    the matching `publish_*`. The adapter enforces `qep_assignments_one_active_per_run`,
    monotonic Run version/fence, and idempotent replay against real PostgreSQL; those
    behaviors are proven in the `ASGN-OFFER`/`ASGN-CLAIM`/`ASGN-COMMIT-START`/`ASGN-CLOSE`
    sub-gates, not by this port's Fake.
    """

    async def get_run_for_update(self, *, run_id: str) -> AssignmentMutationSnapshot: ...

    async def publish_offer(
        self, *, offered: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]: ...

    async def publish_claim(
        self, *, claimed: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]: ...

    async def publish_commit_start(
        self, *, commit: CommitStartResult, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[CommitStartResult]: ...

    async def publish_close(
        self, *, closed: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]: ...
