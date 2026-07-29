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

from qarunner.domain.digest import Digest, canonical_digest
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


@dataclass(frozen=True, slots=True)
class BrowserSandboxProfile:
    """Hardened Chromium/Playwright sandbox (T-M5-BROWSER-001 / DES §4.6).

    Extends the M4 SEC-3 baseline with browser-specific requirements: container-
    local bounded ``/dev/shm``, mandatory non-root user, and explicit bans on
    ``privileged``, ``SYS_ADMIN``, and host IPC — these must never be used as
    "make Chromium work" bypasses.
    """

    network_mode: str
    read_only_root: bool
    cap_drop: tuple[str, ...]
    no_new_privileges: bool
    pids_limit: int
    mem_limit: str
    nano_cpus: int
    shm_size: str
    require_non_root_user: bool
    forbid_privileged: bool
    forbid_sys_admin: bool
    forbid_host_ipc: bool

    def __post_init__(self) -> None:
        entity = "browser_sandbox_profile"
        if not isinstance(self.network_mode, str) or not self.network_mode.strip():
            raise DomainValidationError(entity_type=entity, field="network_mode", reason="invalid")
        for field in (
            "read_only_root",
            "no_new_privileges",
            "require_non_root_user",
            "forbid_privileged",
            "forbid_sys_admin",
            "forbid_host_ipc",
        ):
            if not isinstance(getattr(self, field), bool):
                raise DomainValidationError(entity_type=entity, field=field, reason="not_bool")
        if (
            not isinstance(self.cap_drop, tuple)
            or not self.cap_drop
            or any(not isinstance(cap, str) or not cap.strip() for cap in self.cap_drop)
        ):
            raise DomainValidationError(entity_type=entity, field="cap_drop", reason="invalid")
        if (
            isinstance(self.pids_limit, bool)
            or not isinstance(self.pids_limit, int)
            or self.pids_limit < 1
        ):
            raise DomainValidationError(entity_type=entity, field="pids_limit", reason="invalid")
        if not isinstance(self.mem_limit, str) or not self.mem_limit.strip():
            raise DomainValidationError(entity_type=entity, field="mem_limit", reason="invalid")
        if (
            isinstance(self.nano_cpus, bool)
            or not isinstance(self.nano_cpus, int)
            or self.nano_cpus < 1
        ):
            raise DomainValidationError(entity_type=entity, field="nano_cpus", reason="invalid")
        if not isinstance(self.shm_size, str) or not self.shm_size.strip():
            raise DomainValidationError(entity_type=entity, field="shm_size", reason="invalid")

    @classmethod
    def m5_browser_default(cls) -> BrowserSandboxProfile:
        return cls(
            network_mode="none",
            read_only_root=True,
            cap_drop=("ALL",),
            no_new_privileges=True,
            pids_limit=512,
            mem_limit="2g",
            nano_cpus=2_000_000_000,
            shm_size="1g",
            require_non_root_user=True,
            forbid_privileged=True,
            forbid_sys_admin=True,
            forbid_host_ipc=True,
        )

    @property
    def profile_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.browser-sandbox-profile.v1",
            payload={
                "network_mode": self.network_mode,
                "read_only_root": self.read_only_root,
                "cap_drop": list(self.cap_drop),
                "no_new_privileges": self.no_new_privileges,
                "pids_limit": self.pids_limit,
                "mem_limit": self.mem_limit,
                "nano_cpus": self.nano_cpus,
                "shm_size": self.shm_size,
                "require_non_root_user": self.require_non_root_user,
                "forbid_privileged": self.forbid_privileged,
                "forbid_sys_admin": self.forbid_sys_admin,
                "forbid_host_ipc": self.forbid_host_ipc,
            },
        )


def browser_container_create_kwargs(
    *,
    profile: BrowserSandboxProfile,
    user: str,
) -> dict[str, object]:
    """Docker create-kwargs fragment for a browser sandbox under *profile*."""
    if not isinstance(profile, BrowserSandboxProfile):
        raise DomainValidationError(
            entity_type="browser_sandbox_profile",
            field="profile",
            reason="not_browser_sandbox_profile",
        )
    if not isinstance(user, str) or not user.strip():
        raise DomainValidationError(
            entity_type="browser_sandbox_profile", field="user", reason="invalid"
        )
    kwargs: dict[str, object] = {
        "user": user.strip(),
        "network_mode": profile.network_mode,
        "cap_drop": list(profile.cap_drop),
        "security_opt": ["no-new-privileges"] if profile.no_new_privileges else [],
        "pids_limit": profile.pids_limit,
        "mem_limit": profile.mem_limit,
        "nano_cpus": profile.nano_cpus,
        "read_only": profile.read_only_root,
        "tmpfs": {"/tmp": ""},
        "shm_size": profile.shm_size,
        "privileged": False,
    }
    validate_browser_container_create_kwargs(kwargs, profile=profile)
    return kwargs


