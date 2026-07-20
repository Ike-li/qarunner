"""Explicit operator CLI for PostgreSQL DDL changes."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

import asyncpg
from alembic import command

from qarunner.migrations.adoption import adopt_legacy_schema
from qarunner.migrations.config import build_alembic_config
from qarunner.migrations.roles import (
    DatabaseRoleNames,
    apply_database_role_grants,
    bootstrap_database_roles,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m qarunner.migrations")
    subcommands = parser.add_subparsers(dest="command", required=True)

    upgrade = subcommands.add_parser("upgrade", help="upgrade the configured Schema")
    upgrade.add_argument("revision", nargs="?", default="head")
    downgrade = subcommands.add_parser(
        "downgrade",
        help="downgrade a disposable Schema (explicitly opt in)",
    )
    downgrade.add_argument("revision", nargs="?", default="base")
    subcommands.add_parser(
        "adopt-legacy",
        help="validate and stamp an exact legacy v10 Schema",
    )
    subcommands.add_parser(
        "bootstrap-roles",
        help="create restricted development/test roles and a migrator-owned Schema",
    )
    subcommands.add_parser(
        "apply-role-grants",
        help="apply the explicit role allowlist to an Alembic-head Schema",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one approved migration operation and return a process exit code."""
    args = _parser().parse_args(argv)
    if args.command == "bootstrap-roles":
        asyncio.run(_bootstrap_roles())
        return 0
    if args.command == "apply-role-grants":
        asyncio.run(_apply_role_grants())
        return 0

    config = build_alembic_config()
    if args.command == "upgrade":
        command.upgrade(config, args.revision)
        return 0
    if args.command == "downgrade":
        if os.environ.get("QARUNNER_MIGRATION_ALLOW_DOWNGRADE") != "true":
            raise RuntimeError("downgrade requires QARUNNER_MIGRATION_ALLOW_DOWNGRADE=true")
        command.downgrade(config, args.revision)
        return 0
    asyncio.run(
        adopt_legacy_schema(
            config,
            database_url=os.environ["QARUNNER_MIGRATION_DATABASE_URL"],
            schema=os.environ["QARUNNER_MIGRATION_SCHEMA"],
        )
    )
    return 0


async def _bootstrap_roles() -> None:
    database_url = os.environ["QARUNNER_ROLE_DATABASE_URL"]
    schema = os.environ["QARUNNER_ROLE_SCHEMA"]
    prefix = os.environ.get("QARUNNER_ROLE_PREFIX", "qep")
    roles = DatabaseRoleNames.from_prefix(prefix)
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            await bootstrap_database_roles(connection, schema=schema, roles=roles)
    finally:
        await connection.close()


async def _apply_role_grants() -> None:
    database_url = os.environ["QARUNNER_MIGRATION_DATABASE_URL"]
    schema = os.environ["QARUNNER_MIGRATION_SCHEMA"]
    prefix = os.environ.get("QARUNNER_ROLE_PREFIX", "qep")
    roles = DatabaseRoleNames.from_prefix(prefix)
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            await apply_database_role_grants(connection, schema=schema, roles=roles)
    finally:
        await connection.close()
