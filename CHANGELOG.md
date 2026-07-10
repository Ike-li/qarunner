# Changelog

## [0.3.0] — 2026-07-10

### Added — AI failure diagnosis
- Read-only root-cause analysis for failed runs: aggregates failed cases, log tail, baseline diff, pass-rate trend, and per-case flaky history into an LLM prompt
- Produces a structured diagnosis — one of 6 root-cause categories, confidence (HIGH/MED/LOW), evidence list, regression flag, suggested next steps
- Provider-neutral (`anthropic`/`openai`, official SDKs); feature auto-disables (no startup failure) when no API key is configured
- Diagnosis cached per run (`GET`/`POST /runs/{id}/ai-analysis`); owner-scoped like diff/trend/case-history
- New "AI Analysis" tab in the run-details drawer (4th tab alongside logs/report/diff)

### Added — Cross-run comparison (epic complete)
- Baseline diff (`GET /runs/{id}/diff`): compares a run against the latest `COMPLETED` run in the same scope (suite + runner + params), bucketed into newly-failed / fixed / still-failing / new / disappeared cases
- Pass-rate trend (`GET /runs/trend`): per-suite trend line rendered as a dependency-free SVG sparkline
- Flaky detection: per-case pass↔fail flip tracking over recent runs, surfaced as a badge
- Per-case history (`GET /cases/history`): lazy-loaded cross-run result strip for any case in a diff
- All three endpoints owner-scoped (non-admin sees only their own runs); diff/trend/flaky logic implemented as dependency-free pure functions in `core/`

### Security
- Closed a broad set of IDOR gaps: suite access is now required and owner/shared-scope checked for profile creation/update, run creation, suite listing/scanning, and related artifact/log/report path handling
- Fixed 23 bugs from a follow-up code review, spanning: username-enumeration timing side-channel, JWT invalidation on password change/logout, atomic admin-demotion race, minimum password/secret-key length, X-Forwarded-For support, webhook SSRF protection, cascade-delete on user removal, several run-state race conditions, and multiple frontend double-submit guards

## [0.2.0] — 2026-06-26

### Security
- SEC-1: RCE argv-injection hardened — `extra_args`/`selected_files` whitelist validated against dangerous pytest/playwright flags
- SEC-2: JWT secret and admin password now required (no defaults); known placeholders rejected at startup
- SEC-3: Docker executor hardened (non-root, `network_mode=none`, `cap_drop=ALL`, `read_only`, pid/mem/cpu limits); subprocess executor uses env allowlist + RLIMIT
- SEC-4: Object-level authorization (IDOR) on runs, profiles, and schedules
- SEC-5: Login brute-force protection with exponential backoff lockout
- SEC-6: Auth token moved from URL query param to HttpOnly SameSite=Strict cookie
- SEC-7: junit XML parsed with `defusedxml` to block XXE/entity-expansion
- SEC-8: Log tail truncated to prevent memory exhaustion from large outputs

### Data & Concurrency
- DATA-1: SQLite per-operation connections (eliminates concurrent dirty-reads)
- DATA-2: Background task strong references (prevents GC silent cancellation)
- DATA-3: Crash recovery — interrupted runs marked `FAILED` on restart
- DATA-4: Graceful shutdown with drain timeout for in-flight executions
- FUNC-1: `env` field now actually propagated to child processes (was dead feature)
- CONC-1: SSE streaming with disconnect detection, bounded tail, backoff reconnect
- CONC-2: Cron leader election via conditional DB update for multi-replica safety

### Frontend
- App.tsx refactored from 1541 → 83 lines
- 8 domain hooks extracted: useApi, useAuth, useRuns, useSchedules, useProfiles, useSuites, useUsers, useTriggerForm
- DashboardContext centralizes all shared state; 11 components migrated to zero-props
- All `any` types eliminated; Semi UI TagColor union used
- Playwright E2E tests: 10 tests across 3 spec files (ui, suites, run-lifecycle)
- A11y: dialog traps, keyboard navigation, data-testid selectors

### Architecture
- ARCH-1: Schedule port — `core/` zero FastAPI imports
- ARCH-2/3: Service layer (ScheduleService, ProfileService) + unified cron logic
- ARCH-4: Settings injected via DI container
- ARCH-5: Store port composed from RunStore/UserStore/ProfileStore/ScheduleStore/SuiteStore
- ARCH-8: Schema migration versioned via `PRAGMA user_version`

### Deployment
- DEP-1: Docker images run as non-root (`USER app` / `USER runner`)
- DEP-2: Platform image builds with `uv sync --frozen` against lockfile
- DEP-3: `.dockerignore` excludes venv, artifacts, secrets
- DEP-4: `/health` readiness probe + compose healthcheck + resource limits
- DEP-5: Executor Docker image pinned; production disables runtime auto-build
- DEP-6: README production deployment section with same-origin and security notes
- Pre-commit hook with ruff + pytest gate

## [0.1.0] — 2026-06-16

- Initial release: FastAPI backend with hexagonal architecture
- React frontend with Semi UI component library
- pytest/allure test execution via subprocess or Docker
- SQLite persistence with WAL mode
- JWT authentication with bcrypt password hashing
- Test profiles, cron schedules, suite discovery
