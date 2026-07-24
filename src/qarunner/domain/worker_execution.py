"""M4 execution admission: commit-start proof before any sandbox.

Control-plane and Worker agents share this pure gate. Real container creation
happens only after a durable CommitStartProof exists (WORKER_PROTOCOL §5.4 /
T-M4-BOUNDARY-001).
"""

from __future__ import annotations

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
