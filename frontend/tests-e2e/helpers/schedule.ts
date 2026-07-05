import { expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';

import {
  createProfile,
  createSchedule,
  deleteProfile,
  deleteSchedule,
  loginAndGetContext,
  type CreateProfileInput,
  type CreateScheduleInput,
} from './api';

export const E2E_SCHEDULE_SUITE = 'e2e_suite/';

export async function mockScheduleDashboardRoutes(
  page: Page,
  suiteName = E2E_SCHEDULE_SUITE,
): Promise<void> {
  await page.route('**/suites', async (route) => {
    await route.fulfill({
      json: [{ name: suiteName, source: 'local', repo_url: null, ref: null }],
    });
  });
  await page.route('**/runs', async (route) => {
    await route.fulfill({ json: { runs: [] } });
  });
}

export async function openAuthedDashboard(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

export function uniqueScheduleProfileName(testInfo: TestInfo, prefix: string): string {
  return `${prefix}-${testInfo.workerIndex}-${Date.now()}`;
}

export async function openScheduleModalForProfile(
  page: Page,
  profileName: string,
): Promise<void> {
  const profileItem = page
    .getByText(profileName, { exact: true })
    .locator('xpath=ancestor::div[contains(@class, "nestedProfileItem")]')
    .first();
  await expect(profileItem).toBeVisible({ timeout: 10_000 });
  const scheduleButton = profileItem.getByTestId('open-schedule-button');
  await expect(scheduleButton).toBeVisible({ timeout: 10_000 });
  await scheduleButton.click();
  await expect(page.getByTestId('schedule-modal')).toBeAttached();
}

export class ScheduleTestData {
  readonly profileIds: string[] = [];
  readonly scheduleIds: string[] = [];

  private constructor(readonly adminCtx: APIRequestContext) {}

  static async create(): Promise<ScheduleTestData> {
    return new ScheduleTestData(await loginAndGetContext('admin'));
  }

  async createProfile(input: CreateProfileInput): Promise<string> {
    const id = await createProfile(this.adminCtx, input);
    this.profileIds.push(id);
    return id;
  }

  async createSchedule(input: CreateScheduleInput): Promise<string> {
    const id = await createSchedule(this.adminCtx, input);
    this.scheduleIds.push(id);
    return id;
  }

  async dispose(): Promise<void> {
    for (const id of [...this.scheduleIds].reverse()) {
      await deleteSchedule(this.adminCtx, id);
    }
    for (const id of [...this.profileIds].reverse()) {
      await deleteProfile(this.adminCtx, id);
    }
    await this.adminCtx.dispose();
  }
}
