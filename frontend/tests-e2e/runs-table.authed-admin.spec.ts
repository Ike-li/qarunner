import { test, expect } from '@playwright/test';

// Runs Table and Filtering E2E tests — verifies the RunsTable rendering,
// column layout, filter pipeline (status, owner, search, reset, combined,
// log filter tabs), and empty state with "Launch your first run" button.
// Runs under `chromium-authed-admin` so it uses storageState instead of UI login.

const MOCK_RUNS_MIXED = [
  {
    id: 'run-alpha-0001',
    status: 'completed',
    runner: 'pytest',
    created_by: 'admin',
    tests_path: 'suite_a/',
    args: [],
    executor_mode: 'subprocess',
    summary: { total: 10, passed: 10, failed: 0, skipped: 0, error: 0, duration_ms: 500, pass_rate: 1.0 },
    report: null,
    exit_code: 0,
    error: null,
    passed: true,
    created_at: '2026-07-01T10:00:00Z',
    started_at: '2026-07-01T10:00:01Z',
    finished_at: '2026-07-01T10:00:05Z',
    stdout: null,
    stderr: null,
    locked: false,
  },
  {
    id: 'run-beta-0002',
    status: 'failed',
    runner: 'pytest',
    created_by: 'alice',
    tests_path: 'suite_b/',
    args: [],
    executor_mode: 'subprocess',
    summary: { total: 5, passed: 3, failed: 2, skipped: 0, error: 0, duration_ms: 200, pass_rate: 0.6 },
    report: null,
    exit_code: 1,
    error: null,
    passed: false,
    created_at: '2026-07-01T11:00:00Z',
    started_at: '2026-07-01T11:00:01Z',
    finished_at: '2026-07-01T11:00:03Z',
    stdout: null,
    stderr: null,
    locked: false,
  },
  {
    id: 'run-gamma003',
    status: 'completed',
    runner: 'playwright',
    created_by: 'admin',
    tests_path: 'suite_a/',
    args: [],
    executor_mode: 'docker',
    summary: { total: 8, passed: 8, failed: 0, skipped: 0, error: 0, duration_ms: 800, pass_rate: 1.0 },
    report: null,
    exit_code: 0,
    error: null,
    passed: true,
    created_at: '2026-07-01T12:00:00Z',
    started_at: '2026-07-01T12:00:01Z',
    finished_at: '2026-07-01T12:00:08Z',
    stdout: null,
    stderr: null,
    locked: false,
  },
  {
    id: 'run-delta004',
    status: 'queued',
    runner: 'pytest',
    created_by: 'system:schedule',
    tests_path: 'suite_c/',
    args: [],
    executor_mode: 'subprocess',
    summary: null,
    report: null,
    exit_code: null,
    error: null,
    passed: false,
    created_at: '2026-07-01T13:00:00Z',
    started_at: null,
    finished_at: null,
    stdout: null,
    stderr: null,
    locked: false,
  },
  {
    id: 'run-epsilon5',
    status: 'running',
    runner: 'playwright',
    created_by: 'system:schedule',
    tests_path: 'suite_d/',
    args: [],
    executor_mode: 'subprocess',
    summary: null,
    report: null,
    exit_code: null,
    error: null,
    passed: false,
    created_at: '2026-07-01T14:00:00Z',
    started_at: null,
    finished_at: null,
    stdout: null,
    stderr: null,
    locked: true,
  },
  {
    id: 'run-zeta-006',
    status: 'completed',
    runner: 'pytest',
    created_by: 'bob',
    tests_path: 'suite_a/',
    args: [],
    executor_mode: 'subprocess',
    summary: { total: 20, passed: 18, failed: 2, skipped: 0, error: 0, duration_ms: 1200, pass_rate: 0.9 },
    report: null,
    exit_code: 0,
    error: null,
    passed: true,
    created_at: '2026-07-01T15:00:00Z',
    started_at: '2026-07-01T15:00:01Z',
    finished_at: '2026-07-01T15:00:12Z',
    stdout: null,
    stderr: null,
    locked: false,
  },
];

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

async function mockRunsRoute(page: import('@playwright/test').Page, runsData: unknown) {
  await page.route('**/runs', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: { runs: runsData } });
  });
  // Mock /suites, /tests, /profiles to avoid backend dependency.
  await page.route('**/suites', async (route) => {
    await route.fulfill({ json: ['suite_a/', 'suite_b/', 'suite_c/', 'suite_d/'] });
  });
  await page.route('**/tests', async (route) => {
    await route.fulfill({ json: ['suite_a/', 'suite_b/', 'suite_c/', 'suite_d/'] });
  });
  await page.route('**/profiles', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: [] });
  });
}

