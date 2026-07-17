"""Tests for api.deps — create_container factory."""

from __future__ import annotations

from qarunner.adapters.anthropic_analyzer import AnthropicFailureAnalyzer
from qarunner.adapters.openai_analyzer import OpenAiFailureAnalyzer
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.adapters.sqlite_store import SqliteStore
from qarunner.api.deps import Container, _build_ai_analyzer, create_container
from qarunner.config import Settings


def test_create_container_returns_container() -> None:
    c = create_container()
    assert isinstance(c, Container)
    assert c.orchestrator is not None
    assert isinstance(c.store, PostgresStore)
    assert c.store._database_url == c.settings.database_url


def test_create_container_with_custom_settings(monkeypatch) -> None:
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", "/custom/tests")
    monkeypatch.setenv("QARUNNER_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("QARUNNER_DEFAULT_TIMEOUT_SECONDS", "60")
    cfg = Settings()
    c = create_container(cfg)
    assert isinstance(c, Container)
    assert c.orchestrator._tests_root == "/custom/tests"
    assert c.orchestrator._default_timeout == 60


def test_create_container_keeps_sqlite_when_postgres_is_not_selected() -> None:
    cfg = Settings(database_backend="sqlite", db_path=":memory:")

    c = create_container(cfg)

    assert isinstance(c.store, SqliteStore)


def test_create_container_uses_sys_executable_by_default() -> None:
    import sys

    c = create_container()
    assert c.orchestrator._executable == sys.executable


def test_create_container_ai_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("QARUNNER_AI_API_KEY", raising=False)
    c = create_container()
    assert c.ai_analyzer is None


def test_create_container_wires_ai_rate_limiter() -> None:
    from qarunner.core.rate_limit import SlidingWindowRateLimiter

    c = create_container()
    assert isinstance(c.ai_rate_limiter, SlidingWindowRateLimiter)
    assert c.ai_rate_limiter.max_calls == c.settings.ai_post_max_calls
    assert c.ai_rate_limiter.window_seconds == c.settings.ai_post_window_seconds


def test_build_ai_analyzer_none_without_key(monkeypatch) -> None:
    monkeypatch.delenv("QARUNNER_AI_API_KEY", raising=False)
    monkeypatch.setenv("QARUNNER_AI_PROVIDER", "anthropic")
    assert _build_ai_analyzer(Settings()) is None


def test_build_ai_analyzer_anthropic(monkeypatch) -> None:
    monkeypatch.setenv("QARUNNER_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("QARUNNER_AI_API_KEY", "sk-test")
    assert isinstance(_build_ai_analyzer(Settings()), AnthropicFailureAnalyzer)


def test_build_ai_analyzer_openai(monkeypatch) -> None:
    monkeypatch.setenv("QARUNNER_AI_PROVIDER", "openai")
    monkeypatch.setenv("QARUNNER_AI_API_KEY", "sk-test")
    assert isinstance(_build_ai_analyzer(Settings()), OpenAiFailureAnalyzer)


def test_build_ai_analyzer_unknown_provider_is_none(monkeypatch) -> None:
    monkeypatch.setenv("QARUNNER_AI_PROVIDER", "gemini")
    monkeypatch.setenv("QARUNNER_AI_API_KEY", "sk-test")
    assert _build_ai_analyzer(Settings()) is None
