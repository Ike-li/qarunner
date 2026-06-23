"""Application settings — loaded from environment / .env."""

from __future__ import annotations

from pydantic import field_validator
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
    db_path: str = "./artifacts/qarunner.db"
    allure_bin: str = "allure"
    executable: str = ""  # empty → sys.executable at runtime
    default_timeout_seconds: int = 1800
    max_concurrency: int = 4
    # Executor image runtime auto-build (DEP-5). When qarunner-executor:latest is
    # missing, DockerRunner builds it from the Dockerfile at runtime — convenient
    # in dev, but a production risk: the build is silent and can drift from the
    # pinned image. Set false in production and pre-build the image so a missing
    # one fails fast instead of being silently (re)built.
    executor_autobuild: bool = True
    # JWT signing secret — required, no default (SEC-2). Missing → ValidationError
    # at startup; a known placeholder is rejected by the validator below.
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    # Secure attribute for the auth cookie (SEC-6). Off by default so local HTTP
    # dev and the test client work; MUST be true in production (HTTPS) so the
    # HttpOnly auth cookie never rides a plaintext connection.
    cookie_secure: bool = False
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

    @field_validator("secret_key")
    @classmethod
    def _reject_placeholder_secret_key(cls, value: str) -> str:
        if not value.strip() or value in _PLACEHOLDER_SECRET_KEYS:
            raise ValueError(
                "QARUNNER_SECRET_KEY is unset, blank, or a known placeholder — set a "
                'strong random value, e.g. '
                '`python -c "import secrets; print(secrets.token_urlsafe(64))"`'
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
