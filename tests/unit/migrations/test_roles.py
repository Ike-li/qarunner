"""Unit coverage for the PostgreSQL role bootstrap SQL plan."""

from __future__ import annotations

import pytest
from tests.fakes.migration_roles import FakeRoleConnection as FakeConnection

import qarunner.migrations.roles as role_module
import qarunner.migrations.runtime as migration_runtime
from qarunner.migrations.roles import (
    CURRENT_SEQUENCES,
    CURRENT_TABLES,
    DatabaseRoleNames,
    RoleBootstrapError,
    apply_database_role_grants,
    bootstrap_database_roles,
)


def _restricted_role_row(**overrides: bool) -> dict[str, bool]:
    row = {
        "rolcanlogin": True,
        "rolinherit": False,
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolbypassrls": False,
        "has_memberships": False,
    }
    row.update(overrides)
    return row


@pytest.fixture
def roles() -> DatabaseRoleNames:
    return DatabaseRoleNames.from_prefix("unit_qep")


@pytest.mark.asyncio
async def test_bootstrap_creates_restricted_roles_schema_and_explicit_connect_grants(
    roles: DatabaseRoleNames,
) -> None:
    connection = FakeConnection()

    await bootstrap_database_roles(connection, schema="unit_roles", roles=roles)

    assert sum(statement.startswith("CREATE ROLE") for statement in connection.executed) == 5
    assert 'CREATE SCHEMA "unit_roles" AUTHORIZATION "unit_qep_migrator"' in connection.executed
    assert 'GRANT CONNECT ON DATABASE "role_db""quoted" TO "unit_qep_api"' in connection.executed


@pytest.mark.asyncio
async def test_bootstrap_accepts_existing_restricted_roles_and_owned_schema(
    roles: DatabaseRoleNames,
) -> None:
    connection = FakeConnection(
        owner=roles.migrator,
        existing_roles={role: _restricted_role_row() for role in roles.all},
    )

    await bootstrap_database_roles(connection, schema="unit_roles", roles=roles)

    assert not any(statement.startswith("CREATE ROLE") for statement in connection.executed)
    assert not any(statement.startswith("CREATE SCHEMA") for statement in connection.executed)
    assert sum(statement.startswith("GRANT CONNECT") for statement in connection.executed) == 5


@pytest.mark.asyncio
async def test_bootstrap_rejects_public_wrong_owner_and_unsafe_existing_role(
    roles: DatabaseRoleNames,
) -> None:
    with pytest.raises(RoleBootstrapError, match="public Schema"):
        await bootstrap_database_roles(FakeConnection(), schema="public", roles=roles)

    existing = {role: _restricted_role_row() for role in roles.all}
    with pytest.raises(RoleBootstrapError, match="owned by the migrator"):
        await bootstrap_database_roles(
            FakeConnection(owner="someone_else", existing_roles=existing),
            schema="unit_roles",
            roles=roles,
        )

    existing[roles.migrator] = _restricted_role_row(rolcanlogin=False)
    with pytest.raises(RoleBootstrapError, match="not a restricted login role"):
        await bootstrap_database_roles(
            FakeConnection(existing_roles=existing),
            schema="unit_roles",
            roles=roles,
        )

    existing[roles.migrator] = _restricted_role_row(rolcreaterole=True)
    with pytest.raises(RoleBootstrapError, match="not a restricted login role"):
        await bootstrap_database_roles(
            FakeConnection(existing_roles=existing),
            schema="unit_roles",
            roles=roles,
        )

    existing[roles.migrator] = _restricted_role_row(rolinherit=True)
    with pytest.raises(RoleBootstrapError, match="not a restricted login role"):
        await bootstrap_database_roles(
            FakeConnection(existing_roles=existing),
            schema="unit_roles",
            roles=roles,
        )

    existing[roles.migrator] = _restricted_role_row(has_memberships=True)
    with pytest.raises(RoleBootstrapError, match="not a restricted login role"):
        await bootstrap_database_roles(
            FakeConnection(existing_roles=existing),
            schema="unit_roles",
            roles=roles,
        )


