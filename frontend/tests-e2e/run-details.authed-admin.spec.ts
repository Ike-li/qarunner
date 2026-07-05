import { test, expect } from '@playwright/test';

// Run Details Drawer E2E tests — validates the SideSheet drawer that slides in
// when a run row is clicked, including the Logs / Report / Diff tabs, fullscreen
// overlays, action buttons (re-run / cancel / delete), and placeholder states.
// All backend routes are mocked to ensure deterministic, isolated testing.
// Runs under `chromium-authed-admin` so it uses storageState instead of UI login.

// ── Mock data ──────────────────────────────────────────────────────────────

const RUN_WITH_LOGS_AND_REPORT = {
  id: 'run-full-0001',
  status: 'completed',
  runner: 'pytest',
  created_by: 'admin',
  tests_path: 'suite_integration/',
  args: ['--verbose', '--tb=short'],
  executor_mode: 'docker',
  summary: { total: 10, passed: 8, failed: 1, skipped: 1, error: 0, duration_ms: 5000, pass_rate: 0.8 },
  report: {
    allure_results_dir: '/artifacts/run-full-0001/results',
    allure_report_file: '/artifacts/run-full-0001/results/allure-report/index.html',
    html_generated: true,
  },
  exit_code: 1,
  error: 'FAILED suite_integration/test_auth.py::test_login - AssertionError: expected 200 got 401',
  passed: false,
  created_at: '2026-07-03T10:00:00Z',
  started_at: '2026-07-03T10:00:01Z',
  finished_at: '2026-07-03T10:00:06Z',
  stdout: `============================= test session starts ==============================
platform linux -- Python 3.12.8, pytest-8.3.4, pluggy-1.5.0
rootdir: /workspace/suite_integration
collecting ... collected 10 items

suite_integration/test_auth.py::test_login FAILED
suite_integration/test_auth.py::test_logout PASSED
suite_integration/test_api.py::test_create PASSED
suite_integration/test_api.py::test_update PASSED
suite_integration/test_api.py::test_delete PASSED
suite_integration/test_db.py::test_query PASSED
suite_integration/test_db.py::test_migration PASSED
suite_integration/test_db.py::test_rollback PASSED
suite_integration/test_ui.py::test_render PASSED
suite_integration/test_ui.py::test_interaction FAILED

----------- generated xml report: /workspace/reports/results.xml ------------
/workspace/suite_integration/conftest.py:42: UserWarning: Some fixture is deprecated
========================= 2 failed, 8 passed in 5.00s =========================`,
  stderr: null,
  cases: [
    {
      suite: 'suite_integration/test_auth.py',
      name: 'test_login',
      status: 'failed',
      duration_ms: 1200,
      message: 'AssertionError: expected 200 got 401',
    },
  ],
  locked: false,
};

const RUN_NO_LOGS = {
  ...RUN_WITH_LOGS_AND_REPORT,
  id: 'run-no-logs-0002',
  report: null,
  stdout: null,
  stderr: null,
  cases: [],
  error: null,
  summary: { total: 10, passed: 10, failed: 0, skipped: 0, error: 0, duration_ms: 1000, pass_rate: 1.0 },
  passed: true,
  exit_code: 0,
};

const RUN_RUNNING = {
  ...RUN_WITH_LOGS_AND_REPORT,
  id: 'run-running-0003',
  status: 'running',
  summary: null,
  report: null,
  finished_at: null,
  exit_code: null,
  cases: [],
  locked: true,
};

const DIFF_WITH_BASELINE = {
  baseline: { id: 'baseline-run-abcdef12', created_at: '2026-06-01T12:00:00Z', status: 'completed' },
  diff: {
    new_failures: [
      { suite: 'auth', name: 'test_login', status: 'failed', duration_ms: 12, message: 'AssertionError: 401 != 200' },
    ],
    still_failing: [
      { suite: 'pay', name: 'test_charge', status: 'failed', duration_ms: 5, message: null },
    ],
    fixed: [
      { suite: 'auth', name: 'test_logout', status: 'passed', duration_ms: 3, message: null },
    ],
    new_cases: [
      { suite: 'new', name: 'test_added', status: 'passed', duration_ms: 1, message: null },
    ],
    removed_cases: [
      { suite: 'old', name: 'test_gone', status: 'passed', duration_ms: 1, message: null },
    ],
  },
};

