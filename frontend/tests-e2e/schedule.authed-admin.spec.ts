import { test, expect } from '@playwright/test';

import {
  E2E_SCHEDULE_SUITE,
  ScheduleTestData,
  mockScheduleDashboardRoutes,
  openAuthedDashboard,
  openScheduleModalForProfile,
  uniqueScheduleProfileName,
} from './helpers/schedule';

// Schedule management E2E tests — runs under `chromium-authed-admin` and uses
// shared setup helpers instead of inline UI login/API copies.

test.describe('Schedule management — Schedule modal', () => {
  let data: ScheduleTestData;

  test.beforeEach(async ({ page }) => {
    data = await ScheduleTestData.create();
    await mockScheduleDashboardRoutes(page);
  });

  test.afterEach(async () => {
    await data.dispose();
  });

  async function createProfileForTest(testInfo: import('@playwright/test').TestInfo) {
    const profileName = uniqueScheduleProfileName(testInfo, 'schedule-basic');
    const profileId = await data.createProfile({
      name: profileName,
      tests_path: E2E_SCHEDULE_SUITE,
      runner: 'pytest',
    });
    return { profileId, profileName };
  }

  test('opens schedule modal from profile sidebar', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await expect(page.getByTestId('schedule-name-input')).toBeVisible();
    await expect(page.getByTestId('schedule-cron-input')).toBeVisible();
    await expect(page.getByTestId('schedule-timezone-select')).toBeVisible();
    await expect(page.getByTestId('schedule-save-button')).toBeVisible();
  });

  test('validates cron expression and shows preview', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');
    await expect(page.getByTestId('schedule-preview')).toBeVisible();

    const previewItems = page.getByTestId('schedule-preview').locator('li');
    await expect(previewItems).toHaveCount(5);
  });

  test('shows error for invalid cron expression', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-cron-input').fill('invalid cron');
    await expect(page.getByTestId('schedule-preview-error')).toBeVisible();
  });

  test('saves a new schedule', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-name-input').fill('Test Schedule');
    await page.getByTestId('schedule-cron-input').fill('0 3 * * *');
    await page.getByTestId('schedule-save-button').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeVisible();
  });

  test('triggers schedule immediately', async ({ page }, testInfo) => {
    const { profileId, profileName } = await createProfileForTest(testInfo);
    const scheduleId = await data.createSchedule({
      name: `${profileName} schedule`,
      profile_id: profileId,
    });
    await page.route(`**/schedules/${scheduleId}/trigger`, async (route) => {
      await route.fulfill({
        status: 202,
        json: {
          id: 'run-sched-basic-001',
          status: 'queued',
          runner: 'pytest',
          tests_path: E2E_SCHEDULE_SUITE,
          created_by: 'admin',
          created_at: new Date().toISOString(),
        },
      });
    });

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    page.on('dialog', (dialog) => dialog.accept());
    await page.getByTestId('schedule-trigger-button').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('deletes an existing schedule', async ({ page }, testInfo) => {
    const { profileId, profileName } = await createProfileForTest(testInfo);
    await data.createSchedule({
      name: `${profileName} schedule`,
      profile_id: profileId,
    });

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    page.on('dialog', (dialog) => dialog.accept());
    await page.getByTestId('schedule-delete-button').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('changes timezone and updates preview', async ({ page }, testInfo) => {
    const { profileName } = await createProfileForTest(testInfo);
    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await page.getByTestId('schedule-cron-input').fill('0 2 * * *');
    await expect(page.getByTestId('schedule-preview')).toBeVisible();

    await page.getByTestId('schedule-timezone-select').click();
    await page.getByText('Asia/Shanghai').click();

    await expect(page.getByTestId('schedule-preview')).toBeVisible();
  });
});