def validate_browser_container_create_kwargs(
    kwargs: dict[str, object],
    *,
    profile: BrowserSandboxProfile | None = None,
) -> None:
    """Fail closed if create kwargs violate T-M5-BROWSER-001 / DES §4.6."""
    entity = "browser_container_create"
    if not isinstance(kwargs, dict):
        raise DomainValidationError(entity_type=entity, field="kwargs", reason="not_dict")
    policy = profile if profile is not None else BrowserSandboxProfile.m5_browser_default()
    if not isinstance(policy, BrowserSandboxProfile):
        raise DomainValidationError(
            entity_type=entity, field="profile", reason="not_browser_sandbox_profile"
        )

    if policy.forbid_privileged and kwargs.get("privileged") is True:
        raise DomainValidationError(entity_type=entity, field="privileged", reason="forbidden")

    cap_add = kwargs.get("cap_add") or ()
    if isinstance(cap_add, str):
        cap_add = (cap_add,)
    if policy.forbid_sys_admin and any(
        str(cap).upper().replace(" ", "_") in {"SYS_ADMIN", "CAP_SYS_ADMIN"} for cap in cap_add
    ):
        raise DomainValidationError(
            entity_type=entity, field="cap_add", reason="sys_admin_forbidden"
        )

    ipc_mode = kwargs.get("ipc_mode")
    if policy.forbid_host_ipc and isinstance(ipc_mode, str) and ipc_mode.lower() == "host":
        raise DomainValidationError(
            entity_type=entity, field="ipc_mode", reason="host_ipc_forbidden"
        )

    if kwargs.get("network_mode") != policy.network_mode:
        raise DomainValidationError(entity_type=entity, field="network_mode", reason="mismatch")

    cap_drop = kwargs.get("cap_drop") or ()
    if isinstance(cap_drop, str):
        cap_drop = (cap_drop,)
    required_drops = {cap.upper() for cap in policy.cap_drop}
    actual_drops = {str(cap).upper() for cap in cap_drop}
    if not required_drops.issubset(actual_drops):
        raise DomainValidationError(entity_type=entity, field="cap_drop", reason="incomplete")

    if policy.no_new_privileges:
        security_opt = kwargs.get("security_opt") or ()
        if isinstance(security_opt, str):
            security_opt = (security_opt,)
        if "no-new-privileges" not in {str(opt) for opt in security_opt}:
            raise DomainValidationError(
                entity_type=entity, field="security_opt", reason="no_new_privileges_missing"
            )

    if policy.read_only_root and kwargs.get("read_only") is not True:
        raise DomainValidationError(entity_type=entity, field="read_only", reason="required")

    if kwargs.get("pids_limit") != policy.pids_limit:
        raise DomainValidationError(entity_type=entity, field="pids_limit", reason="mismatch")
    if kwargs.get("mem_limit") != policy.mem_limit:
        raise DomainValidationError(entity_type=entity, field="mem_limit", reason="mismatch")
    if kwargs.get("nano_cpus") != policy.nano_cpus:
        raise DomainValidationError(entity_type=entity, field="nano_cpus", reason="mismatch")

    if not kwargs.get("shm_size"):
        raise DomainValidationError(entity_type=entity, field="shm_size", reason="required")
    if kwargs.get("shm_size") != policy.shm_size:
        raise DomainValidationError(entity_type=entity, field="shm_size", reason="mismatch")

    if policy.require_non_root_user:
        user = kwargs.get("user")
        if not isinstance(user, str) or not user.strip():
            raise DomainValidationError(entity_type=entity, field="user", reason="required")
        user_norm = user.strip().lower()
        if user_norm in {"0", "0:0", "root", "root:root"} or user_norm.startswith("0:"):
            raise DomainValidationError(entity_type=entity, field="user", reason="root_forbidden")


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
