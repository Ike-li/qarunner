"""Test doubles for the PostgreSQL role operator tests."""

from __future__ import annotations

from typing import Any

from qarunner.migrations.roles import CURRENT_SEQUENCES, CURRENT_TABLES


class FakeRoleConnection:
    """Record role SQL and expose the catalog branches used by the unit tests."""

    def __init__(
        self,
        *,
        owner: str | None = None,
        has_migrator_role: bool = True,
        tables: set[str] | None = None,
        sequences: set[str] | None = None,
        existing_roles: dict[str, dict[str, bool]] | None = None,
        database: str = 'role_db"quoted',
        session_user: str = "unit_qep_migrator",
    ) -> None:
        self.owner = owner
        self.has_migrator_role = has_migrator_role
        self.tables = set(CURRENT_TABLES) if tables is None else tables
        self.sequences = set(CURRENT_SEQUENCES) if sequences is None else sequences
        self.existing_roles = existing_roles or {}
        self.database = database
        self.session_user = session_user
        self.executed: list[str] = []

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append(query)
        return "OK"

    async def fetchval(self, query: str, *args: object) -> Any:
        if "pg_catalog.pg_namespace" in query:
            return self.owner
        if "current_database()" in query:
            return self.database
        if "session_user" in query:
            return self.session_user
        if "pg_has_role" in query:
            return self.has_migrator_role
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, bool] | None:
        assert "pg_catalog.pg_roles" in query
        return self.existing_roles.get(str(args[0]))

    async def fetch(self, query: str, *args: object) -> list[dict[str, str]]:
        if "information_schema.tables" in query:
            return [{"table_name": table} for table in sorted(self.tables)]
        if "information_schema.sequences" in query:
            return [{"sequence_name": sequence} for sequence in sorted(self.sequences)]
        raise AssertionError(f"unexpected fetch query: {query}")


class FakeOperatorConnection:
    """Track CLI connection lifecycle and transaction ownership."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.closed = False
        self.transaction_count = 0

    class _Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: object) -> None:
            return None

    def transaction(self) -> _Transaction:
        self.transaction_count += 1
        return self._Transaction()

    async def close(self) -> None:
        self.closed = True
