import { test, expect } from '@playwright/test';

// spec: specs/ui-test-plan.md §2 — Dashboard Layout and Header
// Covers stat cards, header, sidebar, language toggle, theme toggle, suite trend.
// Runs under `chromium-authed-admin`, so storageState supplies the admin session.

const SUITE_A = 'suite_a';
const SUITE_B = 'suite_b';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

/**
 * Mock the /tests and /suites endpoints so the sidebar shows the given suites.
 * /runs is also mocked to empty so stats are deterministic.
 * Note: /runs must return { runs: [...] } (the app reads data.runs).
 */
async function mockBackend(page: import('@playwright/test').Page, suites: string[]) {
  await page.route('**/runs', (route) => {
    if (route.request().method() === 'GET')
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: [] }) });
    else
      route.continue();
  });
  await page.route('**/suites', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(suites.map(s => ({ name: s, source: 'local', repo_url: null, ref: null }))) }),
  );
  await page.route('**/tests', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(suites) }),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Dashboard Layout and Header', () => {

  // ── 2.1 Dashboard renders all four stat cards with correct labels ──────────

  test('Dashboard renders all four stat cards with correct labels', async ({ page }) => {
    await openDashboard(page);
    // Wait for dashboard to settle
    await expect(page.getByTestId('stat-total')).toBeVisible();
    await expect(page.getByTestId('stat-success-rate')).toBeVisible();
    await expect(page.getByTestId('stat-failed')).toBeVisible();
    await expect(page.getByTestId('stat-active')).toBeVisible();
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
  });

  // ── 2.2 Header displays user info and control buttons for admin ────────────

  test('Header displays user info and control buttons for admin', async ({ page }) => {
    await openDashboard(page);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('profile-role')).toHaveText('admin');
    // Admin-specific button
    await expect(page.getByTestId('open-users-button')).toBeVisible();
    // Trigger button
    await expect(page.getByTestId('open-trigger-button')).toBeVisible();
    // Theme toggle — button with moon/sun icon (no accessible name, match by icon class)
    await expect(page.locator('header button').filter({ has: page.locator('[class*="lucide-moon"], [class*="lucide-sun"]') })).toBeVisible();
    // Language toggle — button shows locale text "中文" or "EN"
    await expect(page.getByRole('button', { name: /中文|EN/ })).toBeVisible();
    // Logout button — last button in the header actions
    await expect(page.locator('header button:last-of-type')).toBeVisible();
  });

  // ── 2.3 Sidebar shows 'All Suites' with add-suite button ───────────────────

  test('Sidebar shows All Suites with add-suite button', async ({ page }) => {
    // Mock with empty suites so the "no suites" message appears
    await mockBackend(page, []);
    await openDashboard(page);
    // 'All Suites' heading (the first sidebar item)
    await expect(page.getByText('All Suites')).toBeVisible();
    // Add suite button
    await expect(page.getByTestId('open-add-suite-button')).toBeVisible();
    // With no suites present, a message should appear
    await expect(page.getByText('No suites found')).toBeVisible();
  });

  // ── 2.4 Language toggle in header switches UI language ─────────────────────

  test('Language toggle in header switches UI language', async ({ page }) => {
    await openDashboard(page);
    // Button shows "中文" (lang=en) or "EN" (lang=zh) as its visible text
    const langToggle = page.getByRole('button', { name: /中文|EN/ });
    // Initial state: English
    await expect(page.getByTestId('stat-total')).toContainText(/total/i);
    // Click to switch to Chinese
    await langToggle.click();
    // The stats label should now be in Chinese
    await expect(page.getByTestId('stat-total')).toContainText(/总数|总/i);
    // Click again to switch back to English
    await langToggle.click();
    await expect(page.getByTestId('stat-total')).toContainText(/total/i);
  });

  // ── 2.5 Theme toggle in header switches between dark and light ─────────────

  test('Theme toggle in header switches between dark and light', async ({ page }) => {
    await openDashboard(page);
    // Theme toggle has no accessible name — locate by moon/sun icon class
    const themeToggle = page.locator('header button').filter({
      has: page.locator('[class*="lucide-moon"], [class*="lucide-sun"]')
    });
    await expect(themeToggle).toBeVisible();
    // Click to switch theme
    await themeToggle.click();
    // Verify the data-theme attribute is applied
    await expect(page.locator('html')).toHaveAttribute('data-theme', /^(dark|light)$/);
    // Click again to switch back
    await themeToggle.click();
    // Toggle is still present
    await expect(themeToggle).toBeVisible();
  });

  // ── 2.6 Suite trend sparkline appears when a suite is selected ─────────────

  test('Suite trend sparkline appears when a suite is selected from sidebar', async ({ page }) => {
    await mockBackend(page, [SUITE_A, SUITE_B]);
    await openDashboard(page);

    // Mock the trend endpoint before clicking the suite
    await page.route('**/runs/trend*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tests_path: SUITE_A,
          points: [
            { run_id: 'r1', created_at: '2026-06-01T10:00:00Z', pass_rate: 0.6, total: 10, passed: 6, failed: 4 },
            { run_id: 'r2', created_at: '2026-06-02T10:00:00Z', pass_rate: 0.8, total: 10, passed: 8, failed: 2 },
            { run_id: 'r3', created_at: '2026-06-03T10:00:00Z', pass_rate: 1.0, total: 10, passed: 10, failed: 0 },
          ],
        }),
      }),
    );

    // No suite filtered yet -> no trend card
    await expect(page.getByTestId('suite-trend')).toHaveCount(0);

    // Click the suite in the sidebar to filter
    await page.getByText(SUITE_A, { exact: true }).first().click();
    await expect(page.getByTestId('suite-trend')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('suite-trend-svg')).toBeVisible();
    // Latest pass rate: 1.0 = 100%
    await expect(page.getByTestId('suite-trend-latest')).toHaveText('100%');

    // Click 'All Suites' to deselect
    await page.getByText('All Suites').click();
    await expect(page.getByTestId('suite-trend')).toHaveCount(0);
  });

  // ── 2.7 Suite trend shows insufficient-data hint with fewer than 2 runs ────

  test('Suite trend shows insufficient-data hint with fewer than 2 runs', async ({ page }) => {
    await mockBackend(page, [SUITE_A]);
    await openDashboard(page);

    // Mock trend endpoint with only 1 data point
    await page.route('**/runs/trend*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tests_path: SUITE_A,
          points: [
            { run_id: 'r1', created_at: '2026-06-01T10:00:00Z', pass_rate: 0.6, total: 10, passed: 6, failed: 4 },
          ],
        }),
      }),
    );

    // Click the suite in the sidebar
    await page.getByText(SUITE_A, { exact: true }).first().click();
    // Wait for the trend section to appear
    await expect(page.getByTestId('suite-trend')).toBeVisible({ timeout: 5000 });
    // Insufficient data hint should be visible
    await expect(page.getByTestId('suite-trend-insufficient')).toBeVisible();
    // SVG should not be rendered
    await expect(page.getByTestId('suite-trend-svg')).toHaveCount(0);
  });

  // ── 2.8 Dashboard loading state while runs are loading ─────────────────────

  test('Shows loading state while runs are loading', async ({ page }) => {
    // Mock /runs with a 3-second delay so the loading state remains visible
    await page.route('**/runs', async (route) => {
      if (route.request().method() === 'GET') {
        await new Promise((r) => setTimeout(r, 3000));
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ runs: [] }),
        });
      } else {
        await route.continue();
      }
    });

    // Mock /suites and /tests to avoid extraneous network errors
    await page.route('**/suites', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
    );
    await page.route('**/tests', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
    );

    await openDashboard(page);

    // While /runs is still loading, the loading state text should be visible
    await expect(page.getByText('Loading execution history...')).toBeVisible({ timeout: 5000 });

    // After the mock fulfills, stats cards should render
    await expect(page.getByTestId('stat-total')).toBeVisible({ timeout: 10000 });
  });

  // ── 2.9 Theme preference persists after page reload ──────────────────────

  test('Theme preference persists after page reload', async ({ page }) => {
    await openDashboard(page);

    // Locate the theme toggle button (icon-based, no accessible name)
    const themeToggle = page.locator('header button').filter({
      has: page.locator('[class*="lucide-moon"], [class*="lucide-sun"]'),
    });
    await expect(themeToggle).toBeVisible();

    // Toggle theme to light if it is not already light
    const currentTheme = await page.locator('html').getAttribute('data-theme');
    if (currentTheme !== 'light') {
      await themeToggle.click();
    }
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

    // Reload the page — localStorage persists, session cookie persists
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Verify the light theme persisted after reload
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  // ── 2.10 Language preference persists after page reload ───────────────────

  test('Language preference persists after page reload', async ({ page }) => {
    await openDashboard(page);

    // Click language toggle to switch to Chinese
    const langToggle = page.getByRole('button', { name: /中文|EN/ });
    await langToggle.click();

    // Verify UI is now in Chinese
    await expect(page.getByTestId('stat-total')).toContainText(/总数|总/i);

    // Reload the page — localStorage and session persist
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Verify the Chinese language persisted after reload
    await expect(page.getByTestId('stat-total')).toContainText(/总数|总/i);
  });

  // ── 2.11 SuiteTrend handles API failure gracefully ─────────────────────────

  test('SuiteTrend handles API failure gracefully', async ({ page }) => {
    await mockBackend(page, [SUITE_A]);
    await openDashboard(page);

    // Mock the trend endpoint to return 500
    await page.route('**/runs/trend*', (route) =>
      route.fulfill({ status: 500 }),
    );

    // Suite-trend should not exist before selecting a suite
    await expect(page.getByTestId('suite-trend')).toHaveCount(0);

    // Click suite in sidebar to trigger trend fetch
    await page.getByText(SUITE_A, { exact: true }).first().click();
    await expect(page.getByTestId('suite-trend')).toBeVisible({ timeout: 5000 });

    // The catch handler sets trend=null, points=[], so "Insufficient data" is shown
    await expect(page.getByTestId('suite-trend-insufficient')).toBeVisible();

    // SVG polyline should not be rendered when trend data is null
    await expect(page.getByTestId('suite-trend-svg')).toHaveCount(0);
  });

  // ── 2.12 StatsCards handles zero/missing data without NaN ──────────────────

  test('StatsCards handles zero/missing data without NaN', async ({ page }) => {
    // mockBackend already returns empty runs, which gives totalRuns=0,
    // completedRuns=[], overallSuccessRate='0'.
    await mockBackend(page, []);
    await openDashboard(page);

    // stat-total should show "0" (no "NaN")
    await expect(page.getByTestId('stat-total')).toBeVisible();
    await expect(page.getByTestId('stat-total')).toContainText('0');

    // stat-success-rate should show "0%" (no "NaN")
    await expect(page.getByTestId('stat-success-rate')).toBeVisible();
    await expect(page.getByTestId('stat-success-rate')).toContainText('0%');

    // The string "NaN" must not appear anywhere on the dashboard
    const nanElements = page.locator('text=NaN');
    await expect(nanElements).toHaveCount(0);
  });
});
