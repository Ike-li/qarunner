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
    FailureDiagnosis,
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

    async def create_if_below_inflight_limit(self, run: Run, limit: int) -> bool:
        """Atomically create a Run if its owner has fewer than ``limit`` in flight."""
        ...

    async def get(self, run_id: str) -> Run:
        """Return the run or raise RunNotFound."""
        ...

    async def list(self, limit: int | None = None) -> list[Run]: ...

    async def cancel_if_inflight(self, run_id: str, finished_at: str) -> bool:
        """Atomically set CANCELLED only if the run is still QUEUED or RUNNING.

        Returns True if the update was applied, False if the run was already
        in a terminal state (no-op) — prevents overwriting a legitimate
        COMPLETED/FAILED/TIMEOUT with CANCELLED.
        """
        ...

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

    async def demote_if_not_last_admin(self, username: str, new_role: str) -> bool:
        """Atomically change role only if more than one admin remains.

        Returns True if the update was applied, False if the user is the
        last admin (no-op) — prevents two concurrent demotion requests from
        both succeeding and leaving zero admins.
        """
        ...

    async def increment_token_version(self, username: str) -> bool:
        """Bump a user's token_version, invalidating all existing JWTs.

        Returns True if the user existed (BUG-5+13).
        """
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

    async def update_schedule_next_run(
        self, schedule_id: str, next_run_at: datetime | None
    ) -> None: ...


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

    async def lock_run(self, run_id: str, locked: bool) -> bool:
        """Toggle user retention lock unless cleanup currently owns the Run."""
        ...

    async def delete_run(self, run_id: str) -> bool:
        """Delete a run's row. Returns True if a row was removed (P1-4)."""
        ...

    async def count_inflight_runs(self, created_by: str) -> int:
        """Count a user's queued+running runs (for the P2-7 per-user cap)."""
        ...

    async def get_old_unlocked_runs(self, retention_days: int) -> list[Run]: ...

    async def claim_run_cleanup(self, run_id: str, retention_days: int) -> bool:
        """Atomically claim an eligible unlocked Run for artifact cleanup."""
        ...

    async def finish_run_cleanup(self, run_id: str, *, cleaned: bool) -> None:
        """Release cleanup claim and clear report metadata iff artifacts were removed."""
        ...

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
        profile_id: str | None = None,
    ) -> list[CaseHistoryPoint]:
        """Return one case's recent outcomes, oldest-first.

        ``created_by`` (a non-admin caller) scopes to that user's own runs;
        ``None`` (admin) spans all owners. ``profile_id`` optionally narrows the
        history to one saved execution profile.
        """
        ...

    async def get_case_histories(
        self,
        tests_path: str,
        cases: list[tuple[str, str]],
        limit: int = 20,
        created_by: str | None = None,
        profile_id: str | None = None,
    ) -> dict[tuple[str, str], list[CaseHistoryPoint]]:
        """Batched get_case_history for several (suite, name) cases at once."""
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

    async def save_ai_diagnosis(
        self,
        run_id: str,
        diagnosis: FailureDiagnosis,
        provider: str,
        model: str,
        created_at: datetime,
    ) -> None:
        """Cache a run's AI failure diagnosis (upsert; one row per run)."""
        ...

    async def get_ai_diagnosis(self, run_id: str) -> FailureDiagnosis | None:
        """Return a run's cached diagnosis, or None if not yet generated."""
        ...

    async def dequeue_next_queued(self) -> str | None:
        """Atomically claim and return the oldest QUEUED run id, marking it RUNNING.

        Returns None if no QUEUED run is available.
        """
        ...
