# qarunner QA Strategy

> **Version:** 1.0
> **Date:** 2026-07-01
> **Owner:** Ike-li
> **Review Cadence:** Quarterly (next review: 2026-10-01)
> **Trigger:** New product area, team change, major incident, defect escape

---

## 1. Executive Summary

qarunner is a self-hosted test execution, scheduling, and regression comparison service. As an internal tool for development teams, it prioritizes reliability and security over feature velocity. This strategy leverages the existing strong foundation (100% backend coverage, established CI pipeline) while addressing gaps in frontend enforcement and expanding test types to match the product's risk profile.

**Key Decisions:**
- Maintain 100% backend coverage (already achieved)
- Enforce frontend unit test coverage
- Expand E2E coverage for critical regression analysis flows
- Defer visual and performance testing (low risk for internal tool)
- Focus on security testing given the sandbox execution model

---

## 2. Scope & Objectives

### In Scope
- **Backend:** FastAPI application, Docker/subprocess runners, SQLite database, authentication, scheduling
- **Frontend:** React dashboard, i18n, SSE streaming, Allure report integration
- **Infrastructure:** GitHub Actions CI/CD, Docker containers
- **Test Types:** Unit, integration, E2E, security (SAST), accessibility (a11y)

### Out of Scope
- **Visual Regression:** Low risk for internal tool; UI changes are intentional, not regressions
- **Performance/Load Testing:** Single-node, self-hosted; performance issues are deployment-specific
- **Third-Party Services:** No external APIs to integrate; self-contained system
- **Mobile Testing:** Web-only application

### Objectives
1. **Maintain 100% backend coverage** through 2026 Q4 (currently achieved)
2. **Achieve 80% frontend unit test coverage** by end of 2026 Q3
3. **Expand E2E coverage to all 7 critical user journeys** by end of 2026 Q3
4. **Reduce flakiness rate to <2%** over 30-day window (currently unknown)
5. **Keep CI pipeline under 10 minutes** (currently ~5 minutes)

---

## 3. Test Levels & Types

| Level | What It Validates | Owner | Framework | Target Count | Run Frequency |
|-------|-------------------|-------|-----------|-------------|---------------|
| **Unit (Backend)** | Functions, business logic, edge cases, pure functions | Developer | pytest + pytest-asyncio | ~53 test files (current) | Every commit |
| **Unit (Frontend)** | React components, hooks, utilities | Developer | Vitest | ~20 test files (target) | Every commit |
| **Integration (Backend)** | API endpoints, database queries, service interactions | Developer | pytest + httpx | ~15 test files (current) | Every PR |
| **E2E (Frontend)** | Critical user journeys through full stack | Developer | Playwright | 7 spec files (target) | Pre-deploy + nightly |
| **Security** | OWASP Top 10, dependency vulnerabilities, auth flows | Developer | Ruff (SAST) + manual review | Per release | Every PR + pre-release |
| **Accessibility** | WCAG 2.2 AA, keyboard navigation, screen reader | Developer | axe-core (manual) | Key flows | Every PR |

**Rationale:**
- **No visual regression:** Internal tool; UI changes are intentional features, not regressions
- **No performance testing:** Single-node deployment; performance is deployment-specific, not code-level
- **No contract testing:** No inter-service communication; monolithic architecture
- **Manual a11y:** Solo developer; automated a11y tools catch only 30% of issues; manual testing more valuable

---

## 4. Test Pyramid Analysis

### Current State

> Counts refreshed from an actual repo scan on 2026-07-10 (backend: 39 test files / 741 tests; frontend unit: 28 test files / 118 tests; E2E: 38 spec files — up from 11 as AI failure diagnosis and other features shipped with full test coverage). Percentages below are file-count shares of the four rows shown and will not sum against unmeasured categories.

| Level | Count | Percentage | Notes |
|-------|-------|------------|-------|
| **Unit (Backend)** | 39 test files | 32% | 100% line + branch coverage |
| **Unit (Frontend)** | 28 test files | 23% | Not enforced |
| **Integration (Backend)** | ~15 test files | 12% | API + database tests |
| **E2E (Frontend)** | 38 spec files | 32% | All critical paths covered |

**Shape:** Unit/integration/E2E mix has shifted since this document was first written, mainly from E2E growth (11→38 files) as new features shipped with matching test coverage — see note above.

