"""Path safety utilities — prevent directory traversal."""

from __future__ import annotations

from pathlib import Path

from qarunner.errors import UnsafePath


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
