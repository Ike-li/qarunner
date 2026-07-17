"""Tests for qarunner.config — Settings defaults and secret validation (SEC-2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qarunner.config import Settings

_EXPLICIT_SECRET = "test-secret-" + "y" * 60
_EXPLICIT_ADMIN_PASSWORD = "test-admin-password"
_SETTINGS_ENV_KEYS = [
    "QARUNNER_TESTS_ROOT",
    "QARUNNER_ARTIFACTS_ROOT",
    "QARUNNER_DATABASE_BACKEND",
    "QARUNNER_DB_PATH",
    "QARUNNER_DATABASE_URL",
    "QARUNNER_DATABASE_SCHEMA",
    "QARUNNER_ALLURE_BIN",
    "QARUNNER_EXECUTABLE",
    "QARUNNER_DEFAULT_TIMEOUT_SECONDS",
    "QARUNNER_MAX_CONCURRENCY",
    "QARUNNER_FLAKY_MIN_OBSERVATIONS",
    "QARUNNER_FLAKY_FLIP_THRESHOLD",
    "QARUNNER_AI_PROVIDER",
    "QARUNNER_AI_API_KEY",
    "QARUNNER_AI_MODEL",
    "QARUNNER_AI_BASE_URL",
    "QARUNNER_AI_ANALYSIS_MAX_LOG_BYTES",
    "QARUNNER_AI_REQUEST_TIMEOUT_SECONDS",
    "QARUNNER_AI_POST_MAX_CALLS",
    "QARUNNER_AI_POST_WINDOW_SECONDS",
]


class TestSettings:
    """Settings should provide sensible defaults."""

    def test_defaults(self, monkeypatch):
        for key in _SETTINGS_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        s = Settings(secret_key=_EXPLICIT_SECRET, admin_password=_EXPLICIT_ADMIN_PASSWORD)
        assert s.tests_root == "./external_tests/"
        assert s.artifacts_root == "./artifacts"
        assert s.database_backend == "sqlite"
        assert s.db_path == "./artifacts/qarunner.db"
        assert s.database_url == ""
        assert s.database_schema == "public"
        assert s.allure_bin == "allure"
        assert s.executable == ""
        assert s.default_timeout_seconds == 1800
        assert s.max_concurrency == 4
        assert s.flaky_min_observations == 4
        assert s.flaky_flip_threshold == 3
        assert s.ai_provider == "anthropic"
        assert s.ai_api_key == ""
        assert s.ai_model == "claude-opus-4-8"
        assert s.ai_base_url == ""
        assert s.ai_analysis_max_log_bytes == 16384
        assert s.ai_request_timeout_seconds == 60.0
        assert s.ai_post_max_calls == 10
        assert s.ai_post_window_seconds == 60.0

    def test_override(self, monkeypatch):
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", "/custom/tests")
        monkeypatch.setenv("QARUNNER_MAX_CONCURRENCY", "8")
        monkeypatch.setenv("QARUNNER_FLAKY_MIN_OBSERVATIONS", "6")
        monkeypatch.setenv("QARUNNER_FLAKY_FLIP_THRESHOLD", "4")
        monkeypatch.setenv("QARUNNER_AI_PROVIDER", "openai")
        monkeypatch.setenv("QARUNNER_AI_API_KEY", "sk-test")
        monkeypatch.setenv("QARUNNER_AI_MODEL", "gpt-4o")
        monkeypatch.setenv("QARUNNER_AI_BASE_URL", "https://gw.example.com/v1")
        monkeypatch.setenv("QARUNNER_AI_ANALYSIS_MAX_LOG_BYTES", "8192")
        monkeypatch.setenv("QARUNNER_AI_REQUEST_TIMEOUT_SECONDS", "30")
        monkeypatch.setenv("QARUNNER_AI_POST_MAX_CALLS", "3")
        monkeypatch.setenv("QARUNNER_AI_POST_WINDOW_SECONDS", "120")
        s = Settings()
        assert s.tests_root == "/custom/tests"
        assert s.max_concurrency == 8
        assert s.flaky_min_observations == 6
        assert s.flaky_flip_threshold == 4
        assert s.ai_provider == "openai"
        assert s.ai_api_key == "sk-test"
        assert s.ai_model == "gpt-4o"
        assert s.ai_base_url == "https://gw.example.com/v1"
        assert s.ai_analysis_max_log_bytes == 8192
        assert s.ai_request_timeout_seconds == 30.0
        assert s.ai_post_max_calls == 3
        assert s.ai_post_window_seconds == 120.0

    def test_rejects_non_postgresql_database_url(self):
        with pytest.raises(ValidationError, match="PostgreSQL"):
            Settings(
                database_backend="postgres",
                database_url="sqlite:///artifacts/qarunner.db",
            )


@pytest.mark.parametrize(
    "bad",
    [
        "super-secret-dev-key",
        "production-secret-jwt-signing-key-change-me",
        "change-me",
        "",
        "   ",
    ],
)
def test_settings_rejects_placeholder_secret_key(bad: str) -> None:
    """SEC-2: a blank or known-placeholder JWT secret is refused at startup."""
    with pytest.raises(ValidationError):
        Settings(secret_key=bad)


@pytest.mark.parametrize("bad", ["admin123", "admin", "password", "change-me", "", "   "])
def test_settings_rejects_placeholder_admin_password(bad: str) -> None:
    """SEC-2: a blank or known-weak admin password is refused at startup."""
    with pytest.raises(ValidationError):
        Settings(admin_password=bad)


def test_settings_rejects_secret_key_shorter_than_32_chars() -> None:
    """A genuine (non-placeholder) secret that's simply too short for HS256
    must still be refused — length is checked independently of the
    placeholder blocklist."""
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(secret_key="short-but-not-a-placeholder")


def test_settings_secret_key_required(monkeypatch) -> None:
    """SEC-2: a missing QARUNNER_SECRET_KEY refuses startup."""
    monkeypatch.delenv("QARUNNER_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_admin_password_required(monkeypatch) -> None:
    """SEC-2: a missing QARUNNER_ADMIN_PASSWORD refuses startup."""
    monkeypatch.delenv("QARUNNER_ADMIN_PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        Settings()