**CI Duration:** ~5 minutes (target: <10 minutes) ✅
**Flakiness Rate:** Unknown (flaky detection just implemented)
**Pass Rate:** 100% (enforced by CI gate)

### Target State (End of 2026 Q3)

| Level | Count | Percentage | Notes |
|-------|-------|------------|-------|
| **Unit (Backend)** | 39+ test files | 60% | Maintain 100% coverage |
| **Unit (Frontend)** | 20+ test files | 25% | Enforce 80% coverage |
| **Integration (Backend)** | 15+ test files | 12% | Maintain current coverage |
| **E2E (Frontend)** | 7 spec files | 3% | All critical journeys |

**Target Shape:** Healthy pyramid (60% unit, 25% integration, 3% E2E)

**Action Plan:**
1. **Enforce frontend unit coverage** — Add Vitest coverage gate to CI (80% target)
2. **Expand E2E coverage** — Add 2 more spec files for regression analysis and scheduling
3. **Monitor flakiness** — Use new flaky detection to identify and fix flaky tests
4. **Maintain backend coverage** — 100% gate already enforced

---

## 5. Risk Assessment Matrix

| Feature Area | Impact | Likelihood | Score | Testing Approach |
|-------------|--------|------------|-------|-----------------|
| **Test Execution Engine** | 5 - Catastrophic | 4 - Likely | 20 - CRIT | Unit + integration + manual exploratory |
| **Regression Analysis** | 5 - Catastrophic | 3 - Possible | 15 - CRIT | Unit + integration + E2E |
| **Security & Isolation** | 5 - Catastrophic | 2 - Unlikely | 10 - HIGH | Unit + SAST + manual security review |
| **Authentication & Authorization** | 5 - Catastrophic | 2 - Unlikely | 10 - HIGH | Unit + integration + security scan |
| **Scheduling System** | 4 - Major | 3 - Possible | 12 - HIGH | Unit + integration + E2E |
| **Frontend UI** | 3 - Moderate | 3 - Possible | 9 - MED | Unit + E2E (critical paths) |
| **Git Integration** | 3 - Moderate | 2 - Unlikely | 6 - MED | Unit + integration |
| **Allure Reporting** | 2 - Minor | 2 - Unlikely | 4 - LOW | Unit only |

**Risk-Based Allocation:**
- **CRITICAL (15-25):** Full automation + manual exploratory — Test Execution Engine, Regression Analysis
- **HIGH (10-14):** Full automation — Security & Isolation, Authentication & Authorization, Scheduling System
- **MEDIUM (5-9):** Automation for happy path — Frontend UI, Git Integration
- **LOW (1-4):** Unit tests only — Allure Reporting

---

## 6. Environment Strategy

| Environment | Purpose | Test Types | Data | Deploy Trigger |
|------------|---------|------------|------|---------------|
| **Local** | Developer feedback | Unit, integration | Mocked/seeded | On save |
| **CI** | Automated validation | Unit, integration, lint, SAST | Ephemeral | On push/PR |
| **Production** | Monitoring & smoke | Smoke tests (manual) | Live | On deploy |

**Environment Parity:**
- **High parity:** Same SQLite, same Docker execution model
- **Differences:** CI uses fresh DB, production has persistent data
- **Mocking:** No external APIs to mock (self-contained system)

**Test Data Management:**
- **Backend:** In-memory SQLite for unit tests, file-based for integration
- **Fixtures:** pytest fixtures in `tests/conftest.py`
- **Fakes:** Dedicated `tests/fakes/` directory for test doubles
- **Isolation:** Each test gets fresh database state

---

## 7. Tool Selection Rationale

| Criteria (weight) | pytest | Vitest | Playwright | Ruff |
|-------------------|--------|--------|------------|------|
| **Fits tech stack** (25%) | 5 | 5 | 5 | 5 |
| **Team familiarity** (20%) | 5 | 4 | 4 | 5 |
| **Community & docs** (15%) | 5 | 5 | 5 | 5 |
| **CI integration** (15%) | 5 | 5 | 5 | 5 |
| **Maintenance cost** (10%) | 5 | 5 | 4 | 5 |
| **Speed of execution** (10%) | 5 | 5 | 4 | 5 |
| **License cost** (5%) | 5 | 5 | 5 | 5 |
| **Weighted total** | 5.0 | 4.8 | 4.6 | 5.0 |

