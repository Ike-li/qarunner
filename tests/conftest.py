"""Shared pytest setup.

SEC-2 makes ``QARUNNER_SECRET_KEY`` / ``QARUNNER_ADMIN_PASSWORD`` required and
rejects known placeholders, and ``Settings()`` is instantiated throughout the
suite (lifespan, container wiring, route handlers). Inject strong, non-placeholder
values here at import time — before any test module is collected — so every
``Settings()`` validates. Individual tests may still ``monkeypatch.setenv`` to
override these for a single test.
"""

from __future__ import annotations

import os

# Set unconditionally so the suite is deterministic regardless of the ambient
# environment (e.g. a developer exporting a placeholder key). Per-test
# monkeypatching still overrides these afterwards.
os.environ["QARUNNER_SECRET_KEY"] = "test-secret-" + "x" * 60
os.environ["QARUNNER_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["QARUNNER_DATABASE_BACKEND"] = "postgres"
os.environ["QARUNNER_DATABASE_URL"] = "postgresql://unit-test.invalid/qarunner"
