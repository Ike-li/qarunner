# qarunner QA Strategy

> [!IMPORTANT]
> V7.4 新增控制面到专用 Worker 的内部服务契约。后续 QA 基线必须增加 Worker 身份、claim/fencing、断线恢复、一次性容器和证据上传的契约/故障测试；本文现有单宿主描述属于当前实现。
> 发布验收 family、case ID、适用性和样本套件候选见 [`RELEASE_GATE_CATALOG.md`](RELEASE_GATE_CATALOG.md)。

> **Version:** 1.1
> **Date:** 2026-07-12
> **Owner:** Ike-li
> **Review Cadence:** Quarterly (next review: 2026-10-01)
> **Trigger:** New product area, team change, major incident, defect escape

---

## 1. Executive Summary

qarunner is a self-hosted intranet test execution, scheduling, and regression comparison service for one team. V7.4 targets one control plane and one dedicated hardened Worker; the current repository remains a legacy single-host implementation until GAP-021/SOR-GAP-023 close. This strategy therefore tests both the current code and the target Worker contracts, with fail-closed behavior as the security baseline.

**Key Decisions:**
- Maintain 100% backend coverage (already achieved)
- Enforce frontend unit test coverage
- Expand E2E coverage for critical regression analysis flows
- Defer visual and performance testing (low risk for internal tool)
- Focus on security testing given the sandbox execution model

---

## 2. Scope & Objectives

### In Scope
- **Backend:** FastAPI control plane, SQLite database, authentication, scheduling, and the authenticated Worker task contract (current Docker/subprocess runners are legacy implementation paths)
- **Frontend:** React dashboard, i18n, SSE streaming, Allure report integration
- **Infrastructure:** GitHub Actions CI/CD, dedicated Worker host baseline, Docker containers, and evidence upload path
- **Test Types:** Unit, integration, E2E, Worker contract/fault, isolation/capacity, security (SAST), accessibility (a11y)

### Out of Scope
- **Visual Regression:** Low risk for internal tool; UI changes are intentional, not regressions
- **Performance/Load Testing:** Internet-scale load testing is out of scope; fixed single-control-plane/single-Worker capacity and resource-boundary tests are in scope
- **Third-Party Services:** Provider-specific certification beyond approved Git/Registry/SUT/AI/notification contracts
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
| **Worker Contract** | Identity, claim/fencing, heartbeat, drain, upload and replay rejection | Developer/QA | Contract + fault tests | Frozen catalog | Every release |
| **Isolation/Capacity** | Fresh containers, residue cleanup, Worker loss, queue and disk limits | QA/Ops | Docker integration + recovery drill | Frozen catalog | Pre-release |

**Rationale:**
- **No visual regression:** Internal tool; UI changes are intentional features, not regressions
- **Capacity is bounded, not absent:** the supported single-control-plane/single-Worker envelope must be measured and published
- **Worker contract testing is mandatory:** control plane and Worker communicate across an authenticated service boundary
- **Manual a11y:** Solo developer; automated a11y tools catch only 30% of issues; manual testing more valuable

---

## 4. Test Pyramid Analysis

### Current State

> Inventory refreshed on 2026-07-12. Backend counts come from containerized `pytest --collect-only`; frontend counts are static declarations. They do not prove that the tests passed.

| Level | Current inventory | Gate status | Notes |
|-------|-------------------|-------------|-------|
| **Backend default** | 36 unit files / 842 collected cases | CI gate | 100% line + branch coverage; excludes `e2e`/`docker` |
| **Backend Docker integration** | 2 files / 7 collected cases | Opt-in only | Legacy local Docker evidence; not in current CI |
| **Backend E2E** | 2 files / 2 collected cases | Opt-in only | Uses legacy SubprocessRunner/JUnit/Allure path |
| **Frontend unit** | 32 files / 168 declared cases | No coverage gate | No stable release case manifest or machine-readable result output |
| **Frontend Playwright** | 38 specs / 289 declared cases | Manual layers | Contains legacy duplicates, fixed skip and conditional skip |

**Shape:** Raw file count is not the release denominator. The authoritative denominator is the enabled and applicable case set in `RELEASE_GATE_CATALOG.md`.

**CI Duration:** Target <10 minutes; current value was not re-measured in this inventory.
**Flakiness Rate:** Unknown (flaky detection just implemented)
**Pass Rate:** Not asserted by this inventory; only the backend default command has a configured coverage/pass gate.

### Target State (End of 2026 Q3)

| Layer | Target outcome | Release rule |
|-------|----------------|--------------|
| **Backend default** | Maintain 100% line + branch coverage | Every candidate commit |
| **Worker contract/fault** | Implement all applicable `RCF-003/004/008/018/022/023` cases | No skipped/not-run cases |
| **Worker isolation/security** | Implement all applicable `RCF-002/005～007/019～021/024` cases | Dedicated Worker environment |
| **Frontend unit** | Establish machine-readable results and an approved coverage threshold | Threshold breach blocks release |
| **Frontend E2E** | Stable case IDs for core/auth/runner/a11y; remove weak returns and unapproved skips | Frozen catalog denominator |

**Target Shape:** Risk-based layers with explicit contracts and evidence; test-file percentages are not a quality objective.

**Action Plan:**
1. **Approve the release catalog** — Freeze RCF family/case IDs, applicability and sample contracts
2. **Implement Worker gates** — Add protocol, isolation, approved-target and recovery harnesses
3. **Remove false-green paths** — Fix weak returns/assertions and replace unapproved skip with preflight failure or approved applicability
4. **Produce machine-readable frontend evidence** — Add stable result output and an approved coverage threshold
5. **Maintain backend coverage** — Keep the existing 100% default gate while adding opt-in suites to release automation

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
| **V7.4 target production** | Monitoring, smoke, recovery and security boundary | Smoke, Worker contract, recovery | Approved non-production SUT | On deploy |

**Environment Parity:**
- **High parity:** Same state/provenance contracts and Worker task protocol
- **Differences:** CI uses fresh DB and test doubles; target production has persistent control-plane data and a dedicated Worker host
- **Mocking:** Git, Registry, SUT, AI and notification boundaries use contract fakes or approved test endpoints; Worker protocol also requires negative/replay cases

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
7. **Security & Isolation:** Execute untrusted stages in fresh Worker containers, enforce network/identity/resource/artifact boundaries, and verify the accepted shared-kernel residual risk

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
