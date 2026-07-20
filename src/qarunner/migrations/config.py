"""Alembic configuration shared by operator and runtime validation paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_SCHEMA_PATTERN = re.compile(r"[a-z_][a-z0-9_]*\Z")
_MAX_POSTGRES_IDENTIFIER_BYTES = 63
_SCRIPT_LOCATION = Path(__file__).resolve().parent


def validate_schema_name(schema: str) -> str:
    """Reject identifiers that cannot be safely used as PostgreSQL Schema names."""
    if (
        _SCHEMA_PATTERN.fullmatch(schema) is None
        or len(schema.encode("ascii")) > _MAX_POSTGRES_IDENTIFIER_BYTES
    ):
        raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
    return schema


def to_async_sqlalchemy_url(database_url: str) -> str:
    """Use SQLAlchemy's asyncpg dialect without adding a second database driver."""
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError("migration database URL must use PostgreSQL")


def build_alembic_config(
    *,
    database_url: str | None = None,
    schema: str | None = None,
) -> Config:
    """Build an in-memory Alembic config; credentials never enter repository files."""
    resolved_url = database_url or os.environ.get("QARUNNER_MIGRATION_DATABASE_URL", "")
    if not resolved_url:
        raise ValueError("QARUNNER_MIGRATION_DATABASE_URL is required")
    resolved_schema = validate_schema_name(
        schema or os.environ.get("QARUNNER_MIGRATION_SCHEMA", "")
    )

    config = Config()
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    # Alembic Config uses ConfigParser interpolation, so literal percent signs must be escaped.
    config.set_main_option(
        "sqlalchemy.url",
        to_async_sqlalchemy_url(resolved_url).replace("%", "%%"),
    )
    config.attributes["qarunner_schema"] = resolved_schema
    return config


def expected_heads() -> tuple[str, ...]:
    """Return the repository's recognized Alembic heads without reading credentials."""
    config = Config()
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    return tuple(ScriptDirectory.from_config(config).get_heads())
