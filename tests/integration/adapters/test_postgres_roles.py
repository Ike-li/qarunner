"""T-M1-ROLE-001 real PostgreSQL least-privilege contracts."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from tests.fakes.migration_roles import FakeOperatorConnection
from tests.integration.migration_operator import run_migration_operator

import qarunner.migrations.cli as migration_cli
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.migrations.roles import (
    CURRENT_TABLES,
    DatabaseRoleNames,
    RoleBootstrapError,
    apply_database_role_grants,
    bootstrap_database_roles,
    database_role_matrix,
    validate_role_prefix,
)
from qarunner.migrations.runtime import SchemaRevisionError, SchemaRevisionReason


@dataclass(frozen=True)
class _RoleCatalog:
    admin_database_url: str
    schema: str
    roles: DatabaseRoleNames

    async def connect_as(self, role: str) -> asyncpg.Connection:
        return await asyncpg.connect(
            self.admin_database_url,
            user=role,
            server_settings={"search_path": self.schema},
        )


def _database_url_for_role(database_url: str, role: str) -> str:
    parsed = urlsplit(database_url)
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit(
        (
            parsed.scheme,
            f"{quote(role, safe='')}@{host}{port}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


async def _drop_role_catalog(catalog: _RoleCatalog) -> None:
    connection = await asyncpg.connect(catalog.admin_database_url)
    try:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{catalog.schema}" CASCADE')
        for role in reversed(catalog.roles.all):
            if await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = $1)",
                role,
            ):
                await connection.execute(f'DROP OWNED BY "{role}" CASCADE')
                await connection.execute(f'DROP ROLE "{role}"')
    finally:
        await connection.close()


@pytest.fixture
async def role_catalog() -> AsyncIterator[_RoleCatalog]:
    admin_database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    token = uuid4().hex[:12]
    schema = f"test_roles_{token}"
    roles = DatabaseRoleNames.from_prefix(f"test_qep_{token}")
    catalog = _RoleCatalog(admin_database_url, schema, roles)

    admin = await asyncpg.connect(admin_database_url)
    try:
        await bootstrap_database_roles(admin, schema=schema, roles=roles)
    finally:
        await admin.close()

    migration = run_migration_operator(
        _database_url_for_role(admin_database_url, roles.migrator),
        schema,
        "upgrade",
        "head",
    )
    if migration.returncode != 0:
        await _drop_role_catalog(catalog)
    assert migration.returncode == 0, migration.stderr

    migrator = await catalog.connect_as(roles.migrator)
    try:
        await apply_database_role_grants(migrator, schema=schema, roles=roles)
    except BaseException:
        await migrator.close()
        await _drop_role_catalog(catalog)
        raise
    await migrator.close()

    try:
        yield catalog
    finally:
        await _drop_role_catalog(catalog)


def test_database_role_matrix_is_explicit_and_deny_by_default() -> None:
    roles = DatabaseRoleNames.from_prefix("test_qep_contract")
    matrix = database_role_matrix(roles)

    assert roles.all == (
        "test_qep_contract_migrator",
        "test_qep_contract_api",
        "test_qep_contract_coordinator",
        "test_qep_contract_worker_protocol",
        "test_qep_contract_recovery_read",
    )
    assert len(CURRENT_TABLES) == 55
    assert matrix[roles.worker_protocol] == {}
    assert matrix[roles.recovery_read]["qep_audit_events"] == ("SELECT",)
    assert "users" not in matrix[roles.recovery_read]
    assert matrix[roles.api]["users"] == ("DELETE", "INSERT", "SELECT", "UPDATE")
    assert matrix[roles.api]["alembic_version"] == ("SELECT",)
    assert "qep_audit_events" not in matrix[roles.api]
    assert set(matrix[roles.coordinator]) == {
        "alembic_version",
        "qep_assignments",
        "qep_attempts",
        "qep_audit_events",
        "qep_batch_cancellation_intents",
        "qep_batch_cancellation_scope_items",
        "qep_batch_finalization_bases",
        "qep_batch_finalization_readiness_facts",
        "qep_batch_item_resolutions",
        "qep_batch_preexecution_closure_bases",
        "qep_batch_rejections",
        "qep_batch_success_policies",
        "qep_batches",
        "qep_case_manifests",
        "qep_evidence_index",
        "qep_manifest_items",
        "qep_materialized_scope_handoffs",
        "qep_outbox_events",
        "qep_retry_intents",
        "qep_run_finalization_bases",
        "qep_run_item_resolutions",
        "qep_runs",
        "qep_shard_plans",
        "qep_suite_revisions",
        "qep_suites",
        "qep_versioned_fact_commands",
        "qep_versioned_fact_snapshots",
    }
    assert matrix[roles.coordinator]["qep_audit_events"] == ("INSERT", "SELECT")
    assert matrix[roles.coordinator]["qep_batch_rejections"] == ("SELECT",)
    assert "DELETE" not in {
        privilege for privileges in matrix[roles.coordinator].values() for privilege in privileges
    }


@pytest.mark.parametrize(
    "prefix",
    ["QEP", "1qep", "qep-role", "qep;drop_role", "q" * 52],
)
def test_role_prefix_rejects_unsafe_or_overlong_identifiers(prefix: str) -> None:
    with pytest.raises(ValueError, match="safe lowercase"):
        validate_role_prefix(prefix)


def test_role_operator_main_dispatches_without_starting_an_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[object] = []

    def run(coroutine: object) -> None:
        dispatched.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(migration_cli.asyncio, "run", run)
    monkeypatch.setenv("QARUNNER_ROLE_DATABASE_URL", "postgresql://admin/db")
    monkeypatch.setenv("QARUNNER_ROLE_SCHEMA", "qep_test")
    monkeypatch.setenv("QARUNNER_ROLE_PREFIX", "qep_test")
    monkeypatch.setenv("QARUNNER_MIGRATION_DATABASE_URL", "postgresql://migrator/db")
    monkeypatch.setenv("QARUNNER_MIGRATION_SCHEMA", "qep_test")

    assert migration_cli.main(["bootstrap-roles"]) == 0
    assert migration_cli.main(["apply-role-grants"]) == 0
    assert len(dispatched) == 2


@pytest.mark.asyncio
async def test_role_operator_helpers_dispatch_and_close_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, DatabaseRoleNames]] = []
    connections: list[FakeOperatorConnection] = []

    async def connect(database_url: str) -> FakeOperatorConnection:
        connection = FakeOperatorConnection(database_url)
        connections.append(connection)
        return connection

    async def bootstrap(
        connection: FakeOperatorConnection,
        *,
        schema: str,
        roles: DatabaseRoleNames,
    ) -> None:
        calls.append(("bootstrap", connection.database_url, schema, roles))

    async def apply(
        connection: FakeOperatorConnection,
        *,
        schema: str,
        roles: DatabaseRoleNames,
    ) -> None:
        calls.append(("apply", connection.database_url, schema, roles))

    monkeypatch.setattr(migration_cli.asyncpg, "connect", connect)
    monkeypatch.setattr(migration_cli, "bootstrap_database_roles", bootstrap)
    monkeypatch.setattr(migration_cli, "apply_database_role_grants", apply)
    monkeypatch.setenv("QARUNNER_ROLE_DATABASE_URL", "postgresql://admin/db")
    monkeypatch.setenv("QARUNNER_ROLE_SCHEMA", "qep_test")
    monkeypatch.setenv("QARUNNER_ROLE_PREFIX", "qep_test")
    monkeypatch.setenv("QARUNNER_MIGRATION_DATABASE_URL", "postgresql://migrator/db")
    monkeypatch.setenv("QARUNNER_MIGRATION_SCHEMA", "qep_test")

    await migration_cli._bootstrap_roles()
    await migration_cli._apply_role_grants()

    expected_roles = DatabaseRoleNames.from_prefix("qep_test")
    assert calls == [
        ("bootstrap", "postgresql://admin/db", "qep_test", expected_roles),
        ("apply", "postgresql://migrator/db", "qep_test", expected_roles),
    ]
    assert len(connections) == 2
    assert all(connection.closed for connection in connections)
    assert [connection.transaction_count for connection in connections] == [1, 1]


@pytest.mark.asyncio
@pytest.mark.docker
async def test_role_bootstrap_requires_a_non_public_migrator_owned_schema() -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    roles = DatabaseRoleNames.from_prefix(f"test_qep_invalid_{uuid4().hex[:10]}")
    connection = await asyncpg.connect(database_url)
    try:
        with pytest.raises(RoleBootstrapError, match="public Schema"):
            await bootstrap_database_roles(connection, schema="public", roles=roles)
        with pytest.raises(RoleBootstrapError, match="public Schema"):
            await apply_database_role_grants(connection, schema="public", roles=roles)

        schema = f"test_roles_wrong_owner_{uuid4().hex[:10]}"
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        try:
            with pytest.raises(RoleBootstrapError, match="owned by the migrator"):
                await bootstrap_database_roles(connection, schema=schema, roles=roles)
            with pytest.raises(RoleBootstrapError, match="owned by the migrator"):
                await apply_database_role_grants(connection, schema=schema, roles=roles)
        finally:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        for role in reversed(roles.all):
            if await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = $1)",
                role,
            ):
                await connection.execute(f'DROP OWNED BY "{role}" CASCADE')
                await connection.execute(f'DROP ROLE "{role}"')
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_role_bootstrap_rejects_an_existing_privileged_or_nonlogin_role() -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    token = uuid4().hex[:10]
    roles = DatabaseRoleNames.from_prefix(f"test_qep_unsafe_{token}")
    catalog = _RoleCatalog(database_url, f"test_roles_unsafe_{token}", roles)
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f'CREATE ROLE "{roles.migrator}" NOLOGIN CREATEROLE')
        with pytest.raises(RoleBootstrapError, match="not a restricted login role"):
            await bootstrap_database_roles(
                connection,
                schema=catalog.schema,
                roles=roles,
            )
    finally:
        await connection.close()
        await _drop_role_catalog(catalog)


@pytest.mark.asyncio
@pytest.mark.docker
async def test_role_bootstrap_rejects_an_existing_role_membership() -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    token = uuid4().hex[:10]
    roles = DatabaseRoleNames.from_prefix(f"test_qep_member_{token}")
    catalog = _RoleCatalog(database_url, f"test_roles_member_{token}", roles)
    parent_role = f"test_parent_{token}"
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f'CREATE ROLE "{parent_role}" NOLOGIN')
        await connection.execute(
            f'CREATE ROLE "{roles.migrator}" LOGIN NOINHERIT '
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        await connection.execute(f'GRANT "{parent_role}" TO "{roles.migrator}"')
        with pytest.raises(RoleBootstrapError, match="not a restricted login role"):
            await bootstrap_database_roles(
                connection,
                schema=catalog.schema,
                roles=roles,
            )
    finally:
        await connection.close()
        await _drop_role_catalog(catalog)
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'DROP ROLE IF EXISTS "{parent_role}"')
        finally:
            await connection.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_role_operator_commands_apply_the_real_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    token = uuid4().hex[:10]
    schema = f"test_roles_cli_{token}"
    prefix = f"test_qep_cli_{token}"
    roles = DatabaseRoleNames.from_prefix(prefix)
    catalog = _RoleCatalog(database_url, schema, roles)
    monkeypatch.setenv("QARUNNER_ROLE_DATABASE_URL", database_url)
    monkeypatch.setenv("QARUNNER_ROLE_SCHEMA", schema)
    monkeypatch.setenv("QARUNNER_ROLE_PREFIX", prefix)

    try:
        bootstrap = run_migration_operator(database_url, schema, "bootstrap-roles")
        assert bootstrap.returncode == 0, bootstrap.stderr
        assert database_url not in bootstrap.stdout
        assert database_url not in bootstrap.stderr

        migrator_url = _database_url_for_role(database_url, roles.migrator)
        migration = run_migration_operator(migrator_url, schema, "upgrade", "head")
        assert migration.returncode == 0, migration.stderr
        grants = run_migration_operator(migrator_url, schema, "apply-role-grants")
        assert grants.returncode == 0, grants.stderr
        assert migrator_url not in grants.stdout
        assert migrator_url not in grants.stderr

        api = await catalog.connect_as(roles.api)
        try:
            assert await api.fetchval("SELECT version_num FROM alembic_version") == (
                "m1_application_uow"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await api.fetchval("SELECT count(*) FROM qep_audit_events")
        finally:
            await api.close()
    finally:
        await _drop_role_catalog(catalog)


@pytest.mark.asyncio
@pytest.mark.docker
async def test_runtime_roles_are_limited_to_the_explicit_allowlist(
    role_catalog: _RoleCatalog,
) -> None:
    roles = role_catalog.roles

    api = await role_catalog.connect_as(roles.api)
    try:
        assert await api.fetchval("SELECT has_table_privilege(current_user, 'users', 'SELECT')")
        assert not await api.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_audit_events', 'SELECT')"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.fetchval("SELECT count(*) FROM qep_audit_events")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.execute("CREATE TABLE api_escape (id integer)")
    finally:
        await api.close()

    coordinator = await role_catalog.connect_as(roles.coordinator)
    try:
        assert await coordinator.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_audit_events', 'INSERT')"
        )
        assert not await coordinator.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_audit_events', 'DELETE')"
        )
        assert not await coordinator.fetchval(
            "SELECT has_table_privilege(current_user, 'users', 'SELECT')"
        )
        assert not await coordinator.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_projects', 'SELECT')"
        )
        assert not await coordinator.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_batch_rejections', 'INSERT')"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await coordinator.fetchval("SELECT count(*) FROM qep_projects")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await coordinator.execute("TRUNCATE qep_audit_events")
    finally:
        await coordinator.close()

    worker = await role_catalog.connect_as(roles.worker_protocol)
    try:
        assert not await worker.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_assignments', 'SELECT')"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await worker.fetchval("SELECT count(*) FROM qep_assignments")
    finally:
        await worker.close()

    recovery = await role_catalog.connect_as(roles.recovery_read)
    try:
        assert await recovery.fetchval("SELECT count(*) FROM qep_audit_events") == 0
        assert await recovery.fetchval("SELECT count(*) FROM alembic_version") == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await recovery.fetchval("SELECT password_hash FROM users")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await recovery.execute(
                "INSERT INTO qep_audit_events "
                "(id, actor_id, action, object_type, object_id, decision, occurred_at, payload) "
                "VALUES ('forbidden', 'actor', 'write', 'test', 'id', 'allow', now(), '{}')"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await recovery.fetch("SELECT id FROM qep_audit_events FOR UPDATE")
    finally:
        await recovery.close()

    migrator = await role_catalog.connect_as(roles.migrator)
    try:
        await migrator.execute("CREATE TABLE migrator_probe (id integer)")
        await migrator.execute("DROP TABLE migrator_probe")
    finally:
        await migrator.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_runtime_roles_cannot_create_relations_or_manage_roles(
    role_catalog: _RoleCatalog,
) -> None:
    token = uuid4().hex[:10]
    attempted_roles: list[str] = []
    try:
        for index, role in enumerate(role_catalog.roles.runtime):
            connection = await role_catalog.connect_as(role)
            attempted_role = f"forbidden_role_{token}_{index}"
            attempted_roles.append(attempted_role)
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(f"CREATE TABLE forbidden_table_{index} (id integer)")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.execute(f'CREATE ROLE "{attempted_role}"')
            finally:
                await connection.close()
    finally:
        admin = await asyncpg.connect(role_catalog.admin_database_url)
        try:
            for attempted_role in attempted_roles:
                await admin.execute(f'DROP ROLE IF EXISTS "{attempted_role}"')
        finally:
            await admin.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_api_role_can_pass_the_existing_runtime_head_check(
    role_catalog: _RoleCatalog,
) -> None:
    store = PostgresStore(
        _database_url_for_role(role_catalog.admin_database_url, role_catalog.roles.api),
        schema=role_catalog.schema,
        pool_min_size=1,
        pool_max_size=1,
    )
    try:
        await store.initialize()
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_api_role_fails_closed_on_a_stale_runtime_head(
    role_catalog: _RoleCatalog,
) -> None:
    migrator = await role_catalog.connect_as(role_catalog.roles.migrator)
    try:
        await migrator.execute("UPDATE alembic_version SET version_num = 'm1_batch_readiness'")
    finally:
        await migrator.close()

    store = PostgresStore(
        _database_url_for_role(role_catalog.admin_database_url, role_catalog.roles.api),
        schema=role_catalog.schema,
        pool_min_size=1,
        pool_max_size=1,
    )
    try:
        with pytest.raises(SchemaRevisionError) as error:
            await store.initialize()
        assert error.value.reason is SchemaRevisionReason.BEHIND
    finally:
        await store.close()
        migrator = await role_catalog.connect_as(role_catalog.roles.migrator)
        try:
            await migrator.execute("UPDATE alembic_version SET version_num = 'm1_application_uow'")
        finally:
            await migrator.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_role_password_rotation_preserves_the_explicit_grants(
    role_catalog: _RoleCatalog,
) -> None:
    roles = role_catalog.roles
    admin = await asyncpg.connect(role_catalog.admin_database_url)
    try:
        grants_before = await admin.fetch(
            """
            SELECT table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = $1 AND table_schema = $2
            ORDER BY table_name, privilege_type
            """,
            roles.api,
            role_catalog.schema,
        )
        first_password = f"test-{uuid4().hex}"
        second_password = f"test-{uuid4().hex}"
        await admin.execute(f"ALTER ROLE \"{roles.api}\" PASSWORD '{first_password}'")
        first_verifier = await admin.fetchval(
            "SELECT rolpassword FROM pg_catalog.pg_authid WHERE rolname = $1",
            roles.api,
        )
        await admin.execute(f"ALTER ROLE \"{roles.api}\" PASSWORD '{second_password}'")
        second_verifier = await admin.fetchval(
            "SELECT rolpassword FROM pg_catalog.pg_authid WHERE rolname = $1",
            roles.api,
        )
        grants_after = await admin.fetch(
            """
            SELECT table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE grantee = $1 AND table_schema = $2
            ORDER BY table_name, privilege_type
            """,
            roles.api,
            role_catalog.schema,
        )
    finally:
        await admin.close()

    assert first_verifier is not None
    assert second_verifier is not None
    assert first_verifier != second_verifier
    assert grants_after == grants_before

    fresh = await asyncpg.connect(
        role_catalog.admin_database_url,
        user=roles.api,
        password=second_password,
        server_settings={"search_path": role_catalog.schema},
    )
    try:
        assert await fresh.fetchval("SELECT version_num FROM alembic_version") == (
            "m1_application_uow"
        )
        assert not await fresh.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_audit_events', 'SELECT')"
        )
    finally:
        await fresh.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_grant_application_fails_closed_on_unknown_tables(
    role_catalog: _RoleCatalog,
) -> None:
    migrator = await role_catalog.connect_as(role_catalog.roles.migrator)
    try:
        await migrator.execute("CREATE TABLE unreviewed_future_table (id integer)")
        with pytest.raises(RoleBootstrapError, match="unreviewed tables"):
            await apply_database_role_grants(
                migrator,
                schema=role_catalog.schema,
                roles=role_catalog.roles,
            )
        assert not await migrator.fetchval(
            "SELECT has_table_privilege($1, 'unreviewed_future_table', 'SELECT')",
            role_catalog.roles.api,
        )
        await migrator.execute("DROP TABLE unreviewed_future_table")
    finally:
        await migrator.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_grant_application_fails_closed_on_missing_tables_and_unknown_sequences(
    role_catalog: _RoleCatalog,
) -> None:
    migrator = await role_catalog.connect_as(role_catalog.roles.migrator)
    try:
        await migrator.execute("DROP TABLE qep_versioned_fact_commands")
        with pytest.raises(RoleBootstrapError, match="missing tables"):
            await apply_database_role_grants(
                migrator,
                schema=role_catalog.schema,
                roles=role_catalog.roles,
            )
        await migrator.execute(
            """
            CREATE TABLE qep_versioned_fact_commands (
                idempotency_scope TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_digest CHAR(64) NOT NULL,
                command_digest CHAR(64) NOT NULL,
                response_status INTEGER NOT NULL,
                response_ref TEXT NOT NULL,
                fact_kind TEXT NOT NULL,
                fact_key TEXT NOT NULL,
                fact_version BIGINT NOT NULL,
                fact_payload_digest CHAR(64) NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (idempotency_scope, idempotency_key)
            )
            """
        )
        await migrator.execute("CREATE SEQUENCE unreviewed_future_sequence")
        with pytest.raises(RoleBootstrapError, match="sequence catalog"):
            await apply_database_role_grants(
                migrator,
                schema=role_catalog.schema,
                roles=role_catalog.roles,
            )
    finally:
        await migrator.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_grant_application_requires_the_migrator_identity(
    role_catalog: _RoleCatalog,
) -> None:
    api = await role_catalog.connect_as(role_catalog.roles.api)
    try:
        with pytest.raises(RoleBootstrapError, match="must run as the migrator"):
            await apply_database_role_grants(
                api,
                schema=role_catalog.schema,
                roles=role_catalog.roles,
            )
    finally:
        await api.close()

    admin = await asyncpg.connect(role_catalog.admin_database_url)
    try:
        with pytest.raises(RoleBootstrapError, match="must run as the migrator"):
            await apply_database_role_grants(
                admin,
                schema=role_catalog.schema,
                roles=role_catalog.roles,
            )
    finally:
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_revocation_takes_effect_on_a_fresh_connection_and_reapply_is_idempotent(
    role_catalog: _RoleCatalog,
) -> None:
    roles = role_catalog.roles
    migrator = await role_catalog.connect_as(roles.migrator)
    try:
        await migrator.execute(
            f'REVOKE SELECT ON TABLE qep_audit_events FROM "{roles.coordinator}"'
        )
    finally:
        await migrator.close()

    revoked = await role_catalog.connect_as(roles.coordinator)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await revoked.fetchval("SELECT count(*) FROM qep_audit_events")
    finally:
        await revoked.close()

    migrator = await role_catalog.connect_as(roles.migrator)
    try:
        await apply_database_role_grants(
            migrator,
            schema=role_catalog.schema,
            roles=roles,
        )
        await apply_database_role_grants(
            migrator,
            schema=role_catalog.schema,
            roles=roles,
        )
    finally:
        await migrator.close()

    restored = await role_catalog.connect_as(roles.coordinator)
    try:
        assert await restored.fetchval("SELECT count(*) FROM qep_audit_events") == 0
        assert not await restored.fetchval(
            "SELECT has_table_privilege(current_user, 'qep_audit_events', 'DELETE')"
        )
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.docker
async def test_reapply_removes_runtime_default_table_privileges(
    role_catalog: _RoleCatalog,
) -> None:
    roles = role_catalog.roles
    migrator = await role_catalog.connect_as(roles.migrator)
    try:
        await migrator.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{role_catalog.schema}" '
            f'GRANT SELECT ON TABLES TO "{roles.api}"'
        )
        await apply_database_role_grants(
            migrator,
            schema=role_catalog.schema,
            roles=roles,
        )
        await migrator.execute("CREATE TABLE future_default_probe (id integer)")
    finally:
        await migrator.close()

    api = await role_catalog.connect_as(roles.api)
    try:
        assert not await api.fetchval(
            "SELECT has_table_privilege(current_user, 'future_default_probe', 'SELECT')"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await api.fetchval("SELECT count(*) FROM future_default_probe")
    finally:
        await api.close()
        migrator = await role_catalog.connect_as(roles.migrator)
        try:
            await migrator.execute("DROP TABLE future_default_probe")
        finally:
            await migrator.close()
