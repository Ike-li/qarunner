import { test, expect } from '@playwright/test';

// Dashboard filtering E2E tests — verifies the RunsTable filter pipeline
// (status, owner, search, reset) using route-mocked run data for determinism.
// Runs under `chromium-authed-admin` so it uses storageState instead of UI login.

const MOCK_RUNS = [
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
];

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

test.describe('Dashboard filters', () => {
  test.beforeEach(async ({ page }) => {
    // Mock the /runs endpoint to return deterministic data.
    await page.route('**/runs', async (route) => {
      // Only mock GET requests; let POST (trigger run) pass through.
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: { runs: MOCK_RUNS } });
    });
    // Mock /suites and /tests to avoid backend dependency for sidebar/trend.
    await page.route('**/suites', async (route) => {
      await route.fulfill({ json: [] });
    });
    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['suite_a/', 'suite_b/', 'suite_c/'] });
    });
    await openDashboard(page);
    // Wait for the runs table to render.
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
  });

  test('stats cards display correct totals', async ({ page }) => {
    // 4 runs total, 2 completed (both passed), 1 failed, 1 queued
    await expect(page.getByTestId('stat-total')).toContainText('4');
    await expect(page.getByTestId('stat-failed')).toContainText('1');
    await expect(page.getByTestId('stat-active')).toContainText('1');
  });

  test('filter by status shows only matching runs', async ({ page }) => {
    // Open status filter and select "failed"
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option-list .semi-select-option').filter({ hasText: /failed/i }).click();

    // Should show only the failed run
    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-beta');
  });

  test('filter by owner shows only matching runs', async ({ page }) => {
    const ownerSelect = page.getByTestId('filter-owner-select');
    await ownerSelect.click();
    await page.locator('.semi-select-option-list .semi-select-option').filter({ hasText: 'alice' }).click();

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-beta');
  });

  test('search by run ID filters correctly', async ({ page }) => {
    const searchInput = page.getByTestId('search-run-input');
    await searchInput.fill('gamma');

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-gamm');
  });

  test('reset filters restores all runs', async ({ page }) => {
    // First apply a filter
    const statusSelect = page.getByTestId('filter-status-select');
    await statusSelect.click();
    await page.locator('.semi-select-option-list .semi-select-option').filter({ hasText: /failed/i }).click();

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(1);

    // Reset filters
    await page.getByTestId('reset-filters-button').click();

    // All 4 runs should be visible again
    await expect(rows).toHaveCount(4);
  });

  test('combined filters narrow results correctly', async ({ page }) => {
    // Filter by owner=admin AND search by "alpha"
    const ownerSelect = page.getByTestId('filter-owner-select');
    await ownerSelect.click();
    await page.locator('.semi-select-option-list .semi-select-option').filter({ hasText: 'admin' }).click();

    const searchInput = page.getByTestId('search-run-input');
    await searchInput.fill('alpha');

    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('run-alph');
  });
});
