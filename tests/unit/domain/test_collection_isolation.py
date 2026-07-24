"""T-M2-COLLECT-001: collection cannot reach control-plane capability.

Observable contract: the pure accept path stays free of process/DB/Docker
capability, and known control-plane modules are classified as forbidden for
collection jobs. Real sandbox enforcement is M4 Worker scope; this gate freezes
the control-plane side of the isolation contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_accept_path_source_has_no_control_plane_capability() -> None:
    from qarunner.domain.collection_isolation import assert_accept_path_is_isolated

    source = Path("src/qarunner/domain/collection_adapter.py").read_text(encoding="utf-8")
    assert_accept_path_is_isolated(source=source)


def test_forbidden_control_plane_imports_are_classified() -> None:
    from qarunner.domain.collection_isolation import is_forbidden_control_plane_import

    assert is_forbidden_control_plane_import("qarunner.adapters.docker_runner") is True
    assert is_forbidden_control_plane_import("qarunner.adapters.postgres_store") is True
    assert is_forbidden_control_plane_import("qarunner.core.orchestrator") is True
    assert is_forbidden_control_plane_import("docker") is True
    assert is_forbidden_control_plane_import("asyncpg") is True
    assert is_forbidden_control_plane_import("qarunner.domain.manifest") is False
    assert is_forbidden_control_plane_import("qarunner.domain.collection_adapter") is False


def test_assert_accept_path_fails_closed_on_subprocess_token() -> None:
    from qarunner.domain.collection_isolation import assert_accept_path_is_isolated

    with pytest.raises(AssertionError, match="subprocess"):
        assert_accept_path_is_isolated(source="import subprocess\n")