const DIFF_NO_BASELINE = {
  baseline: null,
  diff: { new_failures: [], still_failing: [], fixed: [], new_cases: [], removed_cases: [] },
};

const FLAKY_HISTORY = {
  points: [
    { created_at: '2026-06-01T10:00:00Z', status: 'passed' },
    { created_at: '2026-06-02T10:00:00Z', status: 'failed' },
    { created_at: '2026-06-03T10:00:00Z', status: 'passed' },
    { created_at: '2026-06-04T10:00:00Z', status: 'failed' },
  ],
  flaky: true,
  flip_count: 3,
};

const RUN_ARTIFACTS = {
  artifacts: [
    { path: 'failed-case/trace.zip', size_bytes: 1024 },
    { path: 'failed-case/screenshot.png', size_bytes: 2048 },
  ],
};

// ── Helpers ────────────────────────────────────────────────────────────────

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

async function mockRunsRoute(page: import('@playwright/test').Page, runsArray: unknown[]) {
  await page.route('**/runs', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: runsArray }) });
  });
  // Mock /suites and /tests to avoid backend dependency for sidebar / trend calls.
  await page.route('**/suites', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route('**/tests', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(['suite_integration/']) });
  });
}

function shallowRunForList<T extends object>(run: T) {
  return { ...run, stdout: null, stderr: null, cases: [] };
}

async function mockRunDetailRoute(page: import('@playwright/test').Page, runId: string, runData: unknown) {
  await page.route(`**/runs/${runId}`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runData) });
    } else {
      await route.fallback();
    }
  });
}

async function mockRunArtifactsRoute(page: import('@playwright/test').Page, runId: string) {
  await page.route(`**/runs/${runId}/artifacts`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(RUN_ARTIFACTS),
    });
  });
}

