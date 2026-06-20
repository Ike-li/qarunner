"""Tests for api.deps — create_container factory."""

from __future__ import annotations

from qarunner.api.deps import Container, create_container
from qarunner.config import Settings


def test_create_container_returns_container() -> None:
    c = create_container()
    assert isinstance(c, Container)
    assert c.orchestrator is not None
    assert c.store is not None


def test_create_container_with_custom_settings(monkeypatch) -> None:
    monkeypatch.setenv("QARUNNER_TESTS_ROOT", "/custom/tests")
    monkeypatch.setenv("QARUNNER_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("QARUNNER_DEFAULT_TIMEOUT_SECONDS", "60")
    cfg = Settings()
    c = create_container(cfg)
    assert isinstance(c, Container)
    assert c.orchestrator._tests_root == "/custom/tests"
    assert c.orchestrator._default_timeout == 60


def test_create_container_uses_sys_executable_by_default() -> None:
    import sys

    c = create_container()
    assert c.orchestrator._executable == sys.executable
