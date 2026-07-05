import { test, expect } from '@playwright/test';

// Report viewer E2E tests — verifies the report tab in RunDetailsDrawer and the
// fullscreen Allure report overlay using route-mocked run data.
// Runs under `chromium-authed-admin` so it uses storageState instead of UI login.

const MOCK_RUN_COMPLETED_WITH_REPORT = {
  id: 'run-report-0001',
  status: 'completed',
  runner: 'pytest',
  created_by: 'admin',
  tests_path: 'suite/',
  args: [],
  executor_mode: 'subprocess',
  summary: { total: 10, passed: 8, failed: 1, skipped: 1, error: 0, duration_ms: 5000, pass_rate: 0.8 },
  report: {
    allure_results_dir: '/artifacts/run-report-0001/results',
    allure_report_file: '/artifacts/run-report-0001/results/allure-report/index.html',
    html_generated: true,
  },
  exit_code: 1,
  error: null,
  passed: false,
  created_at: '2026-07-02T10:00:00Z',
  started_at: '2026-07-02T10:00:01Z',
  finished_at: '2026-07-02T10:00:06Z',
  stdout: null,
  stderr: null,
  locked: false,
};

const MOCK_RUN_COMPLETED_NO_REPORT = {
  ...MOCK_RUN_COMPLETED_WITH_REPORT,
  id: 'run-report-0002',
  report: null,
};

const MOCK_RUN_RUNNING = {
  ...MOCK_RUN_COMPLETED_WITH_REPORT,
  id: 'run-report-0003',
  status: 'running',
  summary: null,
  report: null,
  finished_at: null,
};

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

test.describe('Report viewer', () => {
  test.beforeEach(async ({ page }) => {
    // Default: mock /runs with a completed run that has a report.
    await page.route('**/runs', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: { runs: [MOCK_RUN_COMPLETED_WITH_REPORT] } });
    });
    await page.route('**/suites', async (route) => {
      await route.fulfill({ json: [] });
    });
    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['suite/'] });
    });
    await openDashboard(page);
  });

  test('report tab shows summary and report buttons for completed run with report', async ({ page }) => {
    // Click the first row in the runs table to open the drawer.
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    const firstRow = page.locator('table tbody tr').first();
    await firstRow.click();

    // The drawer should open.
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Click the Report tab.
    await page.getByTestId('drawer-tab-report').click();

    // Summary bar should show the pass/fail counts.
    // Use more specific selectors to avoid matching dates or IDs.
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toContainText(/passed.*8|8.*passed/i);
    await expect(dialog).toContainText(/failed.*1|1.*failed/i);

    // The fullscreen report button should be visible.
    await expect(page.getByTestId('report-fullscreen-button')).toBeVisible();

    // The "Open in New Window" link should be visible and point to the report URL.
    const newWindowLink = page.getByTestId('report-new-window-link');
    await expect(newWindowLink).toBeVisible();
    await expect(newWindowLink).toHaveAttribute('href', /\/runs\/run-report-0001\/report/);
  });

  test('fullscreen report overlay opens and closes', async ({ page }) => {
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    const firstRow = page.locator('table tbody tr').first();
    await firstRow.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    await page.getByTestId('drawer-tab-report').click();
    await expect(page.getByTestId('report-fullscreen-button')).toBeVisible();

    // Click "Fullscreen Report".
    await page.getByTestId('report-fullscreen-button').click();

    // The fullscreen overlay should appear.
    const overlay = page.getByTestId('fullscreen-report-overlay');
    await expect(overlay).toBeVisible({ timeout: 5000 });

    // The iframe should be present and point to the report URL.
    const iframe = page.getByTestId('fullscreen-report-iframe');
    await expect(iframe).toBeVisible();
    await expect(iframe).toHaveAttribute('src', /\/runs\/run-report-0001\/report/);

    // Close the overlay via the close button.
    await page.getByTestId('fullscreen-report-close').click();
    await expect(overlay).not.toBeVisible({ timeout: 5000 });
  });

  test('fullscreen report overlay closes on Escape key', async ({ page }) => {
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    const firstRow = page.locator('table tbody tr').first();
    await firstRow.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    await page.getByTestId('drawer-tab-report').click();
    await page.getByTestId('report-fullscreen-button').click();

    const overlay = page.getByTestId('fullscreen-report-overlay');
    await expect(overlay).toBeVisible({ timeout: 5000 });

    // Press Escape to close.
    await page.keyboard.press('Escape');
    await expect(overlay).not.toBeVisible({ timeout: 5000 });
  });

  test('report tab shows "no report" for completed run without report', async ({ page }) => {
    // Override /runs to return a completed run without a report.
    await page.route('**/runs', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: { runs: [MOCK_RUN_COMPLETED_NO_REPORT] } });
    });

    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    const firstRow = page.locator('table tbody tr').first();
    await firstRow.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    await page.getByTestId('drawer-tab-report').click();

    // Should show the "no report generated" placeholder.
    await expect(page.locator('[role="dialog"]')).toContainText(/no report/i);
  });

  test('report tab shows "generating" for running run', async ({ page }) => {
    // Override /runs to return a running run.
    await page.route('**/runs', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: { runs: [MOCK_RUN_RUNNING] } });
    });

    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    const firstRow = page.locator('table tbody tr').first();
    await firstRow.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    await page.getByTestId('drawer-tab-report').click();

    // Should show the "generating report" placeholder.
    await expect(page.locator('[role="dialog"]')).toContainText(/generating/i);
  });
});
