import { test, expect } from '@playwright/test';

// Sidebar Suite Navigation E2E tests
// Tests suite filtering, git-specific buttons, profile display, instant run,
// and profile deletion in the left sidebar.
// Runs under `chromium-authed-admin`: admin auth comes from storageState.

const SUITE_A = 'suite_a/';
const SUITE_B = 'suite_b/';

// ---------------------------------------------------------------------------
// Shared mock data
// ---------------------------------------------------------------------------

const GIT_SUITE = { name: SUITE_A, source: 'git', repo_url: 'https://example.com/repo.git', ref: 'main' };
const LOCAL_SUITE = { name: SUITE_B, source: 'local', repo_url: null, ref: null };

const RUNS = [
  {
    id: 'run-001',
    status: 'completed',
    runner: 'pytest',
    created_by: 'admin',
    profile_id: 'prof-001',
    tests_path: SUITE_A,
    args: [],
    executor_mode: 'subprocess',
    summary: { total: 10, passed: 8, failed: 2, skipped: 0, error: 0, duration_ms: 5000, pass_rate: 0.8 },
    report: null,
    exit_code: 0,
    error: null,
    passed: false,
    created_at: '2026-07-01T10:00:00Z',
    started_at: '2026-07-01T10:00:01Z',
    finished_at: '2026-07-01T10:00:06Z',
    locked: false,
  },
  {
    id: 'run-002',
    status: 'completed',
    runner: 'playwright',
    created_by: 'admin',
    profile_id: 'prof-other',
    tests_path: SUITE_A,
    args: [],
    executor_mode: 'subprocess',
    summary: { total: 5, passed: 5, failed: 0, skipped: 0, error: 0, duration_ms: 3000, pass_rate: 1.0 },
    report: null,
    exit_code: 0,
    error: null,
    passed: true,
    created_at: '2026-07-02T10:00:00Z',
    started_at: '2026-07-02T10:00:01Z',
    finished_at: '2026-07-02T10:00:04Z',
    locked: false,
  },
  {
    id: 'run-003',
    status: 'completed',
    runner: 'pytest',
    created_by: 'alice',
    profile_id: 'prof-other',
    tests_path: SUITE_B,
    args: [],
    executor_mode: 'subprocess',
    summary: { total: 3, passed: 3, failed: 0, skipped: 0, error: 0, duration_ms: 1000, pass_rate: 1.0 },
    report: null,
    exit_code: 0,
    error: null,
    passed: true,
    created_at: '2026-07-01T11:00:00Z',
    started_at: '2026-07-01T11:00:01Z',
    finished_at: '2026-07-01T11:00:02Z',
    locked: false,
  },
];

const PROFILE_WITH_RUNS = {
  id: 'prof-001',
  name: 'Smoke Tests',
  description: null,
  tests_path: SUITE_A,
  runner: 'pytest',
  selected_files: [],
  selected_markers: [],
  extra_args: '',
  executor_mode: 'subprocess',
  timeout: null,
  created_by: 'admin',
  created_at: '2026-07-01T10:00:00Z',
  env: {},
};

