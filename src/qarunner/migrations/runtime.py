"""Read-only production startup validation for the Alembic revision graph."""

from __future__ import annotations

import enum
from pathlib import Path

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util import CommandError

from qarunner.migrations.config import expected_heads, validate_schema_name


class SchemaRevisionReason(enum.StrEnum):
    MISSING = "missing"
    BEHIND = "behind"
    BRANCHED = "branched"
    AHEAD_OR_UNRECOGNIZED = "ahead_or_unrecognized"
    CODE_HAS_MULTIPLE_HEADS = "code_has_multiple_heads"


class SchemaRevisionError(RuntimeError):
    """Raised before runtime writes when the configured Schema is not exactly at head."""

    def __init__(
        self,
        *,
        schema: str,
        reason: SchemaRevisionReason,
        current: tuple[str, ...],
        expected: tuple[str, ...],
    ) -> None:
        self.schema = schema
        self.reason = reason
        self.current = current
        self.expected = expected
        super().__init__(
            f"schema revision is {reason.value}: schema={schema}, "
            f"current={current or ('<none>',)}, expected={expected or ('<none>',)}"
        )


async def validate_schema_revision(connection: asyncpg.Connection, *, schema: str) -> None:
    """Require the target Schema to have exactly the repository's recognized Alembic head."""
    safe_schema = validate_schema_name(schema)
    expected = expected_heads()
    if len(expected) != 1:
        raise SchemaRevisionError(
            schema=safe_schema,
            reason=SchemaRevisionReason.CODE_HAS_MULTIPLE_HEADS,
            current=(),
            expected=expected,
        )

    version_table_exists = await connection.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = $1
              AND relation.relname = 'alembic_version'
              AND relation.relkind IN ('r', 'p')
        )
        """,
        safe_schema,
    )
    if not version_table_exists:
        raise SchemaRevisionError(
            schema=safe_schema,
            reason=SchemaRevisionReason.MISSING,
            current=(),
            expected=expected,
        )

    rows = await connection.fetch(
        f'SELECT version_num FROM "{safe_schema}".alembic_version ORDER BY version_num'
    )
    current = tuple(row["version_num"] for row in rows)
    if not current:
        raise SchemaRevisionError(
            schema=safe_schema,
            reason=SchemaRevisionReason.MISSING,
            current=current,
            expected=expected,
        )
    if len(current) != 1:
        raise SchemaRevisionError(
            schema=safe_schema,
            reason=SchemaRevisionReason.BRANCHED,
            current=current,
            expected=expected,
        )
    if current == expected:
        return

    script = _script_directory()
    try:
        current_revision = script.get_revision(current[0])
    except (CommandError, ResolutionError):
        current_revision = None
    if current_revision is None:
        reason = SchemaRevisionReason.AHEAD_OR_UNRECOGNIZED
    else:
        reason = SchemaRevisionReason.BEHIND
    raise SchemaRevisionError(
        schema=safe_schema,
        reason=reason,
        current=current,
        expected=expected,
    )


def _script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent),
    )
    return ScriptDirectory.from_config(config)
