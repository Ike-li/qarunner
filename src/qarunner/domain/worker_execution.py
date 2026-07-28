"""M4 execution admission: commit-start proof before any sandbox.

Control-plane and Worker agents share this pure gate. Real container creation
happens only after a durable CommitStartProof exists (WORKER_PROTOCOL §5.4 /
T-M4-BOUNDARY-001).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest
from qarunner.domain.errors import DomainValidationError, ExecutionAdmissionError
from qarunner.domain.worker import WorkerRef


@dataclass(frozen=True, slots=True)
class CommitStartProof:
    """Durable identity returned by commit-start before a sandbox may exist."""

    run_id: str
    assignment_id: str
    attempt_id: str
    fence: int
    start_commit_key: str
    worker: WorkerRef
    spec_digest: Digest
    committed_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "run_id",
            "assignment_id",
            "attempt_id",
            "start_commit_key",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(
                    entity_type="commit_start_proof", field=field, reason="invalid"
                )
        if not isinstance(self.worker, WorkerRef):
            raise DomainValidationError(
                entity_type="commit_start_proof", field="worker", reason="not_worker_ref"
            )
        if not isinstance(self.spec_digest, Digest):
            raise DomainValidationError(
                entity_type="commit_start_proof", field="spec_digest", reason="not_digest"
            )
        if isinstance(self.fence, bool) or not isinstance(self.fence, int) or self.fence < 1:
            raise DomainValidationError(
                entity_type="commit_start_proof", field="fence", reason="invalid"
            )
        if not isinstance(self.committed_at, datetime):
            raise DomainValidationError(
                entity_type="commit_start_proof", field="committed_at", reason="not_datetime"
            )
        if self.committed_at.tzinfo is None or self.committed_at.utcoffset() != timedelta(0):
            raise DomainValidationError(
                entity_type="commit_start_proof", field="committed_at", reason="not_utc"
            )


@dataclass(frozen=True, slots=True)
class ExecutionSandboxProfile:
    """Hardened defaults for M4 pytest vertical slice (network=none)."""

    network_mode: str
    read_only_root: bool
    cap_drop: tuple[str, ...]
    no_new_privileges: bool
    pids_limit: int
    mem_limit: str
    nano_cpus: int

    @classmethod
    def m4_pytest_default(cls) -> ExecutionSandboxProfile:
        return cls(
            network_mode="none",
            read_only_root=True,
            cap_drop=("ALL",),
            no_new_privileges=True,
            pids_limit=512,
            mem_limit="2g",
            nano_cpus=2_000_000_000,
        )


def require_execution_admission(*, proof: CommitStartProof) -> CommitStartProof:
    """Admit sandbox creation only with a durable commit-start proof."""
    if not isinstance(proof, CommitStartProof):
        raise ExecutionAdmissionError(reason="commit_start_required")
    if proof.fence < 1:
        raise ExecutionAdmissionError(reason="invalid_fence")
    return proof


def sandbox_labels_for_proof(proof: CommitStartProof) -> dict[str, str]:
    """Stable container labels binding sandbox resources to Attempt/fence."""
    admitted = require_execution_admission(proof=proof)
    return {
        "qarunner.run_id": admitted.run_id,
        "qarunner.assignment_id": admitted.assignment_id,
        "qarunner.attempt_id": admitted.attempt_id,
        "qarunner.fence": str(admitted.fence),
        "qarunner.worker_id": admitted.worker.worker_id,
        "qarunner.worker_generation": str(admitted.worker.generation),
        "qarunner.start_commit_key": admitted.start_commit_key,
    }


def workspace_subpath_for_proof(proof: CommitStartProof) -> str:
    """Relative workspace path segment unique to this Attempt (T-M4-ISOLATE-001).

    Binds run/assignment/attempt/fence so a retried Attempt (same attempt_id,
    higher fence) resolves to a distinct path and can never collide with —
    and so can never read — a prior Attempt's workspace.
    """
    admitted = require_execution_admission(proof=proof)
    return f"{admitted.run_id}/{admitted.assignment_id}/{admitted.attempt_id}/{admitted.fence}"


# ── T-M4-RESTART-001: residual sandbox reconcile after Agent/daemon restart ──


class ResidualSandboxDisposition(enum.StrEnum):
    """What the control plane should do with one residual / expected Attempt."""

    KEEP = "keep"
    ORPHAN_REMOVE = "orphan_remove"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResidualSandboxObservation:
    """One labeled residual container observed on the Worker host after restart.

    Fields mirror :func:`sandbox_labels_for_proof` so a Docker list-by-label
    result can be mapped 1:1 into this type without inventing identity.
    """

    container_id: str
    run_id: str
    assignment_id: str
    attempt_id: str
    fence: int
    worker_id: str
    worker_generation: int
    start_commit_key: str
    running: bool

    def __post_init__(self) -> None:
        entity = "residual_sandbox_observation"
        for field in (
            "container_id",
            "run_id",
            "assignment_id",
            "attempt_id",
            "worker_id",
            "start_commit_key",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(entity_type=entity, field=field, reason="invalid")
        if isinstance(self.fence, bool) or not isinstance(self.fence, int) or self.fence < 1:
            raise DomainValidationError(entity_type=entity, field="fence", reason="invalid")
        if (
            isinstance(self.worker_generation, bool)
            or not isinstance(self.worker_generation, int)
            or self.worker_generation < 1
        ):
            raise DomainValidationError(
                entity_type=entity, field="worker_generation", reason="invalid"
            )
        if not isinstance(self.running, bool):
            raise DomainValidationError(entity_type=entity, field="running", reason="not_bool")

    @property
    def identity_key(self) -> tuple[str, str, str, int]:
        return (self.run_id, self.assignment_id, self.attempt_id, self.fence)


@dataclass(frozen=True, slots=True)
class ExpectedLiveAttempt:
    """Control-plane view of an Attempt that may still own a sandbox.

    ``has_terminal_facts`` is True once Evidence is finalized or an unknown
    observation is already recorded — residual absence then needs no action.
    """

    run_id: str
    assignment_id: str
    attempt_id: str
    fence: int
    start_commit_key: str
    has_terminal_facts: bool

    def __post_init__(self) -> None:
        entity = "expected_live_attempt"
        for field in ("run_id", "assignment_id", "attempt_id", "start_commit_key"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(entity_type=entity, field=field, reason="invalid")
        if isinstance(self.fence, bool) or not isinstance(self.fence, int) or self.fence < 1:
            raise DomainValidationError(entity_type=entity, field="fence", reason="invalid")
        if not isinstance(self.has_terminal_facts, bool):
            raise DomainValidationError(
                entity_type=entity, field="has_terminal_facts", reason="not_bool"
            )

    @property
    def identity_key(self) -> tuple[str, str, str, int]:
        return (self.run_id, self.assignment_id, self.attempt_id, self.fence)


@dataclass(frozen=True, slots=True)
class ResidualSandboxAction:
    """One disposition produced by :func:`reconcile_residual_sandboxes`."""

    disposition: ResidualSandboxDisposition
    run_id: str
    assignment_id: str
    attempt_id: str
    fence: int
    container_id: str | None
    requires_unknown_observation: bool


@dataclass(frozen=True, slots=True)
class ResidualSandboxReconcilePlan:
    """Full plan for one post-restart residual sweep.

    ``auto_start_count`` and ``inferred_success_count`` are structural red-line
    counters for T-M4-RESTART-001: a correct implementation always leaves both
    at zero (never invent success from silence; never start a second sandbox
    for the same Attempt/fence).
    """

    actions: tuple[ResidualSandboxAction, ...]
    auto_start_count: int
    inferred_success_count: int

    def __post_init__(self) -> None:
        if self.auto_start_count != 0 or self.inferred_success_count != 0:
            raise DomainValidationError(
                entity_type="residual_sandbox_reconcile_plan",
                field="red_line",
                reason="auto_start_or_inferred_success_forbidden",
            )


def reconcile_residual_sandboxes(
    *,
    expected_live: Sequence[ExpectedLiveAttempt],
    residuals: Sequence[ResidualSandboxObservation],
) -> ResidualSandboxReconcilePlan:
    """Decide keep / orphan_remove / unknown for residual sandboxes after restart.

    Matching is by ``(run_id, assignment_id, attempt_id, fence)`` — the same
    identity :func:`sandbox_labels_for_proof` stamps on every M4 container. A
    residual whose fence no longer matches the control-plane live Attempt
    (e.g. a retry bumped the fence) is an orphan of the prior incarnation.
    """
    entity = "residual_sandbox_reconcile"
    if not isinstance(expected_live, (tuple, list)) or any(
        not isinstance(item, ExpectedLiveAttempt) for item in expected_live
    ):
        raise DomainValidationError(entity_type=entity, field="expected_live", reason="invalid")
    if not isinstance(residuals, (tuple, list)) or any(
        not isinstance(item, ResidualSandboxObservation) for item in residuals
    ):
        raise DomainValidationError(entity_type=entity, field="residuals", reason="invalid")

    expected_by_key: dict[tuple[str, str, str, int], ExpectedLiveAttempt] = {}
    for item in expected_live:
        if item.identity_key in expected_by_key:
            raise DomainValidationError(
                entity_type=entity, field="expected_live", reason="duplicate_identity"
            )
        expected_by_key[item.identity_key] = item

    residual_keys: set[tuple[str, str, str, int]] = set()
    actions: list[ResidualSandboxAction] = []
    for residual in residuals:
        residual_keys.add(residual.identity_key)
        expected = expected_by_key.get(residual.identity_key)
        if expected is None:
            actions.append(
                ResidualSandboxAction(
                    disposition=ResidualSandboxDisposition.ORPHAN_REMOVE,
                    run_id=residual.run_id,
                    assignment_id=residual.assignment_id,
                    attempt_id=residual.attempt_id,
                    fence=residual.fence,
                    container_id=residual.container_id,
                    requires_unknown_observation=False,
                )
            )
        else:
            actions.append(
                ResidualSandboxAction(
                    disposition=ResidualSandboxDisposition.KEEP,
                    run_id=residual.run_id,
                    assignment_id=residual.assignment_id,
                    attempt_id=residual.attempt_id,
                    fence=residual.fence,
                    container_id=residual.container_id,
                    requires_unknown_observation=False,
                )
            )

    for key, expected in expected_by_key.items():
        if key in residual_keys:
            continue
        if expected.has_terminal_facts:
            continue
        actions.append(
            ResidualSandboxAction(
                disposition=ResidualSandboxDisposition.UNKNOWN,
                run_id=expected.run_id,
                assignment_id=expected.assignment_id,
                attempt_id=expected.attempt_id,
                fence=expected.fence,
                container_id=None,
                requires_unknown_observation=True,
            )
        )

    actions_tuple = tuple(
        sorted(
            actions,
            key=lambda a: (a.run_id, a.assignment_id, a.attempt_id, a.fence, a.disposition.value),
        )
    )
    return ResidualSandboxReconcilePlan(
        actions=actions_tuple,
        auto_start_count=0,
        inferred_success_count=0,
    )
