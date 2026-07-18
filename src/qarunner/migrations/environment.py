"""Testable implementation of the online Alembic environment contract."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from qarunner.migrations.config import validate_schema_name

target_metadata = None


def _run_migrations(context: Any, connection: object) -> None:
    schema = validate_schema_name(context.config.attributes["qarunner_schema"])
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version",
        version_table_schema=schema,
        compare_type=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online_migrations(context: Any) -> None:
    schema = validate_schema_name(context.config.attributes["qarunner_schema"])
    engine = create_async_engine(
        context.config.get_main_option("sqlalchemy.url"),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(lambda sync: _run_migrations(context, sync))
    finally:
        await engine.dispose()


def run_environment(context: Any) -> None:
    """Run Alembic with either a caller-owned or disposable online connection."""
    if context.is_offline_mode():
        raise RuntimeError("qarunner migrations require an online PostgreSQL connection")
    if connection := context.config.attributes.get("connection"):
        _run_migrations(context, connection)
    else:
        asyncio.run(_run_online_migrations(context))
