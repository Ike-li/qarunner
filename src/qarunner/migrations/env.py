"""Alembic loader boundary for the package-owned migration environment."""

from alembic import context  # pragma: no cover - executed by Alembic's dynamic loader

from qarunner.migrations.environment import run_environment  # pragma: no cover

run_environment(context)  # pragma: no cover - exercised through environment.py tests