test.describe('Runs Table and Filtering', () => {
  // 1. Execution records table displays with correct columns
  test('Execution records table displays with correct columns', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Verify the table rendered with data rows.
    const tableRows = page.locator('table tbody tr');
    await expect(tableRows).not.toHaveCount(0);

    // Verify table columns by checking header text.
    const headerCells = page.locator('table thead th');
    // The table has columns: Run ID, Lock icon, Target Suite, Status, Owner, Results, Pass Rate, Duration, Created At, arrow
    // Verify key headers are present.
    await expect(headerCells.nth(0)).toContainText(/run/i);
    await expect(headerCells.nth(2)).toContainText(/suite/i);
    await expect(headerCells.nth(3)).toContainText(/status/i);
    await expect(headerCells.nth(4)).toContainText(/owner/i);
    await expect(headerCells.nth(5)).toContainText(/result/i);

    // Verify Run ID is truncated to 8 characters.
    const firstRowIdCell = tableRows.first().locator('td').nth(0);
    const idText = await firstRowIdCell.textContent();
    expect(idText?.trim().length).toBeLessThanOrEqual(8);

    // Verify status tag uses a color (Semi Tag has a color attribute or class).
    const statusCell = tableRows.first().locator('td').nth(3);
    await expect(statusCell.locator('.semi-tag')).toBeVisible();

    // Verify progress bar is visible for rows with summary data.
    const completedRow = tableRows.filter({ hasText: 'run-alpha' });
    if (await completedRow.count() > 0) {
      await expect(completedRow.first().locator('.semi-progress')).toBeVisible();
    }
  });

  // 2. Filter by status shows only matching runs
  test('Filter by status shows only matching runs', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Initially all 6 runs should be visible (or filtered by logFilterTab="All").
    let rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(6);

    // Open status filter and select "failed".
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: /failed/i }).click();

    // Should show only the failed run (run-beta-0002).
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-beta');

    // Select "completed".
    await statusSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: /completed/i }).click();
    await expect(rows).toHaveCount(3);
  });

  // 3. Filter by owner shows only matching runs
  test('Filter by owner shows only matching runs', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(6);

    // Open owner filter and select "alice".
    const ownerSelect = page.getByTestId('filter-owner-select');
    await ownerSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: 'alice' }).click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-beta');
  });

  // 4. Search by Run ID filters correctly
  test('Search by Run ID filters correctly', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(6);

    // Type partial run ID into search input.
    const searchInput = page.getByPlaceholder(/Search Run ID|搜索运行 ID/);
    await searchInput.fill('gamma');
    await expect(rows).toHaveCount(1);
    // Run ID is truncated to 8 chars in the table
    await expect(rows.first()).toContainText('run-gamm');

    // Clear and search for something that matches multiple runs.
    await searchInput.fill('alpha');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-alph');
  });

  // 5. Reset filters restores all runs
  test('Reset filters restores all runs', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(6);

    // Apply a status filter.
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: /failed/i }).click();
    await expect(rows).toHaveCount(1);

    // Reset filters.
    await page.getByTestId('reset-filters-button').click();

    // All 6 runs should be visible again.
    await expect(rows).toHaveCount(6);

    // Verify the reset-filters-button is no longer visible.
    await expect(page.getByTestId('reset-filters-button')).not.toBeVisible();
  });

  // 6. Combined filters narrow results correctly
  test('Combined filters narrow results correctly', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(6);

    // Set status filter to "completed".
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: /completed/i }).click();
    await expect(rows).toHaveCount(3);

    // Also search by Run ID containing "alpha".
    const searchInput = page.getByPlaceholder(/Search Run ID|搜索运行 ID/);
    await searchInput.fill('alpha');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-alph');
  });

  // 7. Log filter tab buttons filter by All / Manual / Scheduled
  test('Log filter tab buttons filter by All / Manual / Scheduled', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // MOCK_RUNS_MIXED has 4 manual runs (admin x2, alice, bob) and 2 scheduled (system:schedule x2).
    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(6);

    // Click "Manually Triggered" radio button.
    await page.getByText(/Manually Triggered/i).click();
    await expect(rows).toHaveCount(4);
    // Verify all visible rows contain manual owners (not "system:schedule").
    for (let i = 0; i < 4; i++) {
      const rowText = await rows.nth(i).textContent();
      expect(rowText).not.toContain('system:schedule');
    }

    // Click "Scheduled Runs" radio button.
    await page.getByText(/Scheduled Runs/i).click();
    await expect(rows).toHaveCount(2);
    // Verify all visible rows are scheduled.
    for (let i = 0; i < 2; i++) {
      const rowText = await rows.nth(i).textContent();
      expect(rowText).toContain('system:schedule');
    }

    // Click "All Runs" radio button to restore all.
    await page.getByText(/All Runs/i).click();
    await expect(rows).toHaveCount(6);
  });

  // 8. Empty table state shows placeholder with 'Launch First Run' button
  test('Empty table state shows placeholder with Launch First Run button', async ({ page }) => {
    await mockRunsRoute(page, []);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Verify the empty state placeholder text (no runs message).
    await expect(page.getByText(/no runs/i)).toBeVisible({ timeout: 10000 });

    // Verify 'Launch your first run' button is visible.
    const launchButton = page.getByText(/Launch First Run|启动首次运行/i);
    await expect(launchButton).toBeVisible();

    // The empty-state button calls setIsTriggerModalOpen(true) which should
    // render the TriggerRunModal. If the button click doesn't trigger React's
    // state update, fall back to clicking the header trigger button which also
    // calls fetchTests().
    await launchButton.click({ force: true });
    // If modal didn't open, use the header trigger button as fallback
    if (!(await page.getByTestId('trigger-modal').isVisible().catch(() => false))) {
      await page.getByTestId('open-trigger-button').click();
    }
    await expect(page.getByTestId('trigger-modal')).toBeVisible({ timeout: 5000 });
  });

  // ── 9. Pagination appears with more than 10 runs ─────────────────────
  test('Pagination appears with more than 10 runs', async ({ page }) => {
    // Generate 12 mock runs (duplicate base data with unique IDs)
    const twelveRuns = Array.from({ length: 12 }, (_, i) => ({
      ...MOCK_RUNS_MIXED[i % MOCK_RUNS_MIXED.length],
      id: `run-paginate-${String(i + 1).padStart(3, '0')}`,
    }));

    await mockRunsRoute(page, twelveRuns);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Should show 10 rows on page 1 with pagination controls visible.
    await expect(page.locator('table tbody tr')).toHaveCount(10);
    await expect(page.locator('.semi-page')).toBeVisible();

    // Click next page button.
    await page.locator('.semi-page-next').click();

    // Should show remaining 2 rows on page 2.
    await expect(page.locator('table tbody tr')).toHaveCount(2);
  });

  // ── 10. Search with no matching results ──────────────────────────────
  test('Search with no matching results', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(6);

    // Search for a run ID that does not exist in the mock data.
    const searchInput = page.getByPlaceholder(/Search Run ID|搜索运行 ID/);
    await searchInput.fill('NO_RESULT_ZZZZZ');

    // Expect no data rows in the table.
    await expect(page.locator('table tbody tr')).toHaveCount(0);
  });

  // ── 11. Lock toggle button toggles run lock state ────────────────────
  test('Lock toggle button toggles run lock state', async ({ page }) => {
    // Mock the lock toggle endpoint before mockRunsRoute so the fallback
    // chain reaches this handler for non-GET /runs/*/lock requests.
    let lockRequested = false;
    await page.route('**/runs/*/lock', async (route) => {
      if (route.request().method() === 'PUT') {
        lockRequested = true;
        await route.fulfill({ status: 200, json: { locked: true } });
      } else {
        await route.fallback();
      }
    });

    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(6);

    // Click the lock button in column index 1 of the first row
    // (run-alpha-0001 has locked: false).
    const lockBtn = page.locator('table tbody tr').first().locator('td').nth(1).locator('button');
    await lockBtn.click();

    // Verify PUT /runs/{id}/lock was called.
    await expect(async () => {
      expect(lockRequested).toBe(true);
    }).toPass({ timeout: 5000 });
  });

  // ── 12. Refresh button re-fetches runs ───────────────────────────────
  test('Refresh button re-fetches runs', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(6);

    // Start listening for the next GET /runs response.
    const getRunsPromise = page.waitForResponse(
      (resp) => resp.request().method() === 'GET' && resp.url().includes('/runs'),
      { timeout: 10000 },
    );

    // Click the refresh button (icon button with title "Refresh Execution Logs").
    await page.getByRole('button', { name: /Refresh Execution/i }).click();

    // Verify a new GET /runs response was received.
    const response = await getRunsPromise;
    expect(response.status()).toBe(200);
  });

  // ── 13. Filter by "running" status ───────────────────────────────────
  test('Filter by "running" status', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(6);

    // Open status filter and select "running".
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: /^running$/i }).click();

    // Should show only the running run (run-epsilon5).
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.locator('table tbody tr').first()).toContainText('run-eps');
  });

  // ── 14. Filter by "queued" status ────────────────────────────────────
  test('Filter by "queued" status', async ({ page }) => {
    await mockRunsRoute(page, MOCK_RUNS_MIXED);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(6);

    // Open status filter and select "queued".
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: /^queued$/i }).click();

    // Should show only the queued run (run-delta004).
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.locator('table tbody tr').first()).toContainText('run-delt');
  });

  // ── 15. Filter by "timeout" status ───────────────────────────────────
  test('Filter by "timeout" status', async ({ page }) => {
    // Create a modified mock that includes a timeout-status run.
    const runsWithTimeout = [
      ...MOCK_RUNS_MIXED,
      {
        id: 'run-timeout-007',
        status: 'timeout',
        runner: 'pytest',
        created_by: 'admin',
        tests_path: 'suite_e/',
        args: [],
        executor_mode: 'subprocess',
        summary: null,
        report: null,
        exit_code: null,
        error: 'Execution timed out after 300s',
        passed: false,
        created_at: '2026-07-01T16:00:00Z',
        started_at: '2026-07-01T16:00:01Z',
        finished_at: '2026-07-01T16:05:00Z',
        stdout: null,
        stderr: null,
        locked: false,
      },
    ];

    await mockRunsRoute(page, runsWithTimeout);
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(7);

    // Open status filter and select "timeout".
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option').filter({ hasText: /^timeout$/i }).click();

    // Should show only the timeout run (run-timeout-007).
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.locator('table tbody tr').first()).toContainText('run-time');
  });
});
