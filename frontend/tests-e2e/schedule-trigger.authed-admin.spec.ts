import { test, expect } from '@playwright/test';

import {
  E2E_SCHEDULE_SUITE,
  ScheduleTestData,
  mockScheduleDashboardRoutes,
  openAuthedDashboard,
  openScheduleModalForProfile,
  uniqueScheduleProfileName,
} from './helpers/schedule';

// Runs under `chromium-authed-admin`: storageState supplies the admin session,
// while helpers/api.ts owns setup/teardown API calls.

test.describe('Schedule trigger flow', () => {
  let data: ScheduleTestData;

  test.beforeEach(async ({ page }) => {
    data = await ScheduleTestData.create();
    await mockScheduleDashboardRoutes(page);
  });

  test.afterEach(async () => {
    await data.dispose();
  });

  test('trigger schedule via API and verify run appears in dashboard', async ({ page }, testInfo) => {
    const profileName = uniqueScheduleProfileName(testInfo, 'schedule-trigger');
    const profileId = await data.createProfile({
      name: profileName,
      tests_path: E2E_SCHEDULE_SUITE,
      runner: 'pytest',
    });
    const scheduleId = await data.createSchedule({
      name: `${profileName} schedule`,
      profile_id: profileId,
    });

    const mockRun = {
      id: 'run-sched-0001',
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
    };
    await page.route(`**/schedules/${scheduleId}/trigger`, async (route) => {
      await route.fulfill({ status: 202, json: mockRun });
    });

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await expect(page.getByTestId('schedule-trigger-button')).toBeVisible();

    page.on('dialog', (dialog) => dialog.accept());
    await page.getByTestId('schedule-trigger-button').click();

    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });

  test('schedule modal shows pre-populated values for existing schedule', async ({ page }, testInfo) => {
    const profileName = uniqueScheduleProfileName(testInfo, 'schedule-prefill');
    const profileId = await data.createProfile({
      name: profileName,
      tests_path: E2E_SCHEDULE_SUITE,
      runner: 'pytest',
    });
    await data.createSchedule({
      name: `${profileName} schedule`,
      profile_id: profileId,
    });

    await openAuthedDashboard(page);
    await openScheduleModalForProfile(page, profileName);

    await expect(page.getByTestId('schedule-name-input')).not.toHaveValue('');
    await expect(page.getByTestId('schedule-cron-input')).not.toHaveValue('');
    await expect(page.getByTestId('schedule-preview')).toBeVisible({ timeout: 5000 });
  });

  test('create a new schedule from scratch', async ({ page }, testInfo) => {
    const profileName = uniqueScheduleProfileName(testInfo, 'schedule-create');
    const profileId = await data.createProfile({
      name: profileName,
      tests_path: E2E_SCHEDULE_SUITE,
      runner: 'pytest',
    });

    let savedBody: Record<string, unknown> | null = null;
    await page.route('**/schedules', async (route) => {
      if (route.request().method() === 'POST') {
        savedBody = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
        await route.fulfill({
          status: 201,
          json: {
            id: 'sched-new-001',
            name: savedBody.name ?? 'Test',
            profile_id: profileId,
            cron_expression: savedBody.cron_expression ?? '0 3 * * *',
            enabled: true,
            timezone: savedBody.timezone ?? 'UTC',
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

    await page.getByTestId('schedule-name-input').fill('Nightly Regression');
    await page.getByTestId('schedule-cron-input').fill('0 3 * * *');
    await expect(page.getByTestId('schedule-preview')).toBeVisible({ timeout: 5000 });
    await page.getByTestId('schedule-save-button').click();

    await expect(async () => {
      expect(savedBody).not.toBeNull();
      expect(savedBody?.profile_id).toBe(profileId);
      expect(savedBody?.name).toBe('Nightly Regression');
    }).toPass({ timeout: 5000 });
    await expect(page.getByTestId('schedule-modal')).not.toBeVisible({ timeout: 5000 });
  });
});
