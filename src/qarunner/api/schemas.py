"""API response schemas — serialise domain models for HTTP."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from qarunner.models import (
    MAX_TIMEOUT_SECONDS,
    ReportRef,
    Run,
    RunStatus,
    TestProfile,
    TestSchedule,
    TestSummary,
    UserRole,
)


class TestProfileResponse(BaseModel):
    """JSON representation of a test profile."""

    id: str
    name: str
    description: str | None = None
    tests_path: str
    runner: str = "pytest"
    selected_files: list[str] = Field(default_factory=list)
    selected_markers: list[str] = Field(default_factory=list)
    extra_args: str = ""
    executor_mode: str = "docker"
    timeout: int | None = None
    created_by: str
    created_at: datetime
    env: dict[str, str] = Field(default_factory=dict)


class TestProfileCreateRequest(BaseModel):
    """Payload to create a new test profile."""

    name: str
    description: str | None = None
    tests_path: str
    runner: str = "pytest"
    selected_files: list[str] = Field(default_factory=list)
    selected_markers: list[str] = Field(default_factory=list)
    extra_args: str = ""
    executor_mode: Literal["subprocess", "docker"] = "docker"
    timeout: int | None = Field(default=None, gt=0, le=MAX_TIMEOUT_SECONDS)
    env: dict[str, str] = Field(default_factory=dict)


class TestProfileUpdateRequest(BaseModel):
    """Payload to update an existing test profile."""

    name: str
    description: str | None = None
    tests_path: str
    runner: str = "pytest"
    selected_files: list[str] = Field(default_factory=list)
    selected_markers: list[str] = Field(default_factory=list)
    extra_args: str = ""
    executor_mode: Literal["subprocess", "docker"] = "docker"
    timeout: int | None = Field(default=None, gt=0, le=MAX_TIMEOUT_SECONDS)
    env: dict[str, str] = Field(default_factory=dict)


class RunResponse(BaseModel):
    """JSON representation of a test run."""

    id: str
    status: RunStatus
    runner: str
    created_by: str
    tests_path: str
    args: list[str] = Field(default_factory=list)
    executor_mode: str
    summary: TestSummary | None = None
    report: ReportRef | None = None
    exit_code: int | None = None
    error: str | None = None
    passed: bool | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stdout: str | None = None
    stderr: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    locked: bool = False


class RunListResponse(BaseModel):
    """Collection wrapper for listing runs."""

    runs: list[RunResponse]


class LoginRequest(BaseModel):
    """Payload to log in."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """Authentication token response."""

    access_token: str
    token_type: str = "bearer"


class UserCreateRequest(BaseModel):
    """Payload to create a new user (Admin-only)."""

    username: str
    password: str
    role: UserRole = UserRole.USER


class UserUpdateRequest(BaseModel):
    """Payload to update a user (Admin-only); at least one field required."""

    password: str | None = None
    role: UserRole | None = None


class UserResponse(BaseModel):
    """JSON representation of a user."""

    username: str
    role: UserRole
    created_at: str


class UserListResponse(BaseModel):
    """Collection wrapper for listing users."""

    users: list[UserResponse]


class LockRunRequest(BaseModel):
    """Payload to lock or unlock a run."""

    locked: bool


def run_to_response(run: Run) -> RunResponse:
    """Convert a domain ``Run`` into an ``RunResponse``.

    Derives the ``passed`` field:
    - ``True``  when COMPLETED with zero failures and zero errors.
    - ``False`` when COMPLETED but has failures or errors.
    - ``None``  for any other status or when summary is absent.
    """
    passed: bool | None = None
    if run.status == RunStatus.COMPLETED and run.summary is not None:
        passed = run.summary.failed == 0 and run.summary.error == 0

    return RunResponse(
        id=run.id,
        status=run.status,
        runner=run.runner,
        created_by=run.created_by,
        tests_path=run.tests_path,
        args=run.args,
        executor_mode=run.executor_mode,
        summary=run.summary,
        report=run.report,
        exit_code=run.exit_code,
        error=run.error,
        passed=passed,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        env=run.env,
        locked=run.locked,
    )


def profile_to_response(profile: TestProfile) -> TestProfileResponse:
    """Convert a domain TestProfile into a TestProfileResponse."""
    return TestProfileResponse(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        tests_path=profile.tests_path,
        runner=profile.runner,
        selected_files=profile.selected_files,
        selected_markers=profile.selected_markers,
        extra_args=profile.extra_args,
        executor_mode=profile.executor_mode,
        timeout=profile.timeout,
        created_by=profile.created_by,
        created_at=profile.created_at,
        env=profile.env,
    )


class TestScheduleResponse(BaseModel):
    """JSON representation of a test schedule."""

    id: str
    name: str
    profile_id: str
    cron_expression: str
    enabled: bool
    timezone: str
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_by: str
    created_at: datetime


class TestScheduleCreateRequest(BaseModel):
    """Payload to create a new test schedule."""

    name: str
    profile_id: str
    cron_expression: str
    enabled: bool = True
    timezone: str = "UTC"


class TestScheduleUpdateRequest(BaseModel):
    """Payload to update an existing test schedule."""

    name: str
    profile_id: str
    cron_expression: str
    enabled: bool
    timezone: str = "UTC"


class TestSchedulePreviewResponse(BaseModel):
    """Response containing future predicted runs."""

    next_runs: list[datetime]


def schedule_to_response(schedule: TestSchedule) -> TestScheduleResponse:
    """Convert a domain TestSchedule into a TestScheduleResponse."""
    return TestScheduleResponse(
        id=schedule.id,
        name=schedule.name,
        profile_id=schedule.profile_id,
        cron_expression=schedule.cron_expression,
        enabled=schedule.enabled,
        timezone=schedule.timezone,
        last_run_at=schedule.last_run_at,
        next_run_at=schedule.next_run_at,
        created_by=schedule.created_by,
        created_at=schedule.created_at,
    )

class LinkTestSuiteRequest(BaseModel):
    """Payload to link a local directory path as a test suite."""

    path: str


class CloneTestSuiteRequest(BaseModel):
    """Payload to clone a git repository as a test suite."""

    url: str
    name: str | None = None
    ref: str | None = None
    credential_ref: str | None = None


class SuiteInfoResponse(BaseModel):
    """A test suite as a filesystem entity left-joined with its metadata.

    Unregistered directories default to ``source="local"`` so manually placed
    suites still surface (R5).
    """

    name: str
    source: str = "local"
    repo_url: str | None = None
    ref: str | None = None


class LinkTestSuiteResponse(BaseModel):
    """Response of linking a local directory path."""

    success: bool
    suite_name: str
    is_accessible: bool
    message: str
