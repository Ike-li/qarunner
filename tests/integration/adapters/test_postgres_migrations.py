"""T-M1-MIG-001 operator migration and runtime fail-closed contracts."""

from __future__ import annotations

import asyncio
import os
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tests.integration.migration_operator import run_migration_operator

import qarunner.migrations.runtime as migration_runtime
from qarunner.adapters.postgres_store import PostgresStore
from qarunner.migrations.adoption import LegacyAdoptionError, adopt_legacy_schema
from qarunner.migrations.cli import main
from qarunner.migrations.config import (
    build_alembic_config,
    expected_heads,
    to_async_sqlalchemy_url,
    validate_schema_name,
)
from qarunner.migrations.environment import run_environment
from qarunner.migrations.legacy import LEGACY_CHECKSUMS, LEGACY_MIGRATIONS
from qarunner.migrations.runtime import (
    SchemaRevisionError,
    SchemaRevisionReason,
    validate_schema_revision,
)
from qarunner.migrations.versions import (
    legacy_baseline,
    m1_application_uow,
    m1_batch_readiness,
    m1_greenfield_core,
    m1_greenfield_facts,
    m1_preexecution_proof,
    m2_suite_aggregate,
)

_EXPECTED_LEGACY_TABLES = {
    "credentials",
    "run_ai_diagnosis",
    "run_test_cases",
    "runs",
    "schema_migrations",
    "suites",
    "test_profiles",
    "test_schedules",
    "users",
}
_EXPECTED_GREENFIELD_TABLES = {
    "qep_assignments",
    "qep_attempt_events",
    "qep_attempts",
    "qep_audit_events",
    "qep_batch_cancellation_intents",
    "qep_batch_cancellation_scope_items",
    "qep_batch_finalization_bases",
    "qep_batch_finalization_readiness_facts",
    "qep_batch_item_resolutions",
    "qep_batch_preexecution_closure_bases",
    "qep_batch_preexecution_scope_items",
    "qep_batch_rejections",
    "qep_batch_success_policies",
    "qep_batches",
    "qep_case_manifests",
    "qep_duplicate_risk_acceptances",
    "qep_evidence_index",
    "qep_manifest_items",
    "qep_materialized_scope_handoffs",
    "qep_outbox_events",
    "qep_platform_retry_policies",
    "qep_preexecution_planned_scope_seals",
    "qep_preexecution_task_inventory_seals",
    "qep_preexecution_task_ledgers",
    "qep_preexecution_tasks",
    "qep_principals",
    "qep_projects",
    "qep_resource_profiles",
    "qep_retry_intents",
    "qep_role_bindings",
    "qep_run_finalization_bases",
    "qep_run_item_resolutions",
    "qep_run_manifest_items",
    "qep_runs",
    "qep_shard_plans",
    "qep_suite_retry_policies",
    "qep_suite_revisions",
    "qep_suites",
    "qep_unknown_adjudications",
    "qep_unknown_observations",
    "qep_unknown_review_policies",
    "qep_versioned_fact_commands",
    "qep_versioned_fact_snapshots",
    "qep_worker_generations",
    "qep_workers",
}


@pytest.fixture
async def empty_postgres_schema() -> tuple[str, str]:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    schema = f"test_{uuid4().hex}"
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()

    try:
        yield database_url, schema
    finally:
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()