@pytest.mark.asyncio
async def test_apply_reconciles_every_table_sequence_and_default_privilege(
    monkeypatch: pytest.MonkeyPatch,
    roles: DatabaseRoleNames,
) -> None:
    validated: list[str] = []

    async def validate(connection: object, *, schema: str) -> None:
        validated.append(schema)

    monkeypatch.setattr(role_module, "_validate_schema_revision", validate)
    connection = FakeConnection(owner=roles.migrator)

    await apply_database_role_grants(connection, schema="unit_roles", roles=roles)

    assert validated == ["unit_roles"]
    assert (
        'GRANT INSERT, SELECT ON TABLE "unit_roles"."qep_audit_events" '
        'TO "unit_qep_coordinator"' in connection.executed
    )
    assert (
        'GRANT SELECT ON TABLE "unit_roles"."alembic_version" '
        'TO "unit_qep_recovery_read"' in connection.executed
    )
    assert (
        'GRANT USAGE ON SEQUENCE "unit_roles"."run_test_cases_id_seq" TO "unit_qep_api"'
        in connection.executed
    )
    assert any("REVOKE ALL ON TABLES FROM PUBLIC" in sql for sql in connection.executed)
    assert any("REVOKE ALL ON SEQUENCES FROM PUBLIC" in sql for sql in connection.executed)
    assert any(
        'REVOKE ALL ON TABLES FROM "unit_qep_api", "unit_qep_coordinator", '
        '"unit_qep_worker_protocol", "unit_qep_recovery_read"' in sql
        for sql in connection.executed
    )
    assert any(
        'REVOKE ALL ON SEQUENCES FROM "unit_qep_api", "unit_qep_coordinator", '
        '"unit_qep_worker_protocol", "unit_qep_recovery_read"' in sql
        for sql in connection.executed
    )


@pytest.mark.asyncio
async def test_apply_runs_runtime_schema_revision_validation_before_grants(
    monkeypatch: pytest.MonkeyPatch,
    roles: DatabaseRoleNames,
) -> None:
    validated: list[tuple[object, str]] = []

    async def validate(connection: object, *, schema: str) -> None:
        validated.append((connection, schema))

    monkeypatch.setattr(migration_runtime, "validate_schema_revision", validate)
    connection = FakeConnection(owner=roles.migrator)

    await apply_database_role_grants(connection, schema="unit_roles", roles=roles)

    assert validated == [(connection, "unit_roles")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("connection", "schema", "message"),
    [
        (FakeConnection(), "public", "public Schema"),
        (FakeConnection(owner="wrong"), "unit_roles", "owned by the migrator"),
        (
            FakeConnection(owner="unit_qep_migrator", has_migrator_role=False),
            "unit_roles",
            "must run as the migrator",
        ),
        (
            FakeConnection(owner="unit_qep_migrator", session_user="admin"),
            "unit_roles",
            "must run as the migrator",
        ),
        (
            FakeConnection(
                owner="unit_qep_migrator",
                tables={*CURRENT_TABLES, "future_table"},
            ),
            "unit_roles",
            "unreviewed tables",
        ),
        (
            FakeConnection(
                owner="unit_qep_migrator",
                tables=set(CURRENT_TABLES) - {"qep_versioned_fact_commands"},
            ),
            "unit_roles",
            "missing tables",
        ),
        (
            FakeConnection(
                owner="unit_qep_migrator",
                sequences={*CURRENT_SEQUENCES, "future_sequence"},
            ),
            "unit_roles",
            "sequence catalog",
        ),
    ],
    ids=[
        "public",
        "wrong-owner",
        "wrong-caller-role",
        "wrong-caller-session",
        "unknown-table",
        "missing-table",
        "sequence",
    ],
)
async def test_apply_fails_closed_before_granting(
    monkeypatch: pytest.MonkeyPatch,
    roles: DatabaseRoleNames,
    connection: FakeConnection,
    schema: str,
    message: str,
) -> None:
    async def validate(connection: object, *, schema: str) -> None:
        return None

    monkeypatch.setattr(role_module, "_validate_schema_revision", validate)
    with pytest.raises(RoleBootstrapError, match=message):
        await apply_database_role_grants(connection, schema=schema, roles=roles)
    assert not any(statement.startswith("GRANT ") for statement in connection.executed)
