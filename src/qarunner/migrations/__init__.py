"""Operator-owned PostgreSQL migration authority."""

from qarunner.migrations.adoption import LegacyAdoptionError, adopt_legacy_schema
from qarunner.migrations.config import build_alembic_config, expected_heads
from qarunner.migrations.runtime import SchemaRevisionError, validate_schema_revision

__all__ = [
    "LegacyAdoptionError",
    "SchemaRevisionError",
    "adopt_legacy_schema",
    "build_alembic_config",
    "expected_heads",
    "validate_schema_revision",
]
