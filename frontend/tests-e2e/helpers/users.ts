import { expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';

import { createUser, deleteUser, loginAndGetContext } from './api';

export function uniqueUsername(testInfo: TestInfo, prefix: string): string {
  return `${prefix}_${testInfo.workerIndex}_${Date.now()}`;
}

export async function openAdminDashboard(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
  await expect(page.getByTestId('profile-role')).toHaveText('admin');
}

export async function openUserModal(page: Page): Promise<void> {
  await page.getByTestId('open-users-button').click();
  await expect(page.getByTestId('user-modal')).toBeAttached({ timeout: 10_000 });
  await expect(page.getByTestId('user-new-username')).toBeVisible({ timeout: 5_000 });
}

export async function loginThroughUi(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  await page.goto('/');
  await page.getByTestId('login-username').fill(username);
  await page.getByTestId('login-password').fill(password);
  await page.getByTestId('login-submit').click();
}

export class UserTestData {
  readonly usernames: string[] = [];

  private constructor(readonly adminCtx: APIRequestContext) {}

  static async create(): Promise<UserTestData> {
    return new UserTestData(await loginAndGetContext('admin'));
  }

  track(username: string): void {
    if (!this.usernames.includes(username)) this.usernames.push(username);
  }

  async createUser(username: string, password: string, role: 'admin' | 'user' = 'user') {
    this.track(username);
    await createUser(this.adminCtx, { username, password, role });
  }

  async dispose(): Promise<void> {
    for (const username of [...this.usernames].reverse()) {
      await deleteUser(this.adminCtx, username);
    }
    await this.adminCtx.dispose();
  }
}
