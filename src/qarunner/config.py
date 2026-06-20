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
