import { test, expect } from '@playwright/test';

import {
  E2E_SCHEDULE_SUITE,
  ScheduleTestData,
  mockScheduleDashboardRoutes,
  openAuthedDashboard,
  openScheduleModalForProfile,
  uniqueScheduleProfileName,
} from './helpers/schedule';

// Schedule management E2E tests — covers the full schedule lifecycle under the
// `chromium-authed-admin` project. API setup/teardown is centralized in helpers.

test.describe('Schedule Management', () => {
  let data: ScheduleTestData;

  test.beforeEach(async ({ page }) => {
    data = await ScheduleTestData.create();
    await mockScheduleDashboardRoutes(page);
  });

  test.afterEach(async () => {
    await data.dispose();
  });

  async function createProfileForTest(testInfo: import('@playwright/test').TestInfo) {
    const profileName = uniqueScheduleProfileName(testInfo, 'schedule-mgmt');
    const profileId = await data.createProfile({
      name: profileName,
      tests_path: E2E_SCHEDULE_SUITE,
      runner: 'pytest',
    });
    return { profileId, profileName };
  }

  test('Schedule modal opens from profile row in sidebar', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await expect(page.getByTestId('schedule-name-input')).toBeVisible();
    await expect(page.getByTestId('schedule-cron-input')).toBeVisible();
    await expect(page.getByTestId('schedule-timezone-select')).toBeVisible();
    await expect(page.getByTestId('schedule-save-button')).toBeVisible();
  });

  test('Valid cron expression shows preview of next 5 fire times', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');
    await expect(page.getByTestId('schedule-preview')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('schedule-preview').locator('li')).toHaveCount(5);
  });

  test('Invalid cron expression shows error', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-cron-input').fill('invalid cron');
    await expect(page.getByTestId('schedule-preview-error')).toBeVisible({ timeout: 5000 });
  });

  test('Save schedule creates a new schedule and closes modal', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-name-input').fill('Nightly Regression');
    await page.getByTestId('schedule-cron-input').fill('0 3 * * *');
    await page.getByTestId('schedule-save-button').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('Delete existing schedule removes it', async ({ page }, testInfo) => {
    const { profileId, profileName } = await createProfileForTest(testInfo);
    await data.createSchedule({
      name: `${profileName} schedule`,
      profile_id: profileId,
    });

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    page.on('dialog', (dialog) => dialog.accept());
    await expect(page.getByTestId('schedule-delete-button')).toBeVisible();
    await page.getByTestId('schedule-delete-button').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('Trigger schedule immediately creates a run', async ({ page }, testInfo) => {
    const { profileId, profileName } = await createProfileForTest(testInfo);
    const scheduleId = await data.createSchedule({
      name: `${profileName} schedule`,
      profile_id: profileId,
    });
    await page.route(`**/schedules/${scheduleId}/trigger`, async (route) => {
      await route.fulfill({
        status: 202,
        json: {
          id: 'run-sched-trigger-001',
          status: 'queued',
          runner: 'pytest',
          created_by: 'admin',
          tests_path: E2E_SCHEDULE_SUITE,
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
        },
      });
    });

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    page.on('dialog', (dialog) => dialog.accept());
    await expect(page.getByTestId('schedule-trigger-button')).toBeVisible();
    await page.getByTestId('schedule-trigger-button').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('Timezone selector changes preview timestamps', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');
    await expect(page.getByTestId('schedule-preview')).toBeVisible({ timeout: 5000 });
    const initialPreviewText = await page.getByTestId('schedule-preview').innerText();

    await page.getByTestId('schedule-timezone-select').click();
    await page.getByText('Asia/Shanghai').click();

    await expect(async () => {
      const currentText = await page.getByTestId('schedule-preview').innerText();
      expect(currentText).not.toBe(initialPreviewText);
    }).toPass({ timeout: 5000 });
  });

  test('Close schedule modal via X button', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.locator('.semi-modal-close').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeAttached();
  });

  test('Close schedule modal via Escape key', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.keyboard.press('Escape');

    await expect(page.getByTestId('schedule-modal')).not.toBeAttached();
  });

  test('Close schedule modal via mask click', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.locator('.semi-modal-wrap').click({ position: { x: 10, y: 10 } });

    await expect(page.getByTestId('schedule-modal')).not.toBeAttached();
  });

  test('Save with empty schedule name uses the submitted value', async ({ page }, testInfo) => {
    const { profileId, profileName } = await createProfileForTest(testInfo);
    let capturedBody: Record<string, unknown> | null = null;
    await page.route('**/schedules', async (route) => {
      if (route.request().method() === 'POST') {
        capturedBody = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
        await route.fulfill({
          status: 201,
          json: {
            id: 'sched-empty-name',
            name: capturedBody.name,
            profile_id: profileId,
            cron_expression: capturedBody.cron_expression,
            enabled: capturedBody.enabled,
            timezone: capturedBody.timezone,
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

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-name-input').clear();
    await page.getByTestId('schedule-save-button').click();

    await expect(async () => {
      expect(capturedBody).not.toBeNull();
      expect(capturedBody?.name).toBe('');
    }).toPass({ timeout: 5000 });
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('Save with empty cron expression shows validation', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-name-input').fill('Nightly Regression');
    await page.getByTestId('schedule-cron-input').clear();
    await page.getByTestId('schedule-save-button').click();

    await expect(page.getByTestId('schedule-preview-error')).toBeVisible({ timeout: 5000 });
  });

  test('Schedule enable/disable toggle works', async ({ page }, testInfo) => {
    const { profileId, profileName } = await createProfileForTest(testInfo);
    const scheduleId = await data.createSchedule({
      name: `${profileName} schedule`,
      profile_id: profileId,
    });

    let capturedBody: string | null = null;
    await page.route(`**/schedules/${scheduleId}`, async (route, request) => {
      if (request.method() === 'PUT') {
        capturedBody = request.postData();
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      } else {
        await route.continue();
      }
    });

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-modal').getByText(/Enabled|启用/).click();
    await page.getByTestId('schedule-save-button').click();

    await expect(async () => {
      expect(capturedBody).not.toBeNull();
      const parsed = JSON.parse(capturedBody!);
      expect(parsed.enabled).toBe(false);
    }).toPass({ timeout: 5000 });
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });
});
