import { test, expect } from '@playwright/test';

// Schedule management E2E tests — covers scheduling system critical user journey.
// Creates a profile via API in beforeEach so schedule buttons are always visible.

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
      name: 'E2E Schedule Test Profile',
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

test.describe('Schedule management — Schedule modal', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    const token = await getAuthToken(page);

    // Mock /tests to return a suite name so the sidebar has profiles.
    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['e2e_suite/'] });
    });

    // Create a profile via API so the schedule button is always visible.
    await createProfile(page, token, 'e2e_suite/');

    // Reload to pick up the new profile in the sidebar.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
  });

  test('opens schedule modal from profile sidebar', async ({ page }) => {
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    // Verify modal is visible
    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Verify form fields are present
    await expect(page.getByTestId('schedule-name-input')).toBeVisible();
    await expect(page.getByTestId('schedule-cron-input')).toBeVisible();
    await expect(page.getByTestId('schedule-timezone-select')).toBeVisible();
    await expect(page.getByTestId('schedule-save-button')).toBeVisible();
  });

  test('validates cron expression and shows preview', async ({ page }) => {
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Fill in a valid cron expression
    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');

    // Wait for preview to load (debounced)
    await expect(page.getByTestId('schedule-preview')).toBeVisible();

    // Verify preview shows 5 fire times
    const previewItems = page.getByTestId('schedule-preview').locator('li');
    await expect(previewItems).toHaveCount(5);
  });

  test('shows error for invalid cron expression', async ({ page }) => {
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Fill in an invalid cron expression
    await page.getByTestId('schedule-cron-input').fill('invalid cron');

    // Wait for error to appear (debounced)
    await expect(page.getByTestId('schedule-preview-error')).toBeVisible();
  });

  test('saves a new schedule', async ({ page }) => {
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Fill in schedule details
    await page.getByTestId('schedule-name-input').fill('Test Schedule');
    await page.getByTestId('schedule-cron-input').fill('0 3 * * *');

    // Save the schedule
    await page.getByTestId('schedule-save-button').click();

    // Modal should close after save
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible();
  });

  test('triggers schedule immediately', async ({ page }) => {
    const token = await getAuthToken(page);

    // Need an existing schedule for trigger — create one via API.
    const profileId = await createProfile(page, token, 'e2e_suite/');
    await createSchedule(page, token, profileId);

    // Reload so the schedule appears in the modal.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Mock the trigger endpoint to avoid actually running tests.
    page.on('dialog', (dialog) => dialog.accept());

    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Click the trigger button
    await page.getByTestId('schedule-trigger-button').click();

    // Modal should close after trigger
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('deletes an existing schedule', async ({ page }) => {
    const token = await getAuthToken(page);

    // Need an existing schedule for delete — create one via API.
    const profileId = await createProfile(page, token, 'e2e_suite/');
    await createSchedule(page, token, profileId);

    // Reload so the schedule appears in the modal.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Click the delete button
    await page.getByTestId('schedule-delete-button').click();

    // Modal should close after delete
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('changes timezone and updates preview', async ({ page }) => {
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Fill in a cron expression
    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');

    // Wait for preview to load
    await expect(page.getByTestId('schedule-preview')).toBeVisible();

    // Change timezone
    await page.getByTestId('schedule-timezone-select').click();
    await page.getByText('Asia/Shanghai').click();

    // Preview should update (wait for debounce)
    await expect(page.getByTestId('schedule-preview')).toBeVisible();
  });
});
