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
    CANCELLED = "cancelled"


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

    @classmethod
    def from_profile(cls, profile: TestProfile) -> RunRequest:
        """Build a run request from a saved profile.

        Single source of truth for the profile→run mapping shared by the cron
        scheduler and the manual schedule trigger (P2-6), so adding a profile
        field can't silently diverge between the two paths.
        """
        return cls(
            tests_path=profile.tests_path,
            runner=profile.runner,
            args=[],
            allure=True,
            timeout=profile.timeout,
            executor_mode=profile.executor_mode,  # type: ignore[arg-type]
            selected_files=profile.selected_files,
            selected_markers=profile.selected_markers,
            extra_args=profile.extra_args,
            env=profile.env,
        )

    @classmethod
    def from_run(cls, run: Run) -> RunRequest:
        """Rebuild a run request from a finished run, to re-run it (P2-7).

        ``run.args`` already holds the fully-compiled argv (markers / selected
        files / extra args were folded in at create time), so the selective
        fields stay empty — recompiling the stored args is idempotent.
        """
        return cls(
            tests_path=run.tests_path,
            runner=run.runner,
            args=list(run.args),
            allure=run.allure_enabled,
            timeout=run.timeout,
            executor_mode=run.executor_mode,  # type: ignore[arg-type]
            env=run.env,
        )


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
    # Feishu bot webhook URL for run-completion notifications.
    webhook_url: str | None = None



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


class Credential(BaseModel):
    """A stored git credential's metadata (P0-1).

    The plaintext/encrypted secret is deliberately NOT a field here: this model
    is what the API returns and what gets serialised, so the secret can never
    leak through it. The ciphertext is persisted and fetched separately.
    """

    __test__ = False

    id: str
    name: str
    type: str  # currently only "https_token"
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

    __test__ = False  # prevent pytest collection warning

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


class RegressionDiff(BaseModel):
    """Cross-run baseline diff (stage 2): a head run versus its baseline.

    Buckets are keyed by case identity ``(suite, name)``. ``new_failures`` /
    ``fixed`` / ``still_failing`` / ``new_cases`` carry the *head* case (current
    state + message); ``removed_cases`` carries the *base* case (absent from
    head). A non-fail → non-fail transition lands in no bucket.
    """

    new_failures: list[TestCaseResult] = Field(default_factory=list)
    fixed: list[TestCaseResult] = Field(default_factory=list)
    still_failing: list[TestCaseResult] = Field(default_factory=list)
    new_cases: list[TestCaseResult] = Field(default_factory=list)
    removed_cases: list[TestCaseResult] = Field(default_factory=list)


class TrendPoint(BaseModel):
    """One point in a suite's cross-run pass-rate trend (stage 1)."""

    run_id: str
    created_at: datetime
    pass_rate: float
    total: int
    passed: int
    failed: int


class CaseHistoryPoint(BaseModel):
    """One run's outcome for a single test case, for its cross-run history (stage 3)."""

    created_at: datetime
    status: str


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
    # The profile that triggered this run (None for manual triggers).
    profile_id: str | None = None


