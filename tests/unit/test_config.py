"""Tests for qarunner.config — Settings defaults and secret validation (SEC-2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qarunner.config import Settings


class TestSettings:
    """Settings should provide sensible defaults."""

    def test_defaults(self):
        s = Settings()
        assert s.tests_root == "./external_tests/"
        assert s.artifacts_root == "./artifacts"
        assert s.db_path == "./artifacts/qarunner.db"
        assert s.allure_bin == "allure"
        assert s.executable == ""
        assert s.default_timeout_seconds == 1800
        assert s.max_concurrency == 4

    def test_override(self, monkeypatch):
        monkeypatch.setenv("QARUNNER_TESTS_ROOT", "/custom/tests")
        monkeypatch.setenv("QARUNNER_MAX_CONCURRENCY", "8")
        s = Settings()
        assert s.tests_root == "/custom/tests"
        assert s.max_concurrency == 8


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
