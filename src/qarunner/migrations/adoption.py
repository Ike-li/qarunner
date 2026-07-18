"""Atomic adoption of the frozen legacy PostgreSQL catalog."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from qarunner.migrations.config import to_async_sqlalchemy_url, validate_schema_name
from qarunner.migrations.legacy import (
    LEGACY_CHECKSUMS,
    LEGACY_COLUMNS,
    LEGACY_CONSTRAINTS,
    LEGACY_INDEXES,
    LEGACY_TABLES,
)


class LegacyAdoptionError(RuntimeError):
    """Raised when a database is not the exact frozen legacy v10 catalog."""


async def adopt_legacy_schema(
    config: Config,
    *,
    database_url: str,
    schema: str,
) -> None:
    """Validate and stamp an exact legacy Schema in one database transaction."""
    safe_schema = validate_schema_name(schema)
    engine = create_async_engine(
        to_async_sqlalchemy_url(database_url),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": safe_schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended('qarunner-legacy-adoption:' || current_schema(), 0)"
                    ")"
                )
            )
            already_adopted = await connection.run_sync(_validate_exact_legacy_schema)
            if not already_adopted:
                await connection.run_sync(lambda sync: _stamp_baseline(config, sync))
    finally:
        await engine.dispose()


def _validate_exact_legacy_schema(connection: Connection) -> bool:
    tables = {
        row.table_name
        for row in connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            )
        )
    }
    already_adopted = "alembic_version" in tables
    if already_adopted:
        revisions = tuple(
            row.version_num
            for row in connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            )
        )
        if revisions != ("legacy_baseline",):
            raise LegacyAdoptionError("legacy Schema has incompatible Alembic revision state")
        tables.remove("alembic_version")

    if tables != LEGACY_TABLES:
        raise LegacyAdoptionError(
            f"legacy catalog table mismatch: expected={sorted(LEGACY_TABLES)}, "
            f"actual={sorted(tables)}"
        )

    columns: dict[str, dict[str, tuple[str, bool, str | None]]] = {}
    for row in connection.execute(
        text(
            """
            SELECT table_name, column_name, udt_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name <> 'alembic_version'
            ORDER BY table_name, ordinal_position
            """
        )
    ):
        columns.setdefault(row.table_name, {})[row.column_name] = (
            row.udt_name,
            row.is_nullable == "YES",
            row.column_default,
        )
    # Column order is intentionally ignored: v9/v10 databases that added
    # ``checksum`` with ALTER TABLE have a different physical ordinal while
    # preserving the same observable column contract.
    actual_columns = columns
    expected_columns = {
        table: {
            column: (udt_name, nullable, default)
            for column, udt_name, nullable, default in entries
        }
        for table, entries in LEGACY_COLUMNS.items()
    }
    if actual_columns != expected_columns:
        raise LegacyAdoptionError("legacy catalog column mismatch")

    constraints = tuple(
        (row.table_name, row.constraint_type, row.definition)
        for row in connection.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       con.contype::text AS constraint_type,
                       pg_get_constraintdef(con.oid, true) AS definition
                FROM pg_constraint AS con
                JOIN pg_class AS relation ON relation.oid = con.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname <> 'alembic_version'
                ORDER BY relation.relname, con.contype, definition
                """
            )
        )
    )
    if constraints != LEGACY_CONSTRAINTS:
        raise LegacyAdoptionError("legacy catalog constraint mismatch")

    indexes = tuple(
        (
            row.table_name,
            row.index_name,
            row.is_unique,
            row.is_primary,
            row.access_method,
            tuple(row.indexed_columns),
            row.key_column_count,
            row.predicate,
        )
        for row in connection.execute(
            text(
                """
                SELECT table_relation.relname AS table_name,
                       index_relation.relname AS index_name,
                       index.indisunique AS is_unique,
                       index.indisprimary AS is_primary,
                       access_method.amname AS access_method,
                       ARRAY(
                           SELECT pg_get_indexdef(index.indexrelid, position, true)
                           FROM generate_series(1, index.indnatts) AS position
                           ORDER BY position
                       ) AS indexed_columns,
                       index.indnkeyatts AS key_column_count,
                       pg_get_expr(index.indpred, index.indrelid, true) AS predicate
                FROM pg_index AS index
                JOIN pg_class AS table_relation ON table_relation.oid = index.indrelid
                JOIN pg_class AS index_relation ON index_relation.oid = index.indexrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = table_relation.relnamespace
                JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
                WHERE namespace.nspname = current_schema()
                  AND table_relation.relname <> 'alembic_version'
                ORDER BY table_relation.relname, index_relation.relname
                """
            )
        )
    )
    if indexes != LEGACY_INDEXES:
        raise LegacyAdoptionError("legacy catalog index mismatch")

    ledger = list(
        connection.execute(
            text("SELECT version, checksum FROM schema_migrations ORDER BY version")
        )
    )
    expected_versions = list(range(1, 11))
    actual_versions = [row.version for row in ledger]
    if actual_versions != expected_versions:
        raise LegacyAdoptionError(
            "legacy migration ledger must contain exactly contiguous versions 1 through 10"
        )
    for row in ledger:
        if row.checksum != LEGACY_CHECKSUMS[row.version]:
            raise LegacyAdoptionError(f"legacy migration {row.version} checksum mismatch")
    return already_adopted


def _stamp_baseline(config: Config, connection: Connection) -> None:
    config.attributes["connection"] = connection
    try:
        command.stamp(config, "legacy_baseline")
    finally:
        config.attributes.pop("connection", None)
