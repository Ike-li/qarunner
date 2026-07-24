"""Path safety utilities — prevent directory traversal."""

from __future__ import annotations

from pathlib import Path

from qarunner.errors import UnsafePath

# Directory entries never copied into a workspace jail/tarball (shared by the
# legacy orchestrator's Workspace Jail and DockerRunner's source tarball
# build). node_modules is deliberately NOT here: Playwright suites need it
# present so the executor doesn't have to reinstall dependencies (§5.7).
JAIL_IGNORE_NAMES = frozenset({".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__"})


def safe_subpath(root: str, relative: str) -> str:
    """Resolve *relative* under *root* and ensure it doesn't escape.

    Returns the resolved absolute path as a string.
    Raises ``UnsafePath`` if the result escapes *root*.
    """
    root_path = Path(root).resolve()
    resolved = (root_path / relative).resolve()
    if not resolved.is_relative_to(root_path):
        raise UnsafePath(f"{relative!r} resolves outside root {root!r}")
    return str(resolved)