async function openDrawer(page: import('@playwright/test').Page) {
  // Click the first row in the runs table to open the drawer.
  await expect(page.getByTestId('execution-records-title')).toBeVisible({ timeout: 8000 });
  const firstRow = page.locator('table tbody tr').first();
  await expect(firstRow).toBeVisible({ timeout: 5000 });
  await firstRow.click();
  // Wait for the SideSheet dialog to appear.
  await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 5000 });
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('Run Details Drawer', () => {
  test.beforeEach(async ({ page }) => {
    // Default mock: /runs is a shallow list; /runs/{id} carries logs + case results.
    await mockRunsRoute(page, [shallowRunForList(RUN_WITH_LOGS_AND_REPORT)]);
    await mockRunDetailRoute(page, RUN_WITH_LOGS_AND_REPORT.id, RUN_WITH_LOGS_AND_REPORT);
    await openDashboard(page);
  });

  // 1. Clicking a run row opens the details drawer
  test('Clicking a run row opens the details drawer', async ({ page }) => {
    await openDrawer(page);

    // Verify the drawer shows a status badge (COMPLETED / FAILED / RUNNING / QUEUED / TIMEOUT).
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/COMPLETED|FAILED/);

    // Verify all three drawer tabs are visible.
    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible();
    await expect(page.getByTestId('drawer-tab-report')).toBeVisible();
    await expect(page.getByTestId('drawer-tab-diff')).toBeVisible();
  });

  // 2. Logs tab shows console output when available
  test('Logs tab shows console output when available', async ({ page }) => {
    await openDrawer(page);

    // Logs tab is active by default. Verify console output is visible.
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText('test session starts');
    await expect(dialog).toContainText('2 failed, 8 passed');

    // Verify copy, download, fullscreen toggle buttons are available.
    await expect(page.locator('[role="dialog"] button[title="Copy"]')).toBeVisible();
    await expect(page.locator('[role="dialog"] button[title="Download raw log file"]')).toBeVisible();
    await expect(page.locator('[role="dialog"] button', { hasText: 'Fullscreen' })).toBeVisible();
  });

  test('Run detail load failure shows an error state instead of no logs', async ({ page }) => {
    await page.route(`**/runs/${RUN_WITH_LOGS_AND_REPORT.id}`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"detail failed"}' });
      } else {
        await route.fallback();
      }
    });
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    await openDrawer(page);

    await expect(page.getByTestId('run-details-error')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('run-details-error')).toContainText(/Unable to load|无法加载/);
    await expect(page.locator('[role="dialog"]')).not.toContainText(/No console logs available|无控制台日志/);
  });

  // 3. Report tab shows summary bar and report buttons
  test('Report tab shows summary bar and report buttons', async ({ page }) => {
    await openDrawer(page);

    // Switch to report tab.
    await page.getByTestId('drawer-tab-report').click();

    const dialog = page.locator('[role="dialog"]');
    // Verify summary metrics are visible.
    await expect(dialog).toContainText(/passed.*8|8.*passed/i);
    await expect(dialog).toContainText(/failed.*1|1.*failed/i);
    await expect(dialog).toContainText(/error.*0|0.*error/i);
    await expect(dialog).toContainText(/skipped.*1|1.*skipped/i);

    // Verify report action buttons are present.
    await expect(page.getByTestId('report-fullscreen-button')).toBeVisible();
    await expect(page.getByTestId('report-new-window-link')).toBeVisible();

    // Case results live on GET /runs/{id}, not the shallow GET /runs list.
    await expect(page.getByTestId('run-case-results')).toBeVisible();
    await expect(page.getByTestId('run-case-result-0')).toContainText(
      'suite_integration/test_auth.py::test_login',
    );
  });

  test('Report tab shows downloadable runner artifacts', async ({ page }) => {
    await mockRunArtifactsRoute(page, RUN_WITH_LOGS_AND_REPORT.id);

    await openDrawer(page);
    await page.getByTestId('drawer-tab-report').click();

    await expect(page.getByTestId('run-artifacts')).toBeVisible({ timeout: 5000 });
    const traceLink = page.getByTestId('run-artifact-link-0');
    await expect(traceLink).toContainText('failed-case/trace.zip');
    await expect(traceLink).toContainText('1 KB');
    await expect(traceLink).toHaveAttribute(
      'href',
      '/runs/run-full-0001/artifacts/failed-case%2Ftrace.zip',
    );

    const screenshotLink = page.getByTestId('run-artifact-link-1');
    await expect(screenshotLink).toContainText('failed-case/screenshot.png');
    await expect(screenshotLink).toContainText('2 KB');
  });

  // 4. Diff tab shows cross-run comparison buckets
  test('Diff tab shows cross-run comparison buckets', async ({ page }) => {
    // Override the diff endpoint to return data with a baseline.
    await page.route(`**/runs/${RUN_WITH_LOGS_AND_REPORT.id}/diff`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_WITH_BASELINE) });
    });

    await openDrawer(page);
    await page.getByTestId('drawer-tab-diff').click();

    // Verify diff buckets render.
    await expect(page.getByTestId('diff-bucket-new_failures')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('diff-bucket-still_failing')).toBeVisible();
    await expect(page.getByTestId('diff-bucket-fixed')).toBeVisible();
    await expect(page.getByTestId('diff-bucket-new_cases')).toBeVisible();
    await expect(page.getByTestId('diff-bucket-removed_cases')).toBeVisible();

    // Verify diff-empty-baseline is NOT shown.
    await expect(page.getByTestId('diff-empty-baseline')).toHaveCount(0);
  });

  test('Diff tab shows empty state when no baseline exists', async ({ page }) => {
    // Override the diff endpoint to return no baseline.
    await page.route(`**/runs/${RUN_WITH_LOGS_AND_REPORT.id}/diff`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_NO_BASELINE) });
    });

    await openDrawer(page);
    await page.getByTestId('drawer-tab-diff').click();

    // Verify the empty-baseline placeholder is visible.
    await expect(page.getByTestId('diff-empty-baseline')).toBeVisible({ timeout: 5000 });
    // No buckets should be rendered.
    await expect(page.getByTestId('diff-bucket-new_failures')).toHaveCount(0);
  });

  // 5. Expanding a diff case shows cross-run history
  test('Expanding a diff case shows cross-run history', async ({ page }) => {
    await page.route(`**/runs/${RUN_WITH_LOGS_AND_REPORT.id}/diff`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_WITH_BASELINE) });
    });
    await page.route('**/cases/history*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(FLAKY_HISTORY) });
    });

    await openDrawer(page);
    await page.getByTestId('drawer-tab-diff').click();

    // Wait for buckets to render.
    await expect(page.getByTestId('diff-bucket-new_failures')).toBeVisible({ timeout: 5000 });

    // History is lazy-loaded — collapsed until clicking the toggle.
    await expect(page.getByTestId('case-history')).toHaveCount(0);

    // Click the first case toggle button.
    await page.getByTestId('diff-case-toggle').first().click();

    // Verify case-history appears with colored history dots.
    await expect(page.getByTestId('case-history').first()).toBeVisible({ timeout: 5000 });

    // Verify flaky badge is visible (since FLAKY_HISTORY has flaky: true).
    await expect(page.getByTestId('flaky-badge').first()).toBeVisible();
  });

  test('Diff case history shows an error state when history API fails', async ({ page }) => {
    await page.route(`**/runs/${RUN_WITH_LOGS_AND_REPORT.id}/diff`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_WITH_BASELINE) });
    });
    await page.route('**/cases/history*', async (route) => {
      await route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"history failed"}' });
    });

    await openDrawer(page);
    await page.getByTestId('drawer-tab-diff').click();
    await expect(page.getByTestId('diff-bucket-new_failures')).toBeVisible({ timeout: 5000 });

    await page.getByTestId('diff-case-toggle').first().click();

    await expect(page.getByTestId('case-history-error').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('case-history-error').first()).toContainText(/Unable to load|无法加载/);
    await expect(page.getByTestId('flaky-badge')).toHaveCount(0);
  });

  // 6. Re-run button is visible for completed runs
  test('Re-run button is visible for completed runs', async ({ page }) => {
    await openDrawer(page);

    // For a completed run (not locked), both 'Re-run' and 'Delete Run' buttons should be visible.
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/Re-run/);
    await expect(dialog).toContainText(/Delete Run/);
  });

  test('Delete Run button hidden when run is locked', async ({ page }) => {
    // Create a locked completed run.
    const lockedRun = { ...RUN_WITH_LOGS_AND_REPORT, locked: true };
    await mockRunsRoute(page, [lockedRun]);
    await mockRunDetailRoute(page, lockedRun.id, lockedRun);
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    await openDrawer(page);

    // 'Re-run' should still be visible, but 'Delete Run' should be hidden.
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/Re-run/);
    await expect(dialog).not.toContainText(/Delete Run/);
  });

  // 7. Cancel button is visible for running/queued runs
  test('Cancel button is visible for running/queued runs', async ({ page }) => {
    // Replace mock data with a running run.
    await mockRunsRoute(page, [RUN_RUNNING]);
    await mockRunDetailRoute(page, RUN_RUNNING.id, RUN_RUNNING);
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    await openDrawer(page);

    // Verify 'Cancel Run' button is visible.
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/Cancel Run/);

    // 'Re-run' and 'Delete Run' should NOT be present.
    await expect(dialog).not.toContainText(/Re-run/);
    await expect(dialog).not.toContainText(/Delete Run/);
  });

  // 8. Fullscreen terminal overlay opens and closes
  test('Fullscreen terminal overlay opens and closes', async ({ page }) => {
    await openDrawer(page);

    // Wait for the run detail to be fully loaded (logs content visible in the drawer).
    // The fullscreen overlay requires d.runs.selectedRun to be truthy — it won't
    // render until the detail fetch completes and the selectedRun is resolved.
    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({ timeout: 5000 });

    // Click the fullscreen toggle button using evaluate to bypass Semi UI
    // SideSheet pointer interception that prevents Playwright's click.
    const btn = page.locator('button[title="Fullscreen Terminal"]').first();
    await btn.waitFor({ state: 'attached', timeout: 5000 });
    await btn.evaluate((el) => (el as HTMLButtonElement).click());

    // Verify fullscreen terminal overlay appears by looking for its heading text.
    const overlayHeading = page.getByText('Read-only Console Terminal');
    await expect(overlayHeading).toBeVisible({ timeout: 5000 });

    // Press Escape to close.
    await page.keyboard.press('Escape');
    await expect(overlayHeading).not.toBeVisible({ timeout: 5000 });

    // The original drawer should still be visible.
    await expect(page.locator('[role="dialog"]').first()).toBeVisible();
  });

  // 9. Fullscreen report overlay opens and closes
  test('Fullscreen report overlay opens and closes', async ({ page }) => {
    await openDrawer(page);

    // Switch to the report tab.
    await page.getByTestId('drawer-tab-report').click();
    await expect(page.getByTestId('report-fullscreen-button')).toBeVisible();

    // Click the fullscreen report button.
    await page.getByTestId('report-fullscreen-button').click();

    // Verify the fullscreen report overlay appears.
    const reportOverlay = page.getByTestId('fullscreen-report-overlay');
    await expect(reportOverlay).toBeVisible({ timeout: 5000 });

    // Verify the iframe element is visible.
    const reportIframe = page.getByTestId('fullscreen-report-iframe');
    await expect(reportIframe).toBeVisible();

    // Close via the close button.
    await page.getByTestId('fullscreen-report-close').click();
    await expect(reportOverlay).not.toBeVisible({ timeout: 5000 });

    // The drawer should still be visible.
    await expect(page.locator('[role="dialog"]').first()).toBeVisible();
  });

  test('Fullscreen report overlay closes on Escape key', async ({ page }) => {
    await openDrawer(page);

    await page.getByTestId('drawer-tab-report').click();
    await page.getByTestId('report-fullscreen-button').click();

    const reportOverlay = page.getByTestId('fullscreen-report-overlay');
    await expect(reportOverlay).toBeVisible({ timeout: 5000 });

    // Press Escape to close.
    await page.keyboard.press('Escape');
    await expect(reportOverlay).not.toBeVisible({ timeout: 5000 });
  });

  // 10. No logs state shows placeholder message
  test('No logs state shows placeholder message', async ({ page }) => {
    // Switch to a run without stdout/stderr.
    await mockRunsRoute(page, [RUN_NO_LOGS]);
    await mockRunDetailRoute(page, RUN_NO_LOGS.id, RUN_NO_LOGS);
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    await openDrawer(page);

    // Logs tab is active by default. Verify the "No logs available" placeholder.
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/no console logs available/i);
  });

  // 11. Close drawer via X button
  test('Close drawer via X button', async ({ page }) => {
    await openDrawer(page);

    // Verify drawer is visible.
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Click the SideSheet close (X) button — Semi UI renders it as .semi-sidesheet-close.
    const closeBtn = page.locator('.semi-sidesheet-close');
    await closeBtn.click();

    // Verify drawer is no longer visible.
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });
  });

  // 12. Close drawer via Escape key
  test('Close drawer via Escape key', async ({ page }) => {
    await openDrawer(page);

    // Verify drawer is visible.
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Press Escape to close.
    await page.keyboard.press('Escape');

    // Verify drawer is no longer visible.
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });
  });

  // 13. Close drawer via mask click
  test('Close drawer via mask click', async ({ page }) => {
    await openDrawer(page);

    // Verify drawer is visible.
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Click the Semi UI SideSheet mask at a position outside the panel.
    await page.locator('.semi-sidesheet-mask').click({ position: { x: 10, y: 10 } });

    // Verify drawer is no longer visible.
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });
  });

  // 14. Fullscreen terminal search filters log lines
  test('Fullscreen terminal search filters log lines', async ({ page }) => {
    await openDrawer(page);

    // Wait for the drawer log tab to be fully loaded.
    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({ timeout: 5000 });

    // Open fullscreen terminal using evaluate to bypass Semi UI SideSheet
    // pointer interception that prevents Playwright's click.
    const btn = page.locator('button[title="Fullscreen Terminal"]').first();
    await btn.waitFor({ state: 'attached', timeout: 5000 });
    await btn.evaluate((el) => (el as HTMLButtonElement).click());

    // Wait for the fullscreen overlay to appear.
    const overlayHeading = page.getByText('Read-only Console Terminal');
    await expect(overlayHeading).toBeVisible({ timeout: 5000 });

    // Locate the fullscreen terminal dialog for scoped assertions.
    const terminalDialog = page.locator('[role="dialog"][aria-labelledby="fullscreen-terminal-title"]');

    // Verify all log content is visible initially (no filter applied).
    await expect(terminalDialog.getByText('PASSED').first()).toBeVisible();
    await expect(terminalDialog.getByText('FAILED').first()).toBeVisible();

    // Type a search query that matches only FAILED lines.
    const searchInput = page.getByPlaceholder('Search logs...');
    await searchInput.fill('FAILED');

    // After filtering by 'FAILED', passed case rows should be hidden while
    // failed case rows remain. The pytest summary can still mention "passed".
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_logout PASSED')).not.toBeVisible();
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_login FAILED')).toBeVisible();

    // Clear the search query by clicking the clear button.
    await page.getByTitle('Clear search').click();

    // All log lines should be restored after clearing the search.
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_logout PASSED')).toBeVisible();
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_login FAILED')).toBeVisible();
  });

  // 15. Fullscreen terminal log level filter buttons work
  test('Fullscreen terminal log level filter buttons work', async ({ page }) => {
    await openDrawer(page);

    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({ timeout: 5000 });

    // Open fullscreen terminal using evaluate to bypass Semi UI SideSheet
    // pointer interception.
    const btn = page.locator('button[title="Fullscreen Terminal"]').first();
    await btn.waitFor({ state: 'attached', timeout: 5000 });
    await btn.evaluate((el) => (el as HTMLButtonElement).click());

    // Wait for the fullscreen overlay to appear.
    const overlayHeading = page.getByText('Read-only Console Terminal');
    await expect(overlayHeading).toBeVisible({ timeout: 5000 });

    // Locate the fullscreen terminal dialog for scoped assertions.
    const terminalDialog = page.locator('[role="dialog"][aria-labelledby="fullscreen-terminal-title"]');

    // Verify both PASSED and FAILED text are visible initially (ALL filter is default).
    await expect(terminalDialog.getByText('PASSED').first()).toBeVisible();
    await expect(terminalDialog.getByText('FAILED').first()).toBeVisible();

    // Click the "ERR" (ERROR) level filter button.
    await page.getByRole('button', { name: 'ERR' }).click();

    // After filtering by ERROR level, passed case rows should be hidden while
    // failed case rows remain. The pytest summary can still mention "passed".
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_logout PASSED')).not.toBeVisible();
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_login FAILED')).toBeVisible();

    // Click the "ALL" level filter button to reset to showing everything.
    await terminalDialog.getByRole('button', { name: 'ALL', exact: true }).click();

    // All log lines should be restored.
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_logout PASSED')).toBeVisible();
    await expect(terminalDialog.getByText('suite_integration/test_auth.py::test_login FAILED')).toBeVisible();
  });

  // ═══════════════════════════════════════════════════════════════════════════════
  // 16. Diff tab shows loading state while fetching
  test('Diff tab shows loading state while fetching', async ({ page }) => {
    // Mock the diff endpoint to respond with a 2-second delay so we can
    // observe the loading state before the data arrives.
    await page.route(`**/runs/${RUN_WITH_LOGS_AND_REPORT.id}/diff`, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_WITH_BASELINE) });
    });

    await openDrawer(page);
    await page.getByTestId('drawer-tab-diff').click();

    // Verify the loading-placeholder text appears while the request is in-flight.
    await expect(page.locator('[role="dialog"]')).toContainText('Loading baseline diff...');

    // Wait for the diff response to arrive and verify real content renders.
    await expect(page.getByTestId('diff-bucket-new_failures')).toBeVisible({ timeout: 5000 });
  });

  // 17. Diff tab shows error state when API fails
  test('Diff tab shows error state when API fails', async ({ page }) => {
    // Mock the diff endpoint to return a 500 server error.
    await page.route(`**/runs/${RUN_WITH_LOGS_AND_REPORT.id}/diff`, async (route) => {
      await route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Internal Server Error"}' });
    });

    await openDrawer(page);
    await page.getByTestId('drawer-tab-diff').click();

    // API failures are different from "no comparable baseline"; operators need
    // to know the comparison failed instead of trusting an empty-baseline state.
    await expect(page.getByTestId('diff-error')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('diff-error')).toContainText(/Unable to load|无法加载/);
    await expect(page.getByTestId('diff-empty-baseline')).toHaveCount(0);

    // The loading indicator should have cleared.
    await expect(page.locator('[role="dialog"]')).not.toContainText('Loading baseline diff...');
  });

  // 18. Cancel run dialog — user rejects confirmation
  test('Cancel run dialog — user rejects confirmation', async ({ page }) => {
    // Switch to a running run so the Cancel Run button is available.
    await mockRunsRoute(page, [RUN_RUNNING]);
    await mockRunDetailRoute(page, RUN_RUNNING.id, RUN_RUNNING);
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    await openDrawer(page);

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/Cancel Run/);

    // Register a dialog handler that dismisses (rejects) the confirm prompt.
    page.on('dialog', (dlg) => {
      dlg.dismiss();
    });

    // Click Cancel Run — this triggers window.confirm().
    await page.getByRole('button', { name: /Cancel Run/i }).click();

    // Because the dialog was dismissed, handleCancelRun was never called.
    // Verify the drawer remains open and the run still shows RUNNING status.
    await expect(page.locator('[role="dialog"]')).toBeVisible();
    await expect(dialog).toContainText(/RUNNING/);
  });

  // 19. Delete run dialog — user rejects confirmation
  test('Delete run dialog — user rejects confirmation', async ({ page }) => {
    // The default mock (RUN_WITH_LOGS_AND_REPORT) is completed + unlocked,
    // so the Delete Run button is visible.
    await openDrawer(page);

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/Delete Run/);

    // Register a dialog handler that dismisses (rejects) the confirm prompt.
    page.on('dialog', (dlg) => {
      dlg.dismiss();
    });

    // Click Delete Run — this triggers window.confirm().
    await page.getByRole('button', { name: /Delete Run/i }).click();

    // Because the dialog was dismissed, handleDeleteRun was never called.
    // Verify the drawer remains open.
    await expect(page.locator('[role="dialog"]')).toBeVisible();
  });

  // 20. Fullscreen terminal — Copy button
  test('Fullscreen terminal — Copy button', async ({ page }) => {
    await openDrawer(page);

    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({ timeout: 5000 });

    // Open fullscreen terminal via evaluate to bypass Semi UI SideSheet
    // pointer interception.
    const btn = page.locator('button[title="Fullscreen Terminal"]').first();
    await btn.waitFor({ state: 'attached', timeout: 5000 });
    await btn.evaluate((el) => (el as HTMLButtonElement).click());

    // Wait for the fullscreen overlay to appear.
    await expect(page.getByText('Read-only Console Terminal')).toBeVisible({ timeout: 5000 });

    // Scope to the fullscreen terminal dialog for targeting the Copy button.
    const terminalDialog = page.locator('[role="dialog"][aria-labelledby="fullscreen-terminal-title"]');

    // The Copy button has a child <span> with text "Copy" (i18n key "copy").
    const copyButton = terminalDialog.locator('button').filter({ hasText: 'Copy' });
    await expect(copyButton).toBeVisible();

    // Click Copy — copyToClipboard always sets copySuccess = true, which
    // toggles the button text from "Copy" to "Copied".
    await copyButton.click();

    // Verify the visual feedback: a copied-state button is shown.
    await expect(terminalDialog.locator('button').filter({ hasText: 'Copied' })).toBeVisible();
  });

  // 21. Fullscreen terminal — Download button
  test('Fullscreen terminal — Download button', async ({ page }) => {
    await openDrawer(page);

    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({ timeout: 5000 });

    // Open fullscreen terminal via evaluate to bypass Semi UI SideSheet
    // pointer interception.
    const btn = page.locator('button[title="Fullscreen Terminal"]').first();
    await btn.waitFor({ state: 'attached', timeout: 5000 });
    await btn.evaluate((el) => (el as HTMLButtonElement).click());

    // Wait for the fullscreen overlay to appear.
    await expect(page.getByText('Read-only Console Terminal')).toBeVisible({ timeout: 5000 });

    // The Download button in the fullscreen terminal has a title attribute
    // set to "Download raw log file".
    const downloadButton = page
      .locator('[role="dialog"][aria-labelledby="fullscreen-terminal-title"]')
      .locator('button[title="Download raw log file"]');
    await expect(downloadButton).toBeVisible();
  });
});
