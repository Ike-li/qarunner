"""Application settings — loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings


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
    secret_key: str = "super-secret-dev-key"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    admin_user: str = "admin"
    admin_password: str = "admin123"
    # Crash recovery (mark_interrupted_runs) assumes a single instance owns the
    # DB: on startup it fails *all* QUEUED/RUNNING runs as interrupted. Under
    # multiple replicas a late-starting worker would wrongly fail runs still
    # executing in its siblings, so disable this on all but one instance (or use
    # an external recovery story) before scaling out. See CONC-2.
    crash_recovery_on_startup: bool = True
