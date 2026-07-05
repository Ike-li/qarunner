import { test, expect } from '@playwright/test';

// Verifies the cross-run Diff tab (stage 2.4) renders the API's RegressionDiff
// shape. Runs under `chromium-authed-admin` with storageState; the runs list,
// run detail and the /diff endpoint are route-mocked so the rendering, bucket
// ordering/colouring and the no-baseline empty state are exercised
// deterministically without needing real baseline data on disk.

const RUN_ID = 'run-e2e-diff-0001';

const MOCK_RUN = {
  id: RUN_ID,
  status: 'completed',
  runner: 'pytest',
  created_by: 'admin',
  profile_id: 'profile-e2e-diff',
  tests_path: 'suite/',
  args: ['-k', 'smoke'],
  executor_mode: 'docker',
  summary: { total: 5, passed: 3, failed: 2, skipped: 0, error: 0, duration_ms: 100, pass_rate: 0.6 },
  report: null,
  exit_code: 1,
  error: null,
  passed: false,
  created_at: '2026-06-10T10:00:00Z',
  started_at: '2026-06-10T10:00:01Z',
  finished_at: '2026-06-10T10:00:05Z',
  stdout: null,
  stderr: null,
  locked: false,
};

const DIFF_WITH_BASELINE = {
  baseline: { id: 'baseline-run-abcdef12', created_at: '2026-06-01T12:00:00Z', status: 'completed' },
  diff: {
    new_failures: [{ suite: 'auth', name: 'test_login', status: 'failed', duration_ms: 12, message: 'AssertionError: 401 != 200' }],
    still_failing: [{ suite: 'pay', name: 'test_charge', status: 'failed', duration_ms: 5, message: null }],
    fixed: [{ suite: 'auth', name: 'test_logout', status: 'passed', duration_ms: 3, message: null }],
    new_cases: [{ suite: 'new', name: 'test_added', status: 'passed', duration_ms: 1, message: null }],
    removed_cases: [{ suite: 'old', name: 'test_gone', status: 'passed', duration_ms: 1, message: null }],
  },
};

const DIFF_NO_BASELINE = {
  baseline: null,
  diff: { new_failures: [], still_failing: [], fixed: [], new_cases: [], removed_cases: [] },
};

// Default calibrated policy requires four observations and three flips.
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

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

async function openRunAndDiffTab(page: import('@playwright/test').Page) {
  const firstRow = page.locator('table tbody tr').first();
  await expect(firstRow).toBeVisible({ timeout: 8000 });
  await firstRow.click();
  await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 5000 });
  await page.getByTestId('drawer-tab-diff').click();
}

test.describe('Run details — Diff tab (cross-run stage 2)', () => {
  test.beforeEach(async ({ page }) => {
    // Route-mock the runs list + detail so a clickable row always exists.
    await page.route('**/runs', (route) => {
      if (route.request().method() === 'GET') {
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: [MOCK_RUN] }) });
      } else {
        route.continue();
      }
    });
    await page.route(`**/runs/${RUN_ID}`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_RUN) }),
    );
    await openDashboard(page);
  });

  test('renders the five diff buckets from the API', async ({ page }) => {
    await page.route(`**/runs/${RUN_ID}/diff`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_WITH_BASELINE) }),
    );

    await openRunAndDiffTab(page);

    // New-failures bucket on top, carrying the failing case + its message.
    await expect(page.getByTestId('diff-bucket-new_failures')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('test_login', { exact: false })).toBeVisible();
    await expect(page.getByText('AssertionError: 401 != 200', { exact: false })).toBeVisible();
    // Other buckets render too.
    await expect(page.getByTestId('diff-bucket-still_failing')).toBeVisible();
    await expect(page.getByTestId('diff-bucket-fixed')).toBeVisible();
    await expect(page.getByTestId('diff-bucket-new_cases')).toBeVisible();
    await expect(page.getByTestId('diff-bucket-removed_cases')).toBeVisible();
  });

  test('shows the empty state when there is no baseline', async ({ page }) => {
    await page.route(`**/runs/${RUN_ID}/diff`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_NO_BASELINE) }),
    );

    await openRunAndDiffTab(page);

    await expect(page.getByTestId('diff-empty-baseline')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('diff-bucket-new_failures')).toHaveCount(0);
  });

  test('expands a case to reveal its cross-run history and flaky badge', async ({ page }) => {
    await page.route(`**/runs/${RUN_ID}/diff`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DIFF_WITH_BASELINE) }),
    );
    let historyRequestUrl: URL | null = null;
    await page.route('**/cases/history*', (route) => {
      historyRequestUrl = new URL(route.request().url());
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(FLAKY_HISTORY),
      });
    });

    await openRunAndDiffTab(page);
    await expect(page.getByTestId('diff-bucket-new_failures')).toBeVisible({ timeout: 5000 });

    // History is lazy — collapsed until the case row is clicked.
    await expect(page.getByTestId('case-history')).toHaveCount(0);
    await page.getByTestId('diff-case-toggle').first().click();

    await expect(page.getByTestId('case-history').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('flaky-badge').first()).toBeVisible();
    expect(historyRequestUrl?.searchParams.get('profile_id')).toBe('profile-e2e-diff');
  });
});
