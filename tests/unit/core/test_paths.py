"""Tests for qarunner.core.paths — safe_subpath."""

import pytest

from qarunner.core.paths import safe_subpath
from qarunner.errors import UnsafePath


class TestSafeSubpath:
    """safe_subpath should confine resolution within root."""

    def test_simple_relative(self, tmp_path):
        result = safe_subpath(str(tmp_path), "subdir")
        assert result == str(tmp_path / "subdir")

    def test_nested_relative(self, tmp_path):
        result = safe_subpath(str(tmp_path), "a/b/c")
        assert result == str(tmp_path / "a" / "b" / "c")

    def test_dot_slash(self, tmp_path):
        result = safe_subpath(str(tmp_path), "./subdir")
        assert result == str(tmp_path / "subdir")

    def test_traversal_raises(self, tmp_path):
        with pytest.raises(UnsafePath):
            safe_subpath(str(tmp_path), "../../etc/passwd")

    def test_dot_dot_within_root_raises(self, tmp_path):
        """../other when root has a sibling still escapes."""
        with pytest.raises(UnsafePath):
            safe_subpath(str(tmp_path), "../outside")

    def test_symlink_escape_raises(self, tmp_path):
        """A symlink pointing outside root must be caught."""
        outside = tmp_path.parent / "outside_secret"
        outside.mkdir()
        link = tmp_path / "sneaky"
        link.symlink_to(outside)
        with pytest.raises(UnsafePath):
            safe_subpath(str(tmp_path), "sneaky")

    def test_root_itself(self, tmp_path):
        result = safe_subpath(str(tmp_path), ".")
        assert result == str(tmp_path)
