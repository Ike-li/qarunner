"""Core data models for qarunner — all Pydantic frozen models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class RunStatus(enum.StrEnum):
    """Lifecycle states for a test run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class UserRole(enum.StrEnum):
    """Roles for users."""

    ADMIN = "admin"
    USER = "user"


class User(BaseModel):
    """User domain model."""

    username: str
    role: UserRole
    created_at: datetime



# Upper bound for a run's timeout (seconds). Bounds untrusted input so a single
# run can neither expire instantly (<=0) nor hold a worker slot indefinitely (P2-5).
MAX_TIMEOUT_SECONDS = 86_400  # 24h


class RunRequest(BaseModel):
    """Incoming API request to trigger a test run."""

    tests_path: str
    runner: str = "pytest"
    args: list[str] = Field(default_factory=list)
    allure: bool = True
    timeout: int | None = Field(default=None, gt=0, le=MAX_TIMEOUT_SECONDS)
    # Default to the isolated docker executor (SEC): untrusted test code must not
    # run in the platform process by default. subprocess stays opt-in (admins, or
    # allow_subprocess_for_non_admins). Literal rejects unknown modes (P2-6).
    executor_mode: Literal["subprocess", "docker"] = "docker"
    # Selective run parameters
    selected_files: list[str] = Field(default_factory=list)
    selected_markers: list[str] = Field(default_factory=list)
    extra_args: str = ""
    env: dict[str, str] = Field(default_factory=dict)



class TestProfile(BaseModel):
    """Execution profile template."""

    __test__ = False

    id: str
    name: str
    description: str | None = None
    tests_path: str
    runner: str = "pytest"
    selected_files: list[str] = Field(default_factory=list)
    selected_markers: list[str] = Field(default_factory=list)
    extra_args: str = ""
    executor_mode: str = "subprocess"
    timeout: int | None = None
    created_by: str
    created_at: datetime
    env: dict[str, str] = Field(default_factory=dict)



class TestSuite(BaseModel):
    """A registered external test suite living under tests_root.

    ``source`` is "local" (symlinked host path, dev-only) or "git" (cloned repo).
    git suites carry repo_url/ref/credential_ref for updates; ``created_by`` is
    the owner used for object-level authz.
    """

    __test__ = False

    name: str
    source: str = "local"
    repo_url: str | None = None
    ref: str | None = None
    credential_ref: str | None = None
    created_by: str
    created_at: datetime


class TestSchedule(BaseModel):
    """Configuration for automated test runs on cron schedule."""

    __test__ = False

    id: str
    name: str
    profile_id: str
    cron_expression: str
    enabled: bool = True
    timezone: str = "UTC"
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_by: str
    created_at: datetime


class ProcessResult(BaseModel):
    """Result of a subprocess execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


class TestCaseResult(BaseModel):
    """Outcome of a single test case."""

    suite: str
    name: str
    status: str  # "passed" | "failed" | "skipped" | "error"
    duration_ms: int
    message: str | None = None


class TestSummary(BaseModel):
    """Aggregate counts from a test run."""

    __test__ = False  # prevent pytest collection warning

    total: int
    passed: int
    failed: int
    skipped: int
    error: int
    duration_ms: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total


class CollectResult(BaseModel):
    """Result of collecting test outcomes from junit XML."""

    summary: TestSummary
    cases: list[TestCaseResult]


class ReportRef(BaseModel):
    """Reference to allure report artifacts."""

    allure_results_dir: str
    allure_report_file: str | None = None
    html_generated: bool = False


class Run(BaseModel):
    """A single test run record."""

    model_config = {"frozen": True}

    id: str
    status: RunStatus
    runner: str
    created_by: str
    tests_path: str
    args: list[str] = Field(default_factory=list)
    allure_enabled: bool = True
    timeout: int | None = None
    executor_mode: str = "subprocess"
    summary: TestSummary | None = None
    report: ReportRef | None = None
    exit_code: int | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    env: dict[str, str] = Field(default_factory=dict)
    locked: bool = False
    worker_node_id: str | None = None


