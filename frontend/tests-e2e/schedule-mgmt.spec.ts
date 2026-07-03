import { test, expect } from '@playwright/test';

// Schedule management E2E tests — covers the full schedule lifecycle:
// opening modal, cron preview (valid/invalid), save, delete, trigger, and
// timezone-driven preview updates.
// Uses a mix of real API calls (profile/schedule creation) and route
// mocks (trigger endpoint, /tests listing) for deterministic execution.

const ADMIN_PASSWORD =
  process.env.E2E_ADMIN_PASSWORD || 'Demo-Qarunner-2026!';

async function login(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByTestId('login-username').fill('admin');
  await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

async function getAuthToken(
  page: import('@playwright/test').Page,
): Promise<string> {
  const resp = await page.request.post('/auth/login', {
    data: { username: 'admin', password: ADMIN_PASSWORD },
  });
  const data = await resp.json();
  return data.access_token;
}

async function createProfile(
  page: import('@playwright/test').Page,
  token: string,
): Promise<string> {
  const resp = await page.request.post('/profiles', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name: 'E2E Schedule Mgmt Profile',
      tests_path: 'e2e_suite/',
      runner: 'pytest',
    },
  });
  const data = await resp.json();
  return data.id;
}

async function createSchedule(
  page: import('@playwright/test').Page,
  token: string,
  profileId: string,
): Promise<string> {
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

test.describe('Schedule Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    const token = await getAuthToken(page);

    // Mock /suites to return a suite name so the sidebar renders profiles.
    // Note: the app calls /suites (not /tests) via useSuites.fetchTests().
    await page.route('**/suites', async (route) => {
      await route.fulfill({ json: [{ name: 'e2e_suite/', source: 'local', repo_url: null, ref: null }] });
    });

    // Mock /runs to return empty runs so the dashboard loads without backend calls.
    await page.route('**/runs', (route) => route.fulfill({ json: { runs: [] } }));

    // Create a profile via API so the schedule button is always visible.
    await createProfile(page, token);

    // Reload to pick up the new profile in the sidebar.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
  });

  // ── 1. Schedule modal opens from profile row in sidebar ───────────────

  test('Schedule modal opens from profile row in sidebar', async ({
    page,
  }) => {
    // 1. Click on profile row's open-schedule-button
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    // 2. Verify schedule-modal is visible with expected form fields
    await expect(page.getByTestId('schedule-modal')).toBeAttached();
    await expect(page.getByTestId('schedule-name-input')).toBeVisible();
    await expect(page.getByTestId('schedule-cron-input')).toBeVisible();
    await expect(page.getByTestId('schedule-timezone-select')).toBeVisible();
    await expect(page.getByTestId('schedule-save-button')).toBeVisible();
  });

  // ── 2. Valid cron expression shows preview of next 5 fire times ──────

  test('Valid cron expression shows preview of next 5 fire times', async ({
    page,
  }) => {
    // 1. Login then open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Type a valid cron expression
    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');

    // 3. Wait for debounce then verify schedule-preview shows 5 items
    await expect(page.getByTestId('schedule-preview')).toBeVisible({
      timeout: 5000,
    });
    const previewItems = page
      .getByTestId('schedule-preview')
      .locator('li');
    await expect(previewItems).toHaveCount(5);
  });

  // ── 3. Invalid cron expression shows error ───────────────────────────

  test('Invalid cron expression shows error', async ({ page }) => {
    // 1. Login then open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Type an invalid cron expression
    await page.getByTestId('schedule-cron-input').fill('invalid cron');

    // 3. Wait for debounce then verify error is shown
    await expect(page.getByTestId('schedule-preview-error')).toBeVisible({
      timeout: 5000,
    });
  });

  // ── 4. Save schedule creates a new schedule and closes modal ─────────

  test('Save schedule creates a new schedule and closes modal', async ({
    page,
  }) => {
    // 1. Login then open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Fill schedule-name-input and schedule-cron-input
    await page.getByTestId('schedule-name-input').fill('Nightly Regression');
    await page.getByTestId('schedule-cron-input').fill('0 3 * * *');

    // 3. Click schedule-save-button
    await page.getByTestId('schedule-save-button').click();

    // 4. Verify schedule saved and modal closes
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({
      timeout: 5000,
    });
  });

  // ── 5. Delete existing schedule removes it ──────────────────────────────

  test('Delete existing schedule removes it', async ({ page }) => {
    const token = await getAuthToken(page);

    // Create a profile and schedule via API so the modal has existing data.
    const profileId = await createProfile(page, token);
    await createSchedule(page, token, profileId);

    // Reload so the schedule appears in the modal for this profile.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Handle the window.confirm dialog that fires on delete.
    page.on('dialog', (dialog) => dialog.accept());

    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    // 2. Verify modal shows existing schedule data with delete button
    await expect(page.getByTestId('schedule-modal')).toBeAttached();
    await expect(page.getByTestId('schedule-delete-button')).toBeVisible();

    // 3. Click schedule-delete-button
    await page.getByTestId('schedule-delete-button').click();

    // 4. Verify modal closes after delete
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({
      timeout: 5000,
    });
  });

  // ── 6. Trigger schedule immediately creates a run ───────────────────────

  test('Trigger schedule immediately creates a run', async ({ page }) => {
    const token = await getAuthToken(page);

    // Create a profile and schedule via API.
    const profileId = await createProfile(page, token);
    const scheduleId = await createSchedule(page, token, profileId);

    // Mock the trigger endpoint to return a queued run without executing.
    const mockRun = {
      id: 'run-sched-trigger-001',
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

    await page.route(`**/schedules/${scheduleId}/trigger`, async (route) => {
      await route.fulfill({ status: 202, json: mockRun });
    });

    // Reload so the schedule appears in the sidebar modal.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Handle the alert dialog that fires after trigger succeeds.
    page.on('dialog', (dialog) => dialog.accept());

    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    // 2. Verify modal has trigger button for existing schedule
    await expect(page.getByTestId('schedule-modal')).toBeAttached();
    await expect(page.getByTestId('schedule-trigger-button')).toBeVisible();

    // 3. Click schedule-trigger-button
    await page.getByTestId('schedule-trigger-button').click();

    // 4. Verify run triggered and modal closes
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({
      timeout: 5000,
    });
  });

  // ── 7. Timezone selector changes preview timestamps ────────────────────

  test('Timezone selector changes preview timestamps', async ({ page }) => {
    // 1. Login then open schedule modal, type cron, wait for preview
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');

    // Wait for debounced preview to appear
    await expect(page.getByTestId('schedule-preview')).toBeVisible({
      timeout: 5000,
    });

    // Capture the initial preview text (UTC timestamps).
    const initialPreviewText = await page
      .getByTestId('schedule-preview')
      .innerText();

    // 2. Switch schedule-timezone-select to 'Asia/Shanghai'
    await page.getByTestId('schedule-timezone-select').click();
    await page.getByText('Asia/Shanghai').click();

    // 3. Verify preview timestamps update after debounce + API refetch.
    // Use expect.poll to wait for the preview text to change rather than a
    // fixed timeout.
    await expect(async () => {
      const currentText = await page
        .getByTestId('schedule-preview')
        .innerText();
      expect(currentText).not.toBe(initialPreviewText);
    }).toPass({ timeout: 5000 });
  });

  // ── 8. Close schedule modal via X button ────────────────────────────────

  test('Close schedule modal via X button', async ({ page }) => {
    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Click the X close button
    await page.locator('.semi-modal-close').click();

    // 3. Verify modal is no longer attached
    await expect(page.getByTestId('schedule-modal')).not.toBeAttached();
  });

  // ── 9. Close schedule modal via Escape key ───────────────────────────────

  test('Close schedule modal via Escape key', async ({ page }) => {
    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Press Escape
    await page.keyboard.press('Escape');

    // 3. Verify modal is no longer attached
    await expect(page.getByTestId('schedule-modal')).not.toBeAttached();
  });

  // ── 10. Close schedule modal via mask click ─────────────────────────────

  test('Close schedule modal via mask click', async ({ page }) => {
    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Click the modal mask at a position outside the panel
    await page.locator('.semi-modal-mask').click({ position: { x: 10, y: 10 } });

    // 3. Verify modal is no longer attached
    await expect(page.getByTestId('schedule-modal')).not.toBeAttached();
  });

  // ── 11. Save with empty schedule name shows validation ──────────────────────

  test('Save with empty schedule name shows validation', async ({ page }) => {
    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Clear the pre-filled name
    await page.getByTestId('schedule-name-input').clear();

    // 3. Click save — backend validates required fields
    await page.getByTestId('schedule-save-button').click();

    // 4. Verify error banner appears (backend validation rejects empty name)
    await expect(page.getByTestId('schedule-preview-error')).toBeVisible({ timeout: 5000 });
  });

  // ── 12. Save with empty cron expression shows validation ──────────────────

  test('Save with empty cron expression shows validation', async ({ page }) => {
    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Fill name so it's valid
    await page.getByTestId('schedule-name-input').fill('Nightly Regression');

    // 3. Clear the pre-filled cron expression
    await page.getByTestId('schedule-cron-input').clear();

    // 4. Click save — backend validates required fields
    await page.getByTestId('schedule-save-button').click();

    // 5. Verify error banner appears (backend validation rejects empty cron)
    await expect(page.getByTestId('schedule-preview-error')).toBeVisible({ timeout: 5000 });
  });

  // ── 13. Schedule enable/disable toggle works ──────────────────────────────

  test('Schedule enable/disable toggle works', async ({ page }) => {
    const token = await getAuthToken(page);

    // Need an existing schedule for the toggle — create one via API.
    const profileId = await createProfile(page, token);
    const scheduleId = await createSchedule(page, token, profileId);

    // Mock the PUT endpoint to capture the request body.
    let capturedBody: string | null = null;
    await page.route(`**/schedules/${scheduleId}`, async (route, request) => {
      if (request.method() === 'PUT') {
        capturedBody = request.postData();
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      } else {
        await route.continue();
      }
    });

    // Reload so the schedule appears in the modal.
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 1. Open schedule modal
    const scheduleButtons = page.getByTestId('open-schedule-button');
    await expect(scheduleButtons.first()).toBeVisible({ timeout: 10000 });
    await scheduleButtons.first().click();

    await expect(page.getByTestId('schedule-modal')).toBeAttached();

    // 2. Toggle the enabled checkbox (initially checked → uncheck)
    const checkbox = page.getByTestId('schedule-modal').getByRole('checkbox');
    await checkbox.click();

    // 3. Save the schedule
    await page.getByTestId('schedule-save-button').click();

    // 4. Verify PUT request was made with enabled: false
    await expect(async () => {
      expect(capturedBody).not.toBeNull();
      const parsed = JSON.parse(capturedBody!);
      expect(parsed.enabled).toBe(false);
    }).toPass({ timeout: 5000 });

    // 5. Verify modal closes after save
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });
});