def test_in_process_operator_paths_cover_real_alembic_and_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    empty_schema = f"test_{uuid4().hex}"
    legacy_schema = f"test_{uuid4().hex}"

    async def create_schema(schema: str) -> None:
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'CREATE SCHEMA "{schema}"')
        finally:
            await connection.close()

    async def drop_schema(schema: str) -> None:
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()

    asyncio.run(create_schema(empty_schema))
    asyncio.run(create_schema(legacy_schema))
    try:
        monkeypatch.setenv("QARUNNER_MIGRATION_DATABASE_URL", database_url)
        monkeypatch.setenv("QARUNNER_MIGRATION_SCHEMA", empty_schema)
        assert main(["upgrade", "head"]) == 0
        with pytest.raises(RuntimeError, match="ALLOW_DOWNGRADE=true"):
            main(["downgrade", "base"])
        monkeypatch.setenv("QARUNNER_MIGRATION_ALLOW_DOWNGRADE", "true")
        assert main(["downgrade", "base"]) == 0

        asyncio.run(_install_exact_legacy_schema(database_url, legacy_schema))
        monkeypatch.setenv("QARUNNER_MIGRATION_SCHEMA", legacy_schema)
        assert main(["adopt-legacy"]) == 0
        config = build_alembic_config(database_url=database_url, schema=legacy_schema)
        asyncio.run(
            adopt_legacy_schema(
                config,
                database_url=database_url,
                schema=legacy_schema,
            )
        )
    finally:
        asyncio.run(drop_schema(empty_schema))
        asyncio.run(drop_schema(legacy_schema))


def test_in_process_revision_scripts_execute_upgrade_and_downgrade() -> None:
    database_url = os.environ["QARUNNER_TEST_DATABASE_URL"]
    schema = f"test_{uuid4().hex}"

    async def exercise() -> None:
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(f'CREATE SCHEMA "{schema}"')
        finally:
            await connection.close()

        engine = create_async_engine(
            to_async_sqlalchemy_url(database_url),
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )

        def invoke(sync_connection: object, module: object, operation: str) -> None:
            migration_context = MigrationContext.configure(sync_connection)
            operations = Operations(migration_context)
            previous = module.op
            module.op = operations
            try:
                getattr(module, operation)()
            finally:
                module.op = previous

        try:
            async with engine.begin() as sql_connection:
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, legacy_baseline, "upgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_greenfield_core, "upgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_greenfield_facts, "upgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_batch_readiness, "upgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_application_uow, "upgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_preexecution_proof, "upgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m2_suite_aggregate, "upgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m2_suite_aggregate, "downgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_preexecution_proof, "downgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_application_uow, "downgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_batch_readiness, "downgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_greenfield_facts, "downgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, m1_greenfield_core, "downgrade")
                )
                await sql_connection.run_sync(
                    lambda sync: invoke(sync, legacy_baseline, "downgrade")
                )
        finally:
            await engine.dispose()
            connection = await asyncpg.connect(database_url)
            try:
                await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
            finally:
                await connection.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("mutation", "already_adopted"),
    [
        ("UPDATE alembic_version SET version_num = 'rogue_revision'", True),
        ("CREATE TABLE drift_table (id INTEGER)", False),
        ("ALTER TABLE users ALTER COLUMN role DROP NOT NULL", False),
        (
            "ALTER TABLE test_schedules DROP CONSTRAINT test_schedules_profile_id_fkey",
            False,
        ),
        ("DROP INDEX idx_runs_cleanup_candidates", False),
        ("DELETE FROM schema_migrations WHERE version = 5", False),
        (
            "UPDATE schema_migrations SET checksum = repeat('0', 64) WHERE version = 4",
            False,
        ),
    ],
    ids=[
        "revision",
        "table",
        "column",
        "constraint",
        "index",
        "ledger",
        "checksum",
    ],
)
@pytest.mark.asyncio
async def test_in_process_adoption_rejects_every_catalog_drift_branch(
    empty_postgres_schema: tuple[str, str],
    mutation: str,
    already_adopted: bool,
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)
    config = build_alembic_config(database_url=database_url, schema=schema)
    if already_adopted:
        await adopt_legacy_schema(config, database_url=database_url, schema=schema)

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute(mutation)
    finally:
        await connection.close()

    with pytest.raises(LegacyAdoptionError):
        await adopt_legacy_schema(config, database_url=database_url, schema=schema)


def test_alembic_environment_rejects_offline_mode() -> None:
    fake_context = SimpleNamespace(is_offline_mode=lambda: True)

    with pytest.raises(RuntimeError, match="require an online PostgreSQL connection"):
        run_environment(fake_context)


