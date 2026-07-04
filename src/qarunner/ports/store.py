"""Persistence ports.

``RunStore`` is the minimal run-lifecycle surface the orchestrator depends on.
The API/container needs more (users, profiles, schedules, maintenance), so those
are split into focused ports and composed into ``Store`` — the single port the
``Container`` is typed against, keeping it decoupled from the concrete adapter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from qarunner.models import (
    CaseHistoryPoint,
    Credential,
    Run,
    TestCaseResult,
    TestProfile,
    TestSchedule,
    TestSuite,
)


@runtime_checkable
class RunStore(Protocol):
    """Minimal run persistence used by the orchestrator."""

    async def save(self, run: Run) -> None: ...

    async def get(self, run_id: str) -> Run:
        """Return the run or raise RunNotFound."""
        ...

    async def list(self) -> list[Run]: ...

    async def save_cases(
        self,
        run_id: str,
        tests_path: str,
        created_at: datetime,
        cases: list[TestCaseResult],
    ) -> None:
        """Persist a finished run's per-case results (cross-run analysis source)."""
        ...

    async def dequeue_next_queued(self) -> str | None:
        """Atomically claim and return the oldest QUEUED run id, marking it RUNNING.

        Returns None if no QUEUED run is available.  This is the single-
        consumer entry-point for the scheduler poller — never call it from
        business logic.
        """
        ...


@runtime_checkable
class UserStore(Protocol):
    """Persistence for user accounts."""

    async def get_user(self, username: str) -> dict | None: ...

    async def create_user(self, username: str, password_hash: str, role: str) -> None: ...

    async def list_users(self) -> list[dict]: ...

    async def delete_user(self, username: str) -> bool:
        """Delete a user. Returns True if a row was removed (P1-3)."""
        ...

    async def update_password(self, username: str, password_hash: str) -> bool:
        """Set a user's password hash. Returns True if the user existed (P1-3)."""
        ...

    async def update_role(self, username: str, role: str) -> bool:
        """Set a user's role. Returns True if the user existed (P1-3)."""
        ...


@runtime_checkable
class ProfileStore(Protocol):
    """Persistence for saved test profiles."""

    async def save_profile(self, profile: TestProfile) -> None: ...

    async def get_profile(self, profile_id: str) -> TestProfile | None: ...

    async def list_profiles(self, tests_path: str | None = None) -> list[TestProfile]: ...

    async def delete_profile(self, profile_id: str) -> bool: ...


@runtime_checkable
class ScheduleStore(Protocol):
    """Persistence for cron schedules (incl. the CONC-2 leader claim)."""

    async def save_schedule(self, schedule: TestSchedule) -> None: ...

    async def get_schedule(self, schedule_id: str) -> TestSchedule | None: ...

    async def list_schedules(self, profile_id: str | None = None) -> list[TestSchedule]: ...

    async def delete_schedule(self, schedule_id: str) -> bool: ...

    async def claim_schedule_run(self, schedule_id: str, fire_time: datetime) -> bool: ...


@runtime_checkable
class SuiteStore(Protocol):
    """Persistence for registered test suites (local symlink / git clone)."""

    async def save_suite(self, suite: TestSuite) -> None: ...

    async def get_suite(self, name: str) -> TestSuite | None: ...

    async def list_suites(self) -> list[TestSuite]: ...

    async def delete_suite(self, name: str) -> bool: ...


@runtime_checkable
class CredentialStore(Protocol):
    """Persistence for git credentials (P0-1). Secret ciphertext is stored and
    read back only via the dedicated accessor — never on the metadata model."""

    async def save_credential(self, credential: Credential, encrypted_secret: str) -> None: ...

    async def get_credential(self, credential_id: str) -> Credential | None: ...

    async def get_credential_secret(self, credential_id: str) -> str | None: ...

    async def list_credentials(self) -> list[Credential]: ...

    async def delete_credential(self, credential_id: str) -> bool: ...


@runtime_checkable
class Store(
    RunStore,
    UserStore,
    ProfileStore,
    ScheduleStore,
    SuiteStore,
    CredentialStore,
    Protocol,
):
    """Full persistence surface used by the API container.

    Combines the focused stores and adds lifecycle and run-maintenance methods.
    """

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def lock_run(self, run_id: str, locked: bool) -> None: ...

    async def delete_run(self, run_id: str) -> bool:
        """Delete a run's row. Returns True if a row was removed (P1-4)."""
        ...

    async def count_inflight_runs(self, created_by: str) -> int:
        """Count a user's queued+running runs (for the P2-7 per-user cap)."""
        ...

    async def get_old_unlocked_runs(self, retention_days: int) -> list[Run]: ...

    async def mark_interrupted_runs(self, worker_node_id: str | None = None) -> int: ...

    async def get_cases_for_run(self, run_id: str) -> list[TestCaseResult]:
        """Return a run's persisted per-case results (empty if none)."""
        ...

    async def get_case_history(
        self,
        tests_path: str,
        suite: str,
        name: str,
        limit: int = 20,
        created_by: str | None = None,
    ) -> list[CaseHistoryPoint]:
        """Return one case's recent outcomes, oldest-first.

        ``created_by`` (a non-admin caller) scopes to that user's own runs;
        ``None`` (admin) spans all owners.
        """
        ...

    async def count_flaky_tests(
        self,
        days: int = 30,
        created_by: str | None = None,
        *,
        min_observations: int = 4,
        flip_threshold: int = 3,
    ) -> int:
        """Count unique test cases matching the configured flaky policy."""
        ...

    async def dequeue_next_queued(self) -> str | None:
        """Atomically claim and return the oldest QUEUED run id, marking it RUNNING.

        Returns None if no QUEUED run is available.
        """
        ...
