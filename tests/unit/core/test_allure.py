"""Tests for qarunner.core.allure — build_generate_command / should_generate."""

from qarunner.core.allure import build_generate_command, should_generate


class TestBuildGenerateCommand:
    """build_generate_command produces the correct allure CLI args."""

    def test_basic(self):
        cmd = build_generate_command("allure", "/artifacts/run-001/results")
        assert cmd == [
            "allure",
            "generate",
            "/artifacts/run-001/results/allure-results",
            "--single-file",
            "--clean",
            "-o",
            "/artifacts/run-001/results/allure-report",
        ]

    def test_custom_bin(self):
        cmd = build_generate_command("/usr/local/bin/allure", "/tmp/r")
        assert cmd[0] == "/usr/local/bin/allure"


class TestShouldGenerate:
    """should_generate returns True only when allure-results has files."""

    def test_no_dir(self, tmp_path):
        assert should_generate(str(tmp_path)) is False

    def test_empty_dir(self, tmp_path):
        (tmp_path / "allure-results").mkdir()
        assert should_generate(str(tmp_path)) is False

    def test_has_files(self, tmp_path):
        d = tmp_path / "allure-results"
        d.mkdir()
        (d / "result.json").write_text("{}")
        assert should_generate(str(tmp_path)) is True
