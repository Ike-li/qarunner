"""Tests for qarunner.config — Settings defaults."""

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
