import { test, expect } from '@playwright/test';

// Verifies the cross-run pass-rate trend sparkline (stage 1). Login hits the
// real backend (same as the other specs); /tests + /suites are route-mocked so
// the sidebar shows a clickable suite, and /runs/trend is mocked so the SVG
// polyline (and the <2-points hint) render deterministically without real
// cross-run data on disk.

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';
const SUITE = 'suite_a';

const TREND_3 = {
  tests_path: SUITE,
  points: [
    { run_id: 'r1', created_at: '2026-06-01T10:00:00Z', pass_rate: 0.6, total: 10, passed: 6, failed: 4 },
    { run_id: 'r2', created_at: '2026-06-02T10:00:00Z', pass_rate: 0.8, total: 10, passed: 8, failed: 2 },
    { run_id: 'r3', created_at: '2026-06-03T10:00:00Z', pass_rate: 1.0, total: 10, passed: 10, failed: 0 },
  ],
};

async function login(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByTestId('login-username').fill('admin');
  await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

test.describe('Dashboard — suite pass-rate trend (cross-run stage 1)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/runs', (route) => {
      if (route.request().method() === 'GET') {
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: [] }) });
      } else {
        route.continue();
      }
    });
    await page.route('**/tests', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([SUITE]) }),
    );
    await page.route('**/suites', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ name: SUITE, source: 'local', repo_url: null, ref: null }]) }),
    );
    await login(page);
  });

  test('renders the trend sparkline for the filtered suite', async ({ page }) => {
    await page.route('**/runs/trend*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TREND_3) }),
    );

    // No suite filtered yet → no trend card.
    await expect(page.getByTestId('suite-trend')).toHaveCount(0);
    // Click the suite in the sidebar → sets the filter → SuiteTrend fetches.
    await page.getByText(SUITE, { exact: true }).first().click();

    await expect(page.getByTestId('suite-trend')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('suite-trend-svg')).toBeVisible();
    await expect(page.getByTestId('suite-trend-latest')).toHaveText('100%');
  });

  test('shows the insufficient-data hint with fewer than two points', async ({ page }) => {
    await page.route('**/runs/trend*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ tests_path: SUITE, points: [TREND_3.points[0]] }) }),
    );

    await page.getByText(SUITE, { exact: true }).first().click();

    await expect(page.getByTestId('suite-trend-insufficient')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('suite-trend-svg')).toHaveCount(0);
  });
});
