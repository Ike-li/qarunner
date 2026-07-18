"""Test-only invocation of the real migration operator command."""

from __future__ import annotations

import os
import subprocess
import sys


def run_migration_operator(
    database_url: str,
    schema: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["QARUNNER_MIGRATION_DATABASE_URL"] = database_url
    env["QARUNNER_MIGRATION_SCHEMA"] = schema
    return subprocess.run(
        [sys.executable, "-m", "qarunner.migrations", *args],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
