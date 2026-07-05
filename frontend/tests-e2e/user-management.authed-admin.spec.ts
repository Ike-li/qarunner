import { test, expect } from '@playwright/test';

import {
  UserTestData,
  openAdminDashboard,
  openUserModal,
  uniqueUsername,
} from './helpers/users';

// User management E2E tests — runs under `chromium-authed-admin`, so admin auth
// comes from storageState instead of inline UI login.

test.describe('User management — Admin operations', () => {
  let data: UserTestData;

  test.beforeEach(async () => {
    data = await UserTestData.create();
  });

  test.afterEach(async () => {
    await data.dispose();
  });

  test('opens user management modal', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await expect(page.getByText('User Management')).toBeVisible();
    await expect(page.getByTestId('user-row-username').filter({ hasText: 'admin' })).toBeVisible();
  });

  test('creates a new user', async ({ page }, testInfo) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    const uniqueUser = uniqueUsername(testInfo, 'testuser');
    data.track(uniqueUser);
    await page.getByTestId('user-new-username').fill(uniqueUser);
    await page.getByTestId('user-new-password').fill('TestPass123!');
    await page.getByTestId('user-add-submit').click();

    await expect(page.getByTestId('user-row-username').filter({ hasText: uniqueUser })).toBeVisible({
      timeout: 5000,
    });
  });

  test('toggles user role', async ({ page }, testInfo) => {
    const tmpUser = uniqueUsername(testInfo, 'tmp_toggle');
    await data.createUser(tmpUser, 'TmpPass123!', 'user');

    await openAdminDashboard(page);
    await openUserModal(page);

    const userRow = page.locator('tr').filter({ hasText: tmpUser });
    await expect(userRow.getByTestId('user-toggle-role')).toBeVisible();
    await userRow.getByTestId('user-toggle-role').click();
    await expect(userRow.getByTestId('user-toggle-role')).toBeVisible();
  });

  test('changes user password', async ({ page }, testInfo) => {
    const tmpUser = uniqueUsername(testInfo, 'tmp_password');
    await data.createUser(tmpUser, 'OldPass123!', 'user');

    await openAdminDashboard(page);
    await openUserModal(page);

    page.on('dialog', (dialog) => dialog.accept('NewPass123!'));
    const userRow = page.locator('tr').filter({ hasText: tmpUser });
    await userRow.getByTestId('user-change-password').click();
    await expect(userRow.getByTestId('user-change-password')).toBeVisible();
  });

  test('deletes a non-admin user', async ({ page }, testInfo) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    const uniqueUser = uniqueUsername(testInfo, 'delete_me');
    await page.getByTestId('user-new-username').fill(uniqueUser);
    await page.getByTestId('user-new-password').fill('DeletePass123!');
    await page.getByTestId('user-add-submit').click();

    await expect(page.getByTestId('user-row-username').filter({ hasText: uniqueUser })).toBeVisible({
      timeout: 5000,
    });

    page.on('dialog', (dialog) => dialog.accept());
    const userRow = page.locator('tr').filter({ hasText: uniqueUser });
    await userRow.getByTestId('user-delete').click();

    await expect(page.getByTestId('user-row-username').filter({ hasText: uniqueUser })).not.toBeVisible({
      timeout: 5000,
    });
  });

  test('closes user management modal', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await page.keyboard.press('Escape');

    await expect(page.getByText('User Management')).not.toBeVisible();
  });
});