const PROFILE_NO_RUNS = {
  id: 'prof-002',
  name: 'No Runs Profile',
  description: null,
  tests_path: SUITE_B,
  runner: 'playwright',
  selected_files: [],
  selected_markers: [],
  extra_args: '',
  executor_mode: 'docker',
  timeout: null,
  created_by: 'admin',
  created_at: '2026-07-01T11:00:00Z',
  env: {},
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

/**
 * Register route mocks before dashboard load so all API calls during page load are
 * intercepted.
 */
async function mockBackend(
  page: import('@playwright/test').Page,
  options: {
    suites?: Array<{ name: string; source: string; repo_url: string | null; ref: string | null }>;
    runs?: { runs: any[] };
    profiles?: any[];
    profilesDelete?: boolean;
    triggerRun?: boolean;
    triggerProfile?: boolean;
  } = {},
) {
  const { suites, runs, profiles, profilesDelete, triggerRun, triggerProfile } = options;

  // ── /runs ────────────────────────────────────────────
  if (runs) {
    await page.route('**/runs', async (route) => {
      if (route.request().method() === 'POST' && triggerRun) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'run-triggered-001',
            status: 'queued',
            runner: 'pytest',
            created_by: 'admin',
            tests_path: SUITE_A,
            args: [],
            executor_mode: 'subprocess',
            summary: null,
            report: null,
            exit_code: null,
            error: null,
            passed: false,
            created_at: new Date().toISOString(),
            started_at: null,
            finished_at: null,
            locked: false,
          }),
        });
        return;
      }
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Unexpected direct /runs trigger' }),
        });
        return;
      }
      if (route.request().method() === 'GET') {
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify(runs) });
        return;
      }
      await route.fallback();
    });
  } else {
    await page.route('**/runs', (route) => route.fulfill({ json: { runs: [] } }));
  }

  // ── /suites ───────────────────────────────────────────
  if (suites) {
    await page.route('**/suites', (route) => route.fulfill({ json: suites }));
  } else {
    await page.route('**/suites', (route) => route.fulfill({ json: [] }));
  }

  // ── git-suite lifecycle actions ───────────────────────
  await page.route(/\/tests\/[^/]+\/(pull|prepare)$/, async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'ok' }),
      });
      return;
    }
    await route.fallback();
  });

  // ── /profiles ──────────────────────────────────────────
  if (profiles) {
    let profilesData = [...profiles];

    await page.route('**/profiles', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ json: profilesData });
        return;
      }
      await route.fallback();
    });

    if (profilesDelete) {
      await page.route(/\/profiles\//, async (route) => {
        if (route.request().method() === 'DELETE') {
          profilesData = [];
          await route.fulfill({ status: 200, json: { message: 'deleted' } });
          return;
        }
        await route.fallback();
      });
    }

    if (triggerProfile) {
      await page.route(/\/profiles\/[^/]+\/trigger$/, async (route) => {
        if (route.request().method() === 'POST') {
          await route.fulfill({
            status: 202,
            contentType: 'application/json',
            body: JSON.stringify({
              id: 'run-triggered-001',
              status: 'queued',
              runner: 'pytest',
              created_by: 'admin',
              profile_id: 'prof-001',
              tests_path: SUITE_A,
              args: [],
              executor_mode: 'docker',
              summary: null,
              report: null,
              exit_code: null,
              error: null,
              passed: null,
              created_at: new Date().toISOString(),
              started_at: null,
              finished_at: null,
              locked: false,
            }),
          });
          return;
        }
        await route.fallback();
      });
    }
  } else {
    await page.route('**/profiles', (route) => route.fulfill({ json: [] }));
  }

  // ── Other common endpoints ──────────────────────────────
  await page.route('**/schedules', (route) => route.fulfill({ json: [] }));
  await page.route('**/credentials', (route) => route.fulfill({ json: { credentials: [] } }));
  await page.route('**/runs/trend*', (route) =>
    route.fulfill({ json: { tests_path: '', points: [] } }),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Sidebar Suite Navigation', () => {

  // ── 1. Clicking a suite in sidebar filters the runs table ───────────────────

  test('Clicking a suite in sidebar filters the runs table', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE, LOCAL_SUITE],
      runs: { runs: RUNS },
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Initially all 3 runs should be visible
    const tableRows = page.locator('table tbody tr');
    await expect(tableRows).toHaveCount(3);

    // Click suite_a/ in the sidebar to filter by it
    await page.getByText(SUITE_A, { exact: true }).first().click();

    // Only the 2 runs for suite_a/ should remain visible
    await expect(tableRows).toHaveCount(2);

    // Verify that the visible rows contain suite_a/ text
    await expect(tableRows.first()).toContainText(SUITE_A);

    // Click 'All Suites' to clear the filter
    await page.getByText('All Suites').click();

    // All 3 runs should be visible again
    await expect(tableRows).toHaveCount(3);
  });

  // ── 2. Suite update and prepare buttons visible for git suites ─────────────

  test('Suite update and prepare buttons visible for git suites', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE, LOCAL_SUITE],
      runs: { runs: [] },
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Hover over the git suite (suite_a/) to reveal action buttons
    await page.getByText(SUITE_A, { exact: true }).first().hover();

    // Git-specific buttons should be visible
    await expect(page.getByTestId(`suite-update-${SUITE_A}`)).toBeVisible();
    await expect(page.getByTestId(`suite-prepare-${SUITE_A}`)).toBeVisible();

    // The remove button is visible for all suites
    await expect(page.getByTestId(`suite-remove-${SUITE_A}`)).toBeVisible();

    // Hover over the local suite (suite_b/)
    await page.getByText(SUITE_B, { exact: true }).first().hover();

    // Git-specific buttons should NOT exist for local suites
    await expect(page.getByTestId(`suite-update-${SUITE_B}`)).toHaveCount(0);
    await expect(page.getByTestId(`suite-prepare-${SUITE_B}`)).toHaveCount(0);

    // The remove button should still be visible for local suites
    await expect(page.getByTestId(`suite-remove-${SUITE_B}`)).toBeVisible();
  });

  // ── 3. Profile row shows pass rate and history dots ─────────────────────────

  test('Profile row shows pass rate and history dots', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE, LOCAL_SUITE],
      runs: { runs: RUNS },
      profiles: [PROFILE_WITH_RUNS, PROFILE_NO_RUNS],
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // ── Profile with runs (Smoke Tests / pytest) ──

    // Profile name is visible
    const profileWithRuns = page.getByText('Smoke Tests', { exact: true }).first();
    await expect(profileWithRuns).toBeVisible();

    // Runner tag (pytest) is visible in the profile row
    await expect(page.getByText(/pytest/).first()).toBeVisible();

    // Pass rate tag: profile prof-001 owns only run-001, so other profiles'
    // runs in the same suite must not pollute this row.
    await expect(page.getByText(/^0% Pass$/)).toBeVisible();

    // History dots: profile renders 5 dot spans total (2 real runs + 3 empty
    // padding). Real run dots have role="button" for clickability.
    // Navigate up from the profile name span to the profile item container,
    // then count elements with role="button" which represent real run dots.
    const profileItemContainer = profileWithRuns.locator('..').locator('..').locator('..');
    const clickableDots = profileItemContainer.locator('span[role="button"]');
    await expect(clickableDots).toHaveCount(1);

    // ── Profile without runs (No Runs Profile / playwright) ──

    const profileNoRuns = page.getByText('No Runs Profile', { exact: true }).first();
    await expect(profileNoRuns).toBeVisible();

    // Runner tag should show 'playwright'
    await expect(page.getByText(/playwright/).first()).toBeVisible();

    // Should show 'No runs' label instead of a pass rate percentage
    await expect(page.getByText('No runs')).toBeVisible();
  });

  // ── 4. Profile instant run button triggers a run ────────────────────────────

  test('Profile instant run button triggers a run', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE],
      runs: { runs: RUNS },
      profiles: [PROFILE_WITH_RUNS],
      triggerProfile: true,
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Set up a listener for the profile-bound trigger request BEFORE clicking.
    const triggerRequest = page.waitForRequest(
      (req) =>
        req.url().includes('/profiles/prof-001/trigger') && req.method() === 'POST',
      { timeout: 3000 },
    );

    // Locate the instant-run button for the profile "Smoke Tests".
    // The profile name span is inside nestedProfileInfo, whose parent is
    // nestedProfileMainRow. The first button in that row is the play button.
    const profileName = page.getByText('Smoke Tests', { exact: true }).first();
    const profileMainRow = profileName.locator('..').locator('..');
    const runButton = profileMainRow.locator('button').first();
    await runButton.click();

    const request = await triggerRequest;
    expect(request.method()).toBe('POST');
  });

  // ── 5. Delete profile removes it from sidebar ────────────────────────────────

  test('Delete profile removes it from sidebar', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE],
      runs: { runs: [] },
      profiles: [PROFILE_WITH_RUNS],
      profilesDelete: true,
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    // Verify the profile is initially visible
    const profileText = page.getByText('Smoke Tests', { exact: true }).first();
    await expect(profileText).toBeVisible();

    // Register dialog handler BEFORE clicking delete — accepts the confirm()
    page.on('dialog', async (dialog) => {
      await dialog.accept();
    });

    // Locate the delete button (last action button) for the profile.
    // Same traversal: profile name span -> nestedProfileMainRow.
    // The last button in the row is the delete button (type="danger").
    const profileMainRow = profileText.locator('..').locator('..');
    const deleteButton = profileMainRow.locator('button').last();
    await deleteButton.click();

    // After deletion the profile should disappear from the sidebar
    await expect(profileText).not.toBeVisible({ timeout: 5000 });
  });

  // ── 6. Suite update (git pull) button triggers API call ──────────────────────

  test('Suite update (git pull) button triggers API call', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE, LOCAL_SUITE],
      runs: { runs: [] },
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    page.on('dialog', async (dialog) => {
      await dialog.accept();
    });

    const postResponse = page.waitForResponse(
      (resp) => resp.url().includes(`/tests/${encodeURIComponent(SUITE_A)}/pull`) && resp.request().method() === 'POST',
    );

    await page.getByText(SUITE_A, { exact: true }).first().hover();
    await page.getByTestId(`suite-update-${SUITE_A}`).click();

    const response = await postResponse;
    expect(response.ok()).toBeTruthy();
  });

  // ── 7. Suite prepare (npm ci) button triggers API call ────────────────────

  test('Suite prepare (npm ci) button triggers API call', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE, LOCAL_SUITE],
      runs: { runs: [] },
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    page.on('dialog', async (dialog) => {
      await dialog.accept();
    });

    const postResponse = page.waitForResponse(
      (resp) => resp.url().includes(`/tests/${encodeURIComponent(SUITE_A)}/prepare`) && resp.request().method() === 'POST',
    );

    await page.getByText(SUITE_A, { exact: true }).first().hover();
    await page.getByTestId(`suite-prepare-${SUITE_A}`).click();

    const response = await postResponse;
    expect(response.ok()).toBeTruthy();
  });

  // ── 8. Suite delete button removes suite ──────────────────────────────

  test('Suite delete button removes suite', async ({ page }) => {
    let suitesData = [GIT_SUITE];

    await page.route('**/suites', async (route) => {
      await route.fulfill({ json: suitesData });
    });
    await page.route('**/runs', (route) => route.fulfill({ json: { runs: [] } }));
    await page.route('**/profiles', (route) => route.fulfill({ json: [] }));
    await page.route('**/schedules', (route) => route.fulfill({ json: [] }));
    await page.route('**/credentials', (route) => route.fulfill({ json: { credentials: [] } }));
    await page.route('**/runs/trend*', (route) =>
      route.fulfill({ json: { tests_path: '', points: [] } }),
    );

    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    await page.route(`**/tests/${encodeURIComponent(SUITE_A)}`, async (route) => {
      if (route.request().method() === 'DELETE') {
        suitesData = [];
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Removed' }) });
      } else {
        await route.fallback();
      }
    });

    page.on('dialog', async (dialog) => {
      await dialog.accept();
    });

    await expect(page.getByText(SUITE_A, { exact: true }).first()).toBeVisible();
    await page.getByText(SUITE_A, { exact: true }).first().hover();
    await page.getByTestId(`suite-remove-${SUITE_A}`).click();

    await expect(page.getByText(SUITE_A, { exact: true })).not.toBeVisible({ timeout: 5000 });
  });

  // ── 9. Quick-trigger button on suite row opens trigger modal ──────────

  test('Quick-trigger button on suite row opens trigger modal', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE],
      runs: { runs: [] },
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    await page.getByText(SUITE_A, { exact: true }).first().hover();

    const suiteRow = page.getByText(SUITE_A, { exact: true }).first().locator('..').locator('..');
    const playButton = suiteRow.locator('button').first();
    await playButton.click();

    await expect(page.getByTestId('trigger-modal')).toBeVisible();
    await expect(page.getByTestId('trigger-modal')).toContainText(SUITE_A);
  });

  // ── 10. History dot click opens run details drawer ─────────────────

  test('History dot click opens run details drawer', async ({ page }) => {
    await mockBackend(page, {
      suites: [GIT_SUITE],
      runs: { runs: RUNS },
      profiles: [PROFILE_WITH_RUNS],
    });
    await openDashboard(page);
    await expect(page.getByTestId('execution-records-title')).toBeVisible();

    const profileText = page.getByText('Smoke Tests', { exact: true }).first();
    await expect(profileText).toBeVisible();

    const container = profileText.locator('..').locator('..').locator('..');
    const clickableDots = container.locator('span[role="button"]');
    await expect(clickableDots).toHaveCount(1);

    await clickableDots.first().click();

    await expect(page.locator('#run-details-title')).toBeVisible();
  });
});