**Selections:**
- **Backend Unit/Integration:** pytest — Already in use, 100% coverage, excellent async support
- **Frontend Unit:** Vitest — Fast, Vite-native, TypeScript-first
- **Frontend E2E:** Playwright — Already in use, excellent debugging, multi-browser support
- **Linting/SAST:** Ruff — Fast, Python-specific, replaces flake8 + isort + black

**Why Not Others:**
- **Jest:** Vitest is faster for Vite projects, better TypeScript support
- **Cypress:** Playwright is faster, better debugging, multi-browser support
- **Schemathesis:** No external APIs to test; internal REST API only
- **axe-core (automated):** Solo developer; manual a11y testing more valuable

---

## 8. CI Scaling Levers

**Current State:** ~5 minutes CI time, single worker

**If CI time exceeds 10 minutes:**
1. **Sharding** — Split pytest across 2-4 workers (pytest-xdist)
2. **Caching** — Cache `.venv`, `node_modules`, Playwright browsers
3. **Selective E2E** — Run smoke E2E on PRs, full E2E on merge/nightly
4. **Test impact analysis** — Run only tests affected by diff (requires dependency graph)

**Parallel Efficiency Target:** >3x on 4 shards (if implemented)

**CI-Minutes-per-PR Target:** <10 minutes (current: ~5 minutes)

**Monitoring:**
- Track CI duration weekly
- Alert if CI exceeds 10 minutes
- Review parallel efficiency monthly (if sharding implemented)

---

## 9. Entry/Exit Criteria

### Unit Tests
**Entry:**
- Code compiles
- Function has documented contract (inputs/outputs)
- No external dependencies (mocked/stubbed)

**Exit:**
- All branches covered
- Edge cases tested
- No skipped tests
- 100% coverage (backend), 80% coverage (frontend)

### Integration Tests
**Entry:**
- Unit tests pass
- Database available (in-memory or file-based)
- Test data seeded

**Exit:**
- All API endpoints tested
- Error paths validated
- No flaky tests

### E2E Tests
**Entry:**
- Integration tests pass
- Frontend deployed (local or CI)
- Test accounts provisioned

**Exit:**
- All critical user journeys pass
- No P0/P1 defects open
- Performance within SLA (N/A for internal tool)

### Release
**Entry:**
- All test levels pass
- No CRITICAL/HIGH defects open
- Release notes drafted

**Exit:**
- Smoke tests pass in production
- Monitoring shows no anomalies for 30 minutes
- Rollback plan verified

---

## 10. Quality Gates & Definition of Done

### PR Gate (Every PR)
- ✅ Unit tests pass (backend + frontend)
- ✅ Integration tests pass
- ✅ Coverage does not decrease (backend: 100%, frontend: 80%)
- ✅ Ruff lint passes (zero errors)
- ✅ No new high/critical SAST findings
- ✅ At least one reviewer approval (self-review for solo developer)

### Merge Gate (Merge to Main)
- ✅ All PR-gate checks pass
- ✅ E2E smoke suite passes
- ✅ No merge conflicts
- ✅ Branch up to date with main

### Deploy Gate (Before Production)
- ✅ Full E2E suite passes on staging (N/A — single environment)
- ✅ Security scan passes
- ✅ Rollback plan documented and tested

### Nightly Gate (Scheduled)
- ✅ Full E2E including edge cases
- ✅ Accessibility scan (manual)
- ✅ Dependency vulnerability scan
- ✅ Results reviewed next morning

**Enforcement:**
- All gates enforced in CI (GitHub Actions)
- No manual overrides (except emergency hotfixes)
- Failed gates block merge/deploy

---

## 11. Metrics & KPIs

| Metric | Definition | Target | Cadence |
|--------|-----------|--------|---------|
| **Code Coverage (Backend)** | Lines/branches covered by unit + integration | 100% | Per PR |
| **Code Coverage (Frontend)** | Lines/branches covered by unit tests | >80% | Per PR |
| **Test Pyramid Ratio** | Unit:Integration:E2E split | 85:12:3 (±5% tolerance) | Monthly |
| **Flakiness Rate** | % of runs with non-deterministic failures | <2% | Weekly |
| **Defect Escape Rate** | % of defects found in prod vs. total | <5% | Per release |
| **MTTR** | Detection to fix deployed | <4h P0, <24h P1 | Per incident |
| **CI Pipeline Duration** | Push to green/red signal | <10 min PR, <15 min full | Weekly |
| **E2E Coverage** | % of critical user journeys with E2E tests | 100% (7/7) | Quarterly |
| **Automation Rate** | % of test cases automated | >90% for regression suite | Quarterly |

