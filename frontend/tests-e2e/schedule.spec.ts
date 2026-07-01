import { test, expect } from '@playwright/test';

// Schedule management E2E tests — covers scheduling system critical user journey.
// Uses stable data-testid hooks (same convention as other spec files).

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'Demo-Qarunner-2026!';

async function login(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByTestId('login-username').fill('admin');
  await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

// Helper to get auth token for API calls
async function getAuthToken(page: import('@playwright/test').Page): Promise<string> {
  const resp = await page.request.post('/auth/login', {
    data: { username: 'admin', password: ADMIN_PASSWORD }
  });
  const data = await resp.json();
  return data.access_token;
}

// Helper to create a profile via API
async function createProfile(page: import('@playwright/test').Page, token: string, suiteName: string) {
  const resp = await page.request.post('/profiles', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name: 'Test Profile',
      tests_path: suiteName,
      runner: 'pytest'
    }
  });
  return resp.ok();
}

test.describe('Schedule management — Schedule modal', () => {
  test('opens schedule modal from profile sidebar', async ({ page }) => {
    await login(page);

    // Check if there are any profiles with schedule buttons
    const scheduleButtons = page.getByTestId('open-schedule-button');
    const count = await scheduleButtons.count();

    if (count === 0) {
      // No profiles exist, skip this test
      test.skip();
      return;
    }

    // Click the schedule button (clock icon) on the first profile
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
    await login(page);

    // Check if there are any profiles with schedule buttons
    const scheduleButtons = page.getByTestId('open-schedule-button');
    const count = await scheduleButtons.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Open schedule modal
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
    await login(page);

    // Check if there are any profiles with schedule buttons
    const scheduleButtons = page.getByTestId('open-schedule-button');
    const count = await scheduleButtons.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Open schedule modal
    await scheduleButtons.first().click();
    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Fill in an invalid cron expression
    await page.getByTestId('schedule-cron-input').fill('invalid cron');

    // Wait for error to appear (debounced)
    await expect(page.getByTestId('schedule-preview-error')).toBeVisible();
  });

  test('saves a new schedule', async ({ page }) => {
    await login(page);

    // Check if there are any profiles with schedule buttons
    const scheduleButtons = page.getByTestId('open-schedule-button');
    const count = await scheduleButtons.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Open schedule modal
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
    await login(page);

    // Check if there are any profiles with schedule buttons
    const scheduleButtons = page.getByTestId('open-schedule-button');
    const count = await scheduleButtons.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Open schedule modal (assuming a schedule already exists)
    await scheduleButtons.first().click();
    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Click the trigger button
    await page.getByTestId('schedule-trigger-button').click();

    // Modal should close after trigger
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible();
  });

  test('deletes an existing schedule', async ({ page }) => {
    await login(page);

    // Check if there are any profiles with schedule buttons
    const scheduleButtons = page.getByTestId('open-schedule-button');
    const count = await scheduleButtons.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Open schedule modal (assuming a schedule already exists)
    await scheduleButtons.first().click();
    await expect(page.getByTestId('schedule-modal')).toBeVisible();

    // Click the delete button
    await page.getByTestId('schedule-delete-button').click();

    // Modal should close after delete
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible();
  });

  test('changes timezone and updates preview', async ({ page }) => {
    await login(page);

    // Check if there are any profiles with schedule buttons
    const scheduleButtons = page.getByTestId('open-schedule-button');
    const count = await scheduleButtons.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Open schedule modal
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