def test_alembic_environment_supports_caller_connection_and_online_engine(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    configured_connections: list[object] = []
    migration_runs = 0

    def configure(**kwargs: object) -> None:
        configured_connections.append(kwargs["connection"])

    def run_migrations() -> None:
        nonlocal migration_runs
        migration_runs += 1

    caller_connection = object()
    caller_config = build_alembic_config(database_url=database_url, schema=schema)
    caller_config.attributes["connection"] = caller_connection
    caller_context = SimpleNamespace(
        config=caller_config,
        is_offline_mode=lambda: False,
        configure=configure,
        begin_transaction=nullcontext,
        run_migrations=run_migrations,
    )
    run_environment(caller_context)

    online_config = build_alembic_config(database_url=database_url, schema=schema)
    online_context = SimpleNamespace(
        config=online_config,
        is_offline_mode=lambda: False,
        configure=configure,
        begin_transaction=nullcontext,
        run_migrations=run_migrations,
    )
    run_environment(online_context)

    assert configured_connections[0] is caller_connection
    assert len(configured_connections) == 2
    assert migration_runs == 2


def test_migration_config_rejects_unsafe_inputs_and_supports_postgres_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert validate_schema_name("safe_schema") == "safe_schema"
    with pytest.raises(ValueError, match="safe lowercase identifier"):
        validate_schema_name("Unsafe-Schema")
    with pytest.raises(ValueError, match="safe lowercase identifier"):
        validate_schema_name("s" * 64)
    assert to_async_sqlalchemy_url("postgresql+asyncpg://db") == "postgresql+asyncpg://db"
    assert to_async_sqlalchemy_url("postgresql://db") == "postgresql+asyncpg://db"
    assert to_async_sqlalchemy_url("postgres://db") == "postgresql+asyncpg://db"
    with pytest.raises(ValueError, match="must use PostgreSQL"):
        to_async_sqlalchemy_url("sqlite:///db")

    monkeypatch.delenv("QARUNNER_MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        build_alembic_config(schema="safe_schema")
    config = build_alembic_config(
        database_url="postgresql://user:p%40ss@db/qarunner",
        schema="safe_schema",
    )
    assert config.attributes["qarunner_schema"] == "safe_schema"
    assert expected_heads() == ("m2_suite_aggregate",)


@pytest.mark.asyncio
async def test_runtime_fails_closed_when_code_has_multiple_heads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(migration_runtime, "expected_heads", lambda: ("head_a", "head_b"))

    class UnusedConnection:
        async def fetchval(self, *_args: object) -> object:
            raise AssertionError("database must not be queried when code has multiple heads")

    with pytest.raises(SchemaRevisionError) as raised:
        await validate_schema_revision(UnusedConnection(), schema="safe_schema")
    assert raised.value.reason is SchemaRevisionReason.CODE_HAS_MULTIPLE_HEADS


def test_migration_module_entrypoint_exports_main() -> None:
    from qarunner.migrations.__main__ import main as entrypoint_main

    assert entrypoint_main is main


async def _install_exact_legacy_schema(database_url: str, schema: str) -> None:
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for version, ddl in LEGACY_MIGRATIONS:
            await connection.execute(ddl)
            await connection.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                version,
                LEGACY_CHECKSUMS[version],
            )
    finally:
        await connection.close()


def test_operator_upgrade_command_exists(empty_postgres_schema: tuple[str, str]) -> None:
    database_url, schema = empty_postgres_schema
    result = run_migration_operator(database_url, schema, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    assert database_url not in result.stdout
    assert database_url not in result.stderr


@pytest.mark.asyncio
async def test_empty_schema_upgrade_matches_authorized_catalog(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    result = run_migration_operator(database_url, schema, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    connection = await asyncpg.connect(database_url)
    try:
        tables = {
            row["table_name"]
            for row in await connection.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = $1
                """,
                schema,
            )
        }
        revision = await connection.fetchval(f'SELECT version_num FROM "{schema}".alembic_version')
        legacy_ledger = await connection.fetch(
            f'SELECT version, checksum FROM "{schema}".schema_migrations ORDER BY version'
        )
    finally:
        await connection.close()

    assert tables == {"alembic_version"} | _EXPECTED_LEGACY_TABLES | _EXPECTED_GREENFIELD_TABLES
    assert revision == "m2_suite_aggregate"
    assert [row["version"] for row in legacy_ledger] == list(range(1, 11))
    assert all(len(row["checksum"]) == 64 for row in legacy_ledger)


@pytest.mark.asyncio
async def test_batch_readiness_migration_adds_authoritative_fact_and_ref_bindings(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    result = run_migration_operator(database_url, schema, "upgrade", "m1_batch_readiness")
    assert result.returncode == 0, result.stderr

    connection = await asyncpg.connect(database_url)
    try:
        readiness_columns = {
            row["column_name"]
            for row in await connection.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = $1
                  AND table_name = 'qep_batch_finalization_readiness_facts'
                """,
                schema,
            )
        }
        ref_columns = {
            (row["table_name"], row["column_name"])
            for row in await connection.fetch(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = $1
                  AND (
                    (table_name = 'qep_batches'
                     AND column_name = 'finalization_readiness_ref')
                    OR
                    (table_name = 'qep_batch_finalization_bases'
                     AND column_name = 'readiness_ref')
                  )
                """,
                schema,
            )
        }
        revision = await connection.fetchval(f'SELECT version_num FROM "{schema}".alembic_version')
    finally:
        await connection.close()

    assert readiness_columns == {
        "ref",
        "batch_id",
        "source_batch_version",
        "batch_version",
        "manifest_digest",
        "shard_plan_digest",
        "canonical_run_set_digest",
        "success_policy_digest",
        "batch_cancellation_intent_digest",
        "readiness_digest",
        "authority_digest",
        "write_epoch",
        "compatibility_epoch",
        "state_model_version",
        "payload",
        "recorded_at",
    }
    assert ref_columns == {
        ("qep_batches", "finalization_readiness_ref"),
        ("qep_batch_finalization_bases", "readiness_ref"),
    }
    assert revision == "m1_batch_readiness"


@pytest.mark.asyncio
async def test_application_uow_migration_adds_generic_fact_and_replay_relations(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    result = run_migration_operator(database_url, schema, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    connection = await asyncpg.connect(database_url)
    try:
        columns = {
            (row["table_name"], row["column_name"])
            for row in await connection.fetch(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = $1
                  AND table_name IN (
                    'qep_versioned_fact_snapshots',
                    'qep_versioned_fact_commands'
                  )
                """,
                schema,
            )
        }
        foreign_keys = {
            row["constraint_name"]
            for row in await connection.fetch(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = $1
                  AND table_name = 'qep_versioned_fact_commands'
                  AND constraint_type = 'FOREIGN KEY'
                """,
                schema,
            )
        }
        revision = await connection.fetchval(f'SELECT version_num FROM "{schema}".alembic_version')
    finally:
        await connection.close()

    assert columns == {
        ("qep_versioned_fact_snapshots", "fact_kind"),
        ("qep_versioned_fact_snapshots", "fact_key"),
        ("qep_versioned_fact_snapshots", "version"),
        ("qep_versioned_fact_snapshots", "fact_id"),
        ("qep_versioned_fact_snapshots", "codec_schema_version"),
        ("qep_versioned_fact_snapshots", "payload_digest"),
        ("qep_versioned_fact_snapshots", "payload"),
        ("qep_versioned_fact_snapshots", "recorded_at"),
        ("qep_versioned_fact_commands", "idempotency_scope"),
        ("qep_versioned_fact_commands", "idempotency_key"),
        ("qep_versioned_fact_commands", "request_digest"),
        ("qep_versioned_fact_commands", "command_digest"),
        ("qep_versioned_fact_commands", "response_status"),
        ("qep_versioned_fact_commands", "response_ref"),
        ("qep_versioned_fact_commands", "fact_kind"),
        ("qep_versioned_fact_commands", "fact_key"),
        ("qep_versioned_fact_commands", "fact_version"),
        ("qep_versioned_fact_commands", "fact_payload_digest"),
        ("qep_versioned_fact_commands", "recorded_at"),
    }
    assert len(foreign_keys) == 1
    assert revision == "m2_suite_aggregate"


@pytest.mark.asyncio
async def test_greenfield_catalog_enforces_m1_uniqueness_and_fence_constraints(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    result = run_migration_operator(database_url, schema, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        constraints = {
            (
                row["table_name"],
                row["constraint_type"],
                row["definition"].replace(f'"{schema}".', "").replace(f"{schema}.", ""),
            )
            for row in await connection.fetch(
                """
                SELECT relation.relname AS table_name,
                       con.contype::text AS constraint_type,
                       pg_get_constraintdef(con.oid, true) AS definition
                FROM pg_constraint AS con
                JOIN pg_class AS relation ON relation.oid = con.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = $1
                  AND relation.relname LIKE 'qep_%'
                """,
                schema,
            )
        }
        indexes = {
            row["index_name"]: row["indexdef"].replace(f'"{schema}".', "")
            for row in await connection.fetch(
                """
                SELECT indexname AS index_name, indexdef
                FROM pg_indexes
                WHERE schemaname = $1
                  AND tablename LIKE 'qep_%'
                """,
                schema,
            )
        }
    finally:
        await connection.close()

    required_constraints = {
        (
            "qep_batches",
            "u",
            "UNIQUE (idempotency_scope, idempotency_key)",
        ),
        ("qep_attempt_events", "p", "PRIMARY KEY (attempt_id, event_id)"),
        ("qep_attempt_events", "u", "UNIQUE (attempt_id, event_seq)"),
        ("qep_attempts", "u", "UNIQUE (run_id, fence)"),
        ("qep_evidence_index", "u", "UNIQUE (attempt_id)"),
        ("qep_outbox_events", "u", "UNIQUE (event_id)"),
        (
            "qep_run_manifest_items",
            "u",
            "UNIQUE (manifest_id, item_index)",
        ),
        (
            "qep_run_item_resolutions",
            "f",
            "FOREIGN KEY (run_id, manifest_id, item_index) "
            "REFERENCES qep_run_manifest_items(run_id, manifest_id, item_index)",
        ),
    }
    assert required_constraints <= constraints
    assert "qep_assignments_one_active_per_run" in indexes
    assert "WHERE (state = ANY" in indexes["qep_assignments_one_active_per_run"]
    assert "qep_outbox_pending_idx" in indexes
    assert "qep_runs_batch_phase_idx" in indexes


@pytest.mark.asyncio
async def test_exact_legacy_v10_schema_can_be_adopted_then_upgraded(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute(
            """
            INSERT INTO users (username, password_hash, role, created_at)
            VALUES ('legacy-user', 'legacy-hash', 'user', '2026-07-18T00:00:00Z')
            """
        )
        ledger_before = await connection.fetch(
            "SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"
        )
    finally:
        await connection.close()

    adoption = run_migration_operator(database_url, schema, "adopt-legacy")

    assert adoption.returncode == 0, adoption.stderr
    assert database_url not in adoption.stdout
    assert database_url not in adoption.stderr

    connection = await asyncpg.connect(database_url)
    try:
        revision = await connection.fetchval(f'SELECT version_num FROM "{schema}".alembic_version')
        tables_after_adoption = {
            row["table_name"]
            for row in await connection.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = $1
                """,
                schema,
            )
        }
    finally:
        await connection.close()

    assert revision == "legacy_baseline"
    assert tables_after_adoption == {"alembic_version"} | _EXPECTED_LEGACY_TABLES

    upgrade = run_migration_operator(database_url, schema, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        ledger_after = await connection.fetch(
            "SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"
        )
        legacy_user = await connection.fetchrow(
            "SELECT username, password_hash, role FROM users WHERE username = 'legacy-user'"
        )
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await connection.close()

    assert ledger_after == ledger_before
    assert dict(legacy_user) == {
        "username": "legacy-user",
        "password_hash": "legacy-hash",
        "role": "user",
    }
    assert revision == "m2_suite_aggregate"


@pytest.mark.asyncio
async def test_legacy_adoption_rejects_column_drift_without_stamping(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute("ALTER TABLE users ALTER COLUMN role DROP NOT NULL")
    finally:
        await connection.close()

    adoption = run_migration_operator(database_url, schema, "adopt-legacy")

    assert adoption.returncode != 0
    assert "legacy catalog column mismatch" in adoption.stderr
    connection = await asyncpg.connect(database_url)
    try:
        alembic_state_exists = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = $1
                  AND table_name = 'alembic_version'
            )
            """,
            schema,
        )
        role_nullable = await connection.fetchval(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = 'users'
              AND column_name = 'role'
            """,
            schema,
        )
    finally:
        await connection.close()

    assert alembic_state_exists is False
    assert role_nullable == "YES"


@pytest.mark.asyncio
async def test_legacy_adoption_rejects_constraint_drift_without_stamping(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        constraint_name = await connection.fetchval(
            """
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_schema = current_schema()
              AND table_name = 'test_schedules'
              AND constraint_type = 'FOREIGN KEY'
            """
        )
        await connection.execute(f'ALTER TABLE test_schedules DROP CONSTRAINT "{constraint_name}"')
    finally:
        await connection.close()

    adoption = run_migration_operator(database_url, schema, "adopt-legacy")

    assert adoption.returncode != 0
    assert "legacy catalog constraint mismatch" in adoption.stderr
    connection = await asyncpg.connect(database_url)
    try:
        alembic_state_exists = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = $1
                  AND table_name = 'alembic_version'
            )
            """,
            schema,
        )
    finally:
        await connection.close()

    assert alembic_state_exists is False


@pytest.mark.asyncio
async def test_legacy_adoption_rejects_index_drift_without_stamping(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute("DROP INDEX idx_runs_cleanup_candidates")
    finally:
        await connection.close()

    adoption = run_migration_operator(database_url, schema, "adopt-legacy")

    assert adoption.returncode != 0
    assert "legacy catalog index mismatch" in adoption.stderr
    connection = await asyncpg.connect(database_url)
    try:
        alembic_state_exists = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = $1
                  AND table_name = 'alembic_version'
            )
            """,
            schema,
        )
    finally:
        await connection.close()

    assert alembic_state_exists is False


@pytest.mark.parametrize(
    ("ledger_mutation", "expected_error"),
    [
        (
            "UPDATE schema_migrations SET checksum = repeat('0', 64) WHERE version = 4",
            "legacy migration 4 checksum mismatch",
        ),
        (
            "DELETE FROM schema_migrations WHERE version = 5",
            "legacy migration ledger must contain exactly contiguous versions 1 through 10",
        ),
        (
            "INSERT INTO schema_migrations (version, checksum) VALUES (999, repeat('0', 64))",
            "legacy migration ledger must contain exactly contiguous versions 1 through 10",
        ),
    ],
    ids=["checksum", "non-contiguous", "unknown-version"],
)
@pytest.mark.asyncio
async def test_legacy_adoption_rejects_ledger_drift_without_mutation(
    empty_postgres_schema: tuple[str, str],
    ledger_mutation: str,
    expected_error: str,
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute(ledger_mutation)
        ledger_before = await connection.fetch(
            "SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"
        )
    finally:
        await connection.close()

    adoption = run_migration_operator(database_url, schema, "adopt-legacy")

    assert adoption.returncode != 0
    assert expected_error in adoption.stderr
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        ledger_after = await connection.fetch(
            "SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"
        )
        alembic_state_exists = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'alembic_version'
            )
            """
        )
    finally:
        await connection.close()

    assert ledger_after == ledger_before
    assert alembic_state_exists is False


@pytest.mark.asyncio
async def test_legacy_adoption_is_idempotent_at_baseline(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)

    first = run_migration_operator(database_url, schema, "adopt-legacy")
    second = run_migration_operator(database_url, schema, "adopt-legacy")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    connection = await asyncpg.connect(database_url)
    try:
        revisions = await connection.fetch(f'SELECT version_num FROM "{schema}".alembic_version')
    finally:
        await connection.close()

    assert [row["version_num"] for row in revisions] == ["legacy_baseline"]


@pytest.mark.parametrize(
    "revision_mutation",
    [
        "UPDATE alembic_version SET version_num = 'm1_greenfield_core'",
        "UPDATE alembic_version SET version_num = 'rogue_revision'",
        "INSERT INTO alembic_version (version_num) VALUES ('m1_greenfield_core')",
    ],
    ids=["known-ahead", "unknown", "branched"],
)
@pytest.mark.asyncio
async def test_legacy_adoption_rejects_incompatible_alembic_state_without_mutation(
    empty_postgres_schema: tuple[str, str],
    revision_mutation: str,
) -> None:
    database_url, schema = empty_postgres_schema
    await _install_exact_legacy_schema(database_url, schema)
    first = run_migration_operator(database_url, schema, "adopt-legacy")
    assert first.returncode == 0, first.stderr

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute(revision_mutation)
        revisions_before = await connection.fetch(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        )
        ledger_before = await connection.fetch(
            "SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"
        )
    finally:
        await connection.close()

    retry = run_migration_operator(database_url, schema, "adopt-legacy")

    assert retry.returncode != 0
    assert "legacy Schema has incompatible Alembic revision state" in retry.stderr
    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        revisions_after = await connection.fetch(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        )
        ledger_after = await connection.fetch(
            "SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"
        )
    finally:
        await connection.close()

    assert revisions_after == revisions_before
    assert ledger_after == ledger_before


@pytest.mark.asyncio
async def test_runtime_startup_rejects_unmigrated_schema_without_ddl(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    store = PostgresStore(database_url, schema=schema)

    with pytest.raises(RuntimeError, match="schema revision is missing"):
        await store.initialize()

    connection = await asyncpg.connect(database_url)
    try:
        tables = await connection.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = $1
            """,
            schema,
        )
    finally:
        await connection.close()

    assert tables == []


@pytest.mark.asyncio
async def test_previous_revision_requires_operator_upgrade_and_preserves_legacy_facts(
    empty_postgres_schema: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, schema = empty_postgres_schema
    monkeypatch.setenv(
        "QARUNNER_ADMIN_PASSWORD",
        "migration-runtime-admin-password-with-at-least-32-characters",
    )
    baseline = run_migration_operator(database_url, schema, "upgrade", "legacy_baseline")
    assert baseline.returncode == 0, baseline.stderr

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute(
            """
            INSERT INTO users (username, password_hash, role, created_at)
            VALUES ('before-greenfield', 'legacy-hash', 'user', '2026-07-18T00:00:00Z')
            """
        )
    finally:
        await connection.close()

    store = PostgresStore(database_url, schema=schema)
    with pytest.raises(RuntimeError, match="schema revision is behind"):
        await store.initialize()

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        users_before_upgrade = await connection.fetch(
            "SELECT username FROM users ORDER BY username"
        )
    finally:
        await connection.close()
    assert [row["username"] for row in users_before_upgrade] == ["before-greenfield"]

    upgrade = run_migration_operator(database_url, schema, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    await store.initialize()
    try:
        assert await store.get_user("before-greenfield") is not None
        assert await store.get_user("admin") is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_failed_upgrade_rolls_back_partial_ddl_and_forward_fix_succeeds(
    empty_postgres_schema: tuple[str, str],
) -> None:
    database_url, schema = empty_postgres_schema
    baseline = run_migration_operator(database_url, schema, "upgrade", "legacy_baseline")
    assert baseline.returncode == 0, baseline.stderr

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute("CREATE TABLE qep_attempts (id TEXT PRIMARY KEY)")
    finally:
        await connection.close()

    failed = run_migration_operator(database_url, schema, "upgrade", "head")
    assert failed.returncode != 0

    connection = await asyncpg.connect(database_url)
    try:
        revision_after_failure = await connection.fetchval(
            f'SELECT version_num FROM "{schema}".alembic_version'
        )
        created_before_conflict = await connection.fetchval(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = $1
              AND table_name IN ('qep_projects', 'qep_principals', 'qep_role_bindings')
            """,
            schema,
        )
    finally:
        await connection.close()

    assert revision_after_failure == "legacy_baseline"
    assert created_before_conflict == 0

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute("DROP TABLE qep_attempts")
    finally:
        await connection.close()

    fixed = run_migration_operator(database_url, schema, "upgrade", "head")
    assert fixed.returncode == 0, fixed.stderr


@pytest.mark.asyncio
async def test_downgrade_requires_explicit_disposable_opt_in_and_reverts_schema(
    empty_postgres_schema: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, schema = empty_postgres_schema
    upgraded = run_migration_operator(database_url, schema, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    blocked = run_migration_operator(database_url, schema, "downgrade", "base")
    assert blocked.returncode != 0
    assert "QARUNNER_MIGRATION_ALLOW_DOWNGRADE=true" in blocked.stderr

    monkeypatch.setenv("QARUNNER_MIGRATION_ALLOW_DOWNGRADE", "true")
    reverted = run_migration_operator(database_url, schema, "downgrade", "base")
    assert reverted.returncode == 0, reverted.stderr

    connection = await asyncpg.connect(database_url)
    try:
        remaining_tables = {
            row["table_name"]
            for row in await connection.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = $1
                """,
                schema,
            )
        }
    finally:
        await connection.close()

    assert remaining_tables == {"alembic_version"}


@pytest.mark.parametrize(
    ("revision_mutation", "expected_reason"),
    [
        (
            "DELETE FROM alembic_version",
            SchemaRevisionReason.MISSING,
        ),
        (
            "INSERT INTO alembic_version (version_num) VALUES ('legacy_baseline')",
            SchemaRevisionReason.BRANCHED,
        ),
        (
            "UPDATE alembic_version SET version_num = 'm2_future_head'",
            SchemaRevisionReason.AHEAD_OR_UNRECOGNIZED,
        ),
        (
            "UPDATE alembic_version SET version_num = 'rogue_revision'",
            SchemaRevisionReason.AHEAD_OR_UNRECOGNIZED,
        ),
    ],
    ids=["empty-version", "branched", "ahead", "unrecognized"],
)
@pytest.mark.asyncio
async def test_runtime_startup_rejects_non_head_revision_without_dml(
    empty_postgres_schema: tuple[str, str],
    revision_mutation: str,
    expected_reason: SchemaRevisionReason,
) -> None:
    database_url, schema = empty_postgres_schema
    upgrade = run_migration_operator(database_url, schema, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        await connection.execute(
            """
            INSERT INTO users (username, password_hash, role, created_at)
            VALUES ('revision-witness', 'legacy-hash', 'user', '2026-07-18T00:00:00Z')
            """
        )
        await connection.execute(revision_mutation)
        revisions_before = await connection.fetch(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        )
    finally:
        await connection.close()

    store = PostgresStore(database_url, schema=schema)
    with pytest.raises(SchemaRevisionError) as raised:
        await store.initialize()
    assert raised.value.reason is expected_reason

    connection = await asyncpg.connect(
        database_url,
        server_settings={"search_path": schema},
    )
    try:
        revisions_after = await connection.fetch(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        )
        users_after = await connection.fetch("SELECT username FROM users ORDER BY username")
    finally:
        await connection.close()

    assert revisions_after == revisions_before
    assert [row["username"] for row in users_after] == ["revision-witness"]
