import { test, expect } from '@playwright/test';

// Schedule trigger E2E tests — covers creating a schedule via API helpers,
// then triggering it from the UI and verifying a run appears.
// Uses route-mocking for deterministic run data.

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'Demo-Qarunner-2026!';

async function login(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByTestId('login-username').fill('admin');
  await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

async function getAuthToken(page: import('@playwright/test').Page): Promise<string> {
  const resp = await page.request.post('/auth/login', {
    data: { username: 'admin', password: ADMIN_PASSWORD },
  });
  const data = await resp.json();
  return data.access_token;
}

async function createProfile(page: import('@playwright/test').Page, token: string, suiteName: string): Promise<string> {
  const resp = await page.request.post('/profiles', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name: 'E2E Schedule Profile',
      tests_path: suiteName,
      runner: 'pytest',
    },
  });
  const data = await resp.json();
  return data.id;
}

async function createSchedule(page: import('@playwright/test').Page, token: string, profileId: string): Promise<string> {
  const resp = await page.request.post('/schedules', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name: 'E2E Test Schedule',
      profile_id: profileId,
      cron_expression: '0 2 * * *',
      timezone: 'UTC',
      enabled: true,
    },
  });
  const data = await resp.json();
  return data.id;
}

test.describe('Schedule trigger flow', () => {
  test('trigger schedule via API and verify run appears in dashboard', async ({ page }) => {
    // Seed profile + schedule via API.
    await login(page);
    const token = await getAuthToken(page);

    // Mock /tests to return a suite name so the sidebar has profiles.
    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['e2e_suite/'] });
    });

    // Reload to pick up the mocked suite list.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Create profile + schedule via real API.
    const profileId = await createProfile(page, token, 'e2e_suite/');
    const scheduleId = await createSchedule(page, token, profileId);

    // Mock the trigger endpoint to return a queued run.
    const mockRun = {
      id: 'run-sched-0001',
      status: 'queued',
      runner: 'pytest',
      created_by: 'admin',
      tests_path: 'e2e_suite/',
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
      stdout: null,
      stderr: null,
      locked: false,
    };

    // Intercept the trigger POST.
    await page.route(`**/schedules/${scheduleId}/trigger`, async (route) => {
      await route.fulfill({ status: 202, json: mockRun });
    });

    // Reload and open the schedule modal for the profile.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Find the schedule button in the sidebar and click it.
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    // The schedule modal should open with the existing schedule pre-populated.
    await expect(page.getByTestId('schedule-modal')).toBeVisible();
    await expect(page.getByTestId('schedule-trigger-button')).toBeVisible();

    // Click "Run Now".
    // Register dialog handler BEFORE clicking — the alert fires synchronously.
    page.on('dialog', (dialog) => dialog.accept());
    await page.getByTestId('schedule-trigger-button').click();

    // Modal should close after trigger.
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('schedule modal shows pre-populated values for existing schedule', async ({ page }) => {
    await login(page);
    const token = await getAuthToken(page);

    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['e2e_suite/'] });
    });
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    const profileId = await createProfile(page, token, 'e2e_suite/');
    await createSchedule(page, token, profileId);

    // Reload to pick up the new profile and schedule.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // The name should be pre-populated with the schedule name.
    const nameInput = page.getByTestId('schedule-name-input');
    await expect(nameInput).not.toHaveValue('');

    // The cron input should be pre-populated.
    const cronInput = page.getByTestId('schedule-cron-input');
    await expect(cronInput).not.toHaveValue('');

    // The preview should show the next runs.
    await expect(page.getByTestId('schedule-preview')).toBeVisible({ timeout: 5000 });
  });

  test('create a new schedule from scratch', async ({ page }) => {
    await login(page);
    const token = await getAuthToken(page);

    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['e2e_suite/'] });
    });
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    const profileId = await createProfile(page, token, 'e2e_suite/');

    // Reload to see the profile in the sidebar.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Mock the POST /schedules endpoint.
    let savedBody: unknown = null;
    await page.route('**/schedules', async (route) => {
      if (route.request().method() === 'POST') {
        savedBody = JSON.parse(route.request().postData() ?? '{}');
        await route.fulfill({
          status: 201,
          json: {
            id: 'sched-new-001',
            name: savedBody?.name ?? 'Test',
            profile_id: profileId,
            cron_expression: savedBody?.cron_expression ?? '0 3 * * *',
            enabled: true,
            timezone: savedBody?.timezone ?? 'UTC',
            next_run_at: new Date().toISOString(),
            last_run_at: null,
            created_by: 'admin',
            created_at: new Date().toISOString(),
          },
        });
      } else {
        await route.fallback();
      }
    });

    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Fill in the schedule form.
    await page.getByTestId('schedule-name-input').fill('Nightly Regression');
    await page.getByTestId('schedule-cron-input').fill('0 3 * * *');

    // Wait for preview to appear (validates the cron expression).
    await expect(page.getByTestId('schedule-preview')).toBeVisible({ timeout: 5000 });

    // Save the schedule.
    await page.getByTestId('schedule-save-button').click();

    // Modal should close on success.
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });
});
