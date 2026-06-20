"""Tests for qarunner.errors — verifies exception hierarchy."""

import pytest


class TestRunNotFound:
    def test_is_key_error(self):
        from qarunner.errors import RunNotFound

        with pytest.raises(KeyError):
            raise RunNotFound("no-such-id")

    def test_message(self):
        from qarunner.errors import RunNotFound

        err = RunNotFound("run-42")
        assert "run-42" in str(err)


class TestUnknownRunner:
    def test_is_value_error(self):
        from qarunner.errors import UnknownRunner

        with pytest.raises(ValueError):
            raise UnknownRunner("mystery")

    def test_message(self):
        from qarunner.errors import UnknownRunner

        err = UnknownRunner("nosuch")
        assert "nosuch" in str(err)


class TestUnsafePath:
    def test_is_value_error(self):
        from qarunner.errors import UnsafePath

        with pytest.raises(ValueError):
            raise UnsafePath("../../etc/passwd")

    def test_message(self):
        from qarunner.errors import UnsafePath

        err = UnsafePath("../../etc/passwd")
        assert "etc" in str(err)


class TestRunnerError:
    def test_is_runtime_error(self):
        from qarunner.errors import RunnerError

        with pytest.raises(RuntimeError):
            raise RunnerError("build failed")

    def test_message(self):
        from qarunner.errors import RunnerError

        err = RunnerError("cmd error")
        assert "cmd error" in str(err)
