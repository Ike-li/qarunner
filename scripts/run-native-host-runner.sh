#!/usr/bin/env bash
# run-native-host-runner.sh — launch the qarunner backend NATIVELY on the macOS
# host so the *subprocess* executor can run host-coupled E2E suites.
#
# Why this exists
# ---------------
# qarunner's docker executor is a hardened Linux sandbox: it has no node/npm, no
# Claude Code CLI, and none of the app-under-test's macOS-native modules. A
# Playwright suite like my-e2e-suite spawns the REAL app (my-app),
# which needs the `claude` CLI + native deps — so it cannot run in that sandbox.
# Running the backend on the host makes the subprocess executor inherit the host
# PATH (node/npx/claude) and native toolchain, and the suite passes unchanged.
#
# Security trade-off: the subprocess executor runs test code directly on the host
# with NO container isolation. Use this ONLY for suites you trust. Untrusted /
# pytest suites should keep using the docker executor (docker-compose.dev.yml).
#
# Usage
# -----
#   QARUNNER_SECRET_KEY='<64+ random chars>' \
#   QARUNNER_ADMIN_PASSWORD='<strong, non-default>' \
#     ./scripts/run-native-host-runner.sh
#
# Then in the UI/API trigger a run with:
#   runner=playwright, executor_mode=subprocess,
#   tests_path=my-e2e-suite,
#   env={ "APP_REPO_PATH": "<abs path to my-app>" }
#
# QARUNNER_TESTS_ROOT defaults to ~/code so a suite dir directly under it (e.g.
# ~/code/my-e2e-suite) resolves without tripping the safe_subpath guard.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${QARUNNER_SECRET_KEY:?set a 64+ character secret (export QARUNNER_SECRET_KEY=...)}"
: "${QARUNNER_ADMIN_PASSWORD:?set a strong, non-default admin password (export QARUNNER_ADMIN_PASSWORD=...)}"

export PYTHONPATH=src
export QARUNNER_TESTS_ROOT="${QARUNNER_TESTS_ROOT:-$HOME/code}"
export QARUNNER_ARTIFACTS_ROOT="${QARUNNER_ARTIFACTS_ROOT:-$HOME/.qarunner-native/artifacts}"
export QARUNNER_DB_PATH="${QARUNNER_DB_PATH:-$HOME/.qarunner-native/qarunner.db}"
export QARUNNER_ALLOW_SUBPROCESS_FOR_NON_ADMINS="${QARUNNER_ALLOW_SUBPROCESS_FOR_NON_ADMINS:-true}"
export QARUNNER_COOKIE_SECURE="${QARUNNER_COOKIE_SECURE:-false}"

mkdir -p "$(dirname "$QARUNNER_DB_PATH")" "$QARUNNER_ARTIFACTS_ROOT"

# The subprocess executor inherits this PATH; these must resolve for host suites.
for bin in node npx claude; do
  command -v "$bin" >/dev/null 2>&1 || echo "⚠️  '$bin' not on PATH — host-coupled suites will fail" >&2
done

echo "qarunner (native host runner) → http://127.0.0.1:${PORT:-8001}  tests_root=$QARUNNER_TESTS_ROOT"
exec .venv/bin/uvicorn qarunner.api.app:app --host 127.0.0.1 --port "${PORT:-8001}"
