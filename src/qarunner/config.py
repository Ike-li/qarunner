"""Application settings — loaded from environment / .env."""

from __future__ import annotations

import socket
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

# Known placeholder / weak values that must never reach a running instance.
# Settings refuses to start if QARUNNER_SECRET_KEY / QARUNNER_ADMIN_PASSWORD match
# one of these (SEC-2), so a forgotten .env.example copy fails fast.
_PLACEHOLDER_SECRET_KEYS = frozenset(
    {
        "super-secret-dev-key",
        "production-secret-jwt-signing-key-change-me",
        "change-me",
        "changeme",
        "secret",
    }
)
_PLACEHOLDER_ADMIN_PASSWORDS = frozenset(
    {
        "admin123",
        "admin",
        "password",
        "change-me",
        "changeme",
    }
)


class Settings(BaseSettings):
    """Runtime configuration for qarunner."""

    model_config = {"env_prefix": "QARUNNER_"}

    tests_root: str = "./external_tests/"
    artifacts_root: str = "./artifacts"
    database_backend: Literal["sqlite", "postgres"] = "sqlite"
    db_path: str = "./artifacts/qarunner.db"
    database_url: str = ""
    database_schema: str = "public"
    database_health_timeout_seconds: float = Field(default=2.0, gt=0)
    allure_bin: str = "allure"
    executable: str = ""  # empty → sys.executable at runtime
    default_timeout_seconds: int = 1800
    max_concurrency: int = 4
    # Per-user cap on simultaneously queued/running runs (P2-7). Bounds unbounded
    # run accumulation by one authenticated user; admins are exempt. 0 disables.
    max_inflight_runs_per_user: int = 20
    # Executor image runtime auto-build (DEP-5). When qarunner-executor:latest is
    # missing, DockerRunner builds it from the Dockerfile at runtime — convenient
    # in dev, but a production risk: the build is silent and can drift from the
    # pinned image. Set false in production and pre-build the image so a missing
    # one fails fast instead of being silently (re)built.
    executor_autobuild: bool = True
    playwright_executor_image: str = "qarunner-playwright-executor:latest"
    # os.pathsep-separated allowlist of host/container roots whose explicit env
    # directory values may be mounted read-only into executor containers.
    executor_extra_readonly_roots: str = ""
    # JWT signing secret — required, no default (SEC-2). Missing → ValidationError
    # at startup; a known placeholder is rejected by the validator below.
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    # Secure attribute for the auth cookie (SEC-6). Off by default so local HTTP
    # dev and the test client work; MUST be true in production (HTTPS) so the
    # HttpOnly auth cookie never rides a plaintext connection.
    cookie_secure: bool = False
    # CORS allowed origins (comma-separated). Empty = same-origin only (no
    # Access-Control-Allow-Origin header). Set to e.g. "http://localhost:5173"
    # for local frontend dev, or "https://qa.example.com" for production.
    cors_origins: str = ""
    admin_user: str = "admin"
    # Initial admin password — required, no default (SEC-2).
    admin_password: str
    # Crash recovery (mark_interrupted_runs) assumes a single instance owns the
    # DB: on startup it fails *all* QUEUED/RUNNING runs as interrupted. Under
    # multiple replicas a late-starting worker would wrongly fail runs still
    # executing in its siblings, so disable this on all but one instance (or use
    # an external recovery story) before scaling out. See CONC-2.
    crash_recovery_on_startup: bool = True
    # Graceful-shutdown grace period (DATA-4): how long to wait for in-flight
    # runs to persist their terminal state before the DB connection closes. Runs
    # still executing after this are cancelled (and recovered as FAILED on the
    # next start by crash_recovery_on_startup), so the process can exit promptly.
    shutdown_drain_timeout_seconds: float = 30.0
    worker_node_id: str = Field(default_factory=socket.gethostname)
    # Public-facing base URL of this instance (e.g. https://qa.example.com). Used
    # to build absolute links in Feishu notification cards. Leave empty to skip
    # notifications entirely (dev / no-webhook deployments).
    public_url: str = ""
    # Login brute-force throttle (SEC-5) tunables. Defaults keep the locked-down
    # production behaviour; dev can widen these via env so exploratory curl probes
    # don't trip the 5-strike lockout (which otherwise blocks admin login and the
    # storageState bootstrap). 0 threshold disables lockout entirely.
    login_throttle_threshold: int = 5
    login_throttle_base_seconds: float = 60.0
    login_throttle_max_seconds: float = 900.0
    # Flaky detection (P2-1). Defaults require a continuing oscillation
    # (pass→fail→pass→fail) rather than a one-off regression + fix.
    flaky_min_observations: int = Field(default=4, ge=1)
    flaky_flip_threshold: int = Field(default=3, ge=1)
    # AI failure-diagnosis (optional). Empty ``ai_api_key`` disables the feature
    # (endpoints return ``enabled:false``; the UI still shows the AI tab but
    # explains the feature is unconfigured inside it). The platform never fails
    # to start on a missing key (unlike secret_key/admin_password).
    # ``ai_provider`` selects the SDK (anthropic | openai); ``ai_base_url``
    # allows a self-hosted gateway/proxy; ``ai_analysis_max_log_bytes`` bounds
    # the combined stdout+stderr tail sent to the LLM.
    # ``ai_post_max_calls`` / ``ai_post_window_seconds`` bound POST frequency
    # per authenticated user (0 max_calls disables the limiter).
    ai_provider: str = "anthropic"
    ai_api_key: str = ""
    ai_model: str = "claude-opus-4-8"
    ai_base_url: str = ""
    ai_analysis_max_log_bytes: int = 16384
    ai_request_timeout_seconds: float = 60.0
    ai_post_max_calls: int = Field(default=10, ge=0)
    ai_post_window_seconds: float = Field(default=60.0, gt=0)

    # Trusted reverse-proxy IPs (BUG-8). Comma-separated list of client
    # addresses whose ``X-Forwarded-For`` header the login throttle should
    # trust for real-client identification. Empty (default) disables
    # ``X-Forwarded-For`` parsing entirely — the transport peer address is
    # always used.
    trusted_proxies: str = ""

    @model_validator(mode="after")
    def _require_postgresql_database_url(self) -> Settings:
        if self.database_backend == "postgres" and not self.database_url.startswith(
            ("postgresql://", "postgres://")
        ):
            raise ValueError(
                "QARUNNER_DATABASE_URL must be a PostgreSQL connection URL when "
                "QARUNNER_DATABASE_BACKEND=postgres"
            )
        return self

    @field_validator("secret_key")
    @classmethod
    def _reject_placeholder_secret_key(cls, value: str) -> str:
        if not value.strip() or value in _PLACEHOLDER_SECRET_KEYS:
            raise ValueError(
                "QARUNNER_SECRET_KEY is unset, blank, or a known placeholder — set a "
                "strong random value, e.g. "
                '`python -c "import secrets; print(secrets.token_urlsafe(64))"`'
            )
        if len(value) < 32:
            raise ValueError(
                "QARUNNER_SECRET_KEY must be at least 32 characters for HS256 security"
            )
        return value

    @field_validator("admin_password")
    @classmethod
    def _reject_placeholder_admin_password(cls, value: str) -> str:
        if not value.strip() or value in _PLACEHOLDER_ADMIN_PASSWORDS:
            raise ValueError(
                "QARUNNER_ADMIN_PASSWORD is unset, blank, or a known weak/default "
                "password — set a strong, unique value"
            )
        return value
