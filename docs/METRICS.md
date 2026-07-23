# Quality Metrics

> Pass rate, flaky count, test duration and coverage describe the tested suites or
> engineering process; they are not qarunner product-success KPIs. Product success
> criteria live in docs/REQUIREMENTS.md and focus on provenance completeness,
> regression correctness, security boundaries, core journeys and signal consumption.
> Release denominators, evidence freshness and sign-off rules live in
> docs/REQUIREMENTS_TRACEABILITY.md; frozen family/case identities and applicability live in
> docs/RELEASE_GATE_CATALOG.md.

> Defines what we measure, why, and what action each metric triggers.
> See also: `docs/FEATURES.md` §5 (Regression View), `.agents/qa-project-context.md`.

## Metric Summary

| # | Metric | Formula | Target | Trigger |
|---|--------|---------|--------|---------|
| 1 | Pass Rate (7-day) | green runs / total runs × 100 | ≥95% | <90% → investigate failing suites |
| 2 | Flaky Test Count | tests with ≥4 observations and ≥3 pass/fail flips | ≤3 | >5 → quarantine top flaky tests |
| 3 | Code Coverage | lines executed / total lines × 100 | ≥99% backend | <100% → block PR merge |
| 4 | Avg Duration (7-day) | mean(finished_at − started_at) | <5 min | >10 min → profile slow tests |
| 5 | Run Volume (7-day) | count of completed runs | baseline | >50% drop → check scheduler health |
| 6 | Weekly Active Triggerers | distinct `runs.created_by` (7-day) | observe — no target yet | establish baseline before setting one |
| 7 | Fix-Attempt Rate (24h) | failed runs re-run in same scope within 24h / failed runs | observe — no target yet | establish baseline before setting one |

## 1. Pass Rate (7-Day Rolling)

**Formula:** `(completed runs with pass_rate=1.0 / total completed runs) × 100` over the last 7 days.

**Target:** ≥95% (startup stage, per qa-metrics company-stage table).

**Why:** The single most visible quality signal. A dropping pass rate means tests are catching real regressions — or the suite itself is broken.

**When it goes red (<90%):**
1. Check if the drop is from new test failures (real regressions) or infrastructure issues (Docker, timeouts).
2. If real regressions: identify the failing suites via the per-suite breakdown.
3. If infrastructure: check executor health and timeout settings.

**Data source:** `runs` table, `status='completed'`, `summary_json.pass_rate`.

---

## 2. Flaky Test Count

**Formula:** Count of unique test cases (suite + name) across all runs in the last 30 days where `flakiness(statuses).is_flaky == True` (≥4 observations and ≥3 pass/fail transitions, per `FlakyPolicy` defaults).

**Target:** ≤3 flaky tests.

**Why:** Flaky tests erode trust in the suite. Once developers think "probably just flaky," they stop paying attention to failures.

**When it goes red (>5):**
1. Identify the top flaky tests via `GET /cases/history`.
2. Quarantine them (tag `@flaky`, move to non-blocking CI job).
3. Investigate root cause: timing, shared state, external dependencies.
4. Tests flaky for 30+ days should be deleted or rewritten.

**Data source:** `run_test_cases` table, `flaky.flakiness()` function.

---

## 3. Code Coverage

**Formula:** Lines executed by tests / total executable lines × 100 (backend: pytest-cov, frontend: vitest).

**Target:** Backend ≥99% (enforced at 100% with known pragma:no-cover exceptions). Frontend: no gate yet.

**Why:** Coverage identifies blind spots — code never exercised by tests is where bugs hide.

**When it goes red:**
- Backend: block PR merge (already enforced by `--cov-fail-under=100`).
- Frontend: track trend, set gate when coverage reaches a stable baseline.

**Data source:** CI workflow artifacts (coverage report).

---

## 4. Average Run Duration (7-Day)

**Formula:** `mean(finished_at − started_at)` for completed runs in the last 7 days.

**Target:** <5 minutes average.

**Why:** Slow runs break the feedback loop. If average duration creeps up, developers context-switch while waiting.

**When it goes red (>10 min):**
1. Check the slowest runs (p95 duration).
2. Profile test execution — are specific suites taking longer?
3. Check if Docker image pulls or npm installs are adding overhead.
4. Consider sharding or parallelization.

**Data source:** `runs` table, `started_at`, `finished_at`.

---

## 5. Run Volume (7-Day)

**Formula:** Count of completed runs in the last 7 days.

**Target:** Baseline-dependent (establish after 2 weeks of data).

**Why:** A sudden drop means the scheduler is broken or nobody is using the system. A spike might mean a debugging loop.

**When it goes red (>50% drop from baseline):**
1. Check scheduler health (`test_schedules` table, `enabled` flag).
2. Check if the cron poller is running.
3. Verify no API errors preventing run creation.

**Data source:** `runs` table, `status='completed'`, `created_at`.

---

## 6. Weekly Active Triggerers (adoption — observation phase)

**Formula:** Count of distinct `runs.created_by` among runs created in the last 7 days.

**Target:** None yet — observation phase. Set a target only after the team has actually adopted the tool and a baseline exists (REQUIREMENTS GO-8).

**Why:** An internal tool's first-principles success is that people keep consuming its signal. Zero triggerers means the regression signal — however accurate — produces no value.

**Data source:** `runs` table, `created_by`, `created_at`. Zero instrumentation required.

---

## 7. Fix-Attempt Rate (trust — observation phase)

**Formula:** Among failed runs in the window, the share that were followed by another run in the **same scope** (per REQUIREMENTS §3.3) within 24 hours.

**Target:** None yet — observation phase (REQUIREMENTS GO-8).

**Why:** A proxy for "failures get acted on". If failures are never re-run, either the signal is not trusted (flaky noise) or nobody is watching — both are death spirals for a regression suite.

**Data source:** `runs` table (`status`, `profile_id`, `tests_path`, `runner`, `args`, `created_at`). Pure query, zero instrumentation.

---

## Implementation

- **Backend:** `GET /metrics` endpoint computes all metrics server-side from the SQLite database.
- **Frontend:** `MetricsView` component displays the metrics with trend indicators.
- **CI:** Coverage is enforced in `.github/workflows/ci.yml` (pytest `--cov-fail-under=100`).