**Using Metrics:**
- Track trends over time, not absolute numbers
- Set realistic targets from current state
- Review quarterly with self (solo developer)
- Never use metrics to punish — use them to improve
- Investigate spikes — a sudden flakiness jump signals infrastructure issues

---

## 12. Timeline & Milestones

### Phase 1 — Foundation (Weeks 1-4) ✅ COMPLETE
- ✅ Risk assessment for all product areas
- ✅ CI pipeline with unit-test gate (100% backend coverage)
- ✅ Baseline metrics (coverage, pipeline time)
- ✅ Unit tests for all backend areas
- ✅ E2E framework configured (Playwright)

**Exit:** CI runs unit tests on every PR, baseline metrics documented.

### Phase 2 — Coverage Expansion (Weeks 5-10) 🔄 IN PROGRESS
- ✅ Integration tests for all API endpoints
- ✅ E2E for 5 critical user journeys
- 🔄 Frontend unit test enforcement (80% coverage target)
- ✅ E2E for remaining 2 critical journeys (regression analysis, scheduling) — see `journey-regression-loop`, `run-diff`, `suite-trend`, `journey-schedule-heartbeat`, `schedule-mgmt` specs
- ✅ Test data management (fixtures, fakes)

**Exit:** All critical paths have E2E coverage, integration tests cover all APIs.

### Phase 3 — Quality Gates (Weeks 11-14) 📋 PLANNED
- 📋 Coverage gates on PRs (frontend: 80%)
- 📋 Security scanning (Ruff SAST)
- 📋 Monitoring dashboards for all KPIs
- 📋 Nightly E2E runs

**Exit:** All four gates (PR, merge, deploy, nightly) active and enforced.

### Phase 4 — Optimization (Weeks 15-20) 📋 PLANNED
- 📋 Fix or quarantine flaky tests
- 📋 CI scaling levers (if needed)
- 📋 First quarterly strategy review

**Exit:** CI under 10 min, flakiness under 2%, first strategy revision published.

**Ongoing:**
- Quarterly strategy review and revision
- Monthly metrics review
- Continuous maintenance (refactor, de-flake, retire)

---

## 13. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-07-01 | Ike-li | Initial strategy document |

---

## Appendices

### A. Critical User Journeys (Full List)
1. **Suite Management:** Link local directory, clone Git repository, prepare dependencies
2. **Test Execution:** Create profile, trigger run, monitor SSE logs, view Allure report
3. **Regression Analysis:** View baseline diff, see pass-rate trends, identify flaky tests, examine case history
4. **Scheduling:** Create cron schedule, preview next runs, manually trigger, manage schedules
5. **User Management:** Create users, assign roles, manage passwords, enforce owner-scope
6. **Dashboard Monitoring:** View stat cards, filter runs, search
7. **Security & Isolation:** Execute in hardened Docker containers, prevent malicious code execution

### B. Risk Matrix (5x5)

| Impact \ Likelihood | 1 - Rare | 2 - Unlikely | 3 - Possible | 4 - Likely | 5 - Almost Certain |
|---------------------|----------|--------------|--------------|------------|-------------------|
| **5 - Catastrophic** | 5 - MED | 10 - HIGH | 15 - CRIT | 20 - CRIT | 25 - CRIT |
| **4 - Major** | 4 - LOW | 8 - MED | 12 - HIGH | 16 - CRIT | 20 - CRIT |
| **3 - Moderate** | 3 - LOW | 6 - MED | 9 - MED | 12 - HIGH | 15 - CRIT |
| **2 - Minor** | 2 - LOW | 4 - LOW | 6 - MED | 8 - MED | 10 - HIGH |
| **1 - Negligible** | 1 - LOW | 2 - LOW | 3 - LOW | 4 - LOW | 5 - MED |

### C. Tool Versions
- **Python:** 3.12
- **FastAPI:** 0.115
- **pytest:** Latest (via uv)
- **React:** 18.3
- **TypeScript:** 5.2
- **Vite:** 5.3
- **Vitest:** 4.1
- **Playwright:** 1.45
- **Ruff:** Latest (via uv)