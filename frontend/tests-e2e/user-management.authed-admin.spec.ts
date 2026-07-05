import { test, expect } from '@playwright/test';

import {
  UserTestData,
  loginThroughUi,
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

    await expect(page.getByTestId('user-modal')).toContainText('User Management');
    await expect(page.getByTestId('user-row-username').filter({ hasText: 'admin' })).toBeVisible();
    await expect(page.getByTestId('user-new-username')).toBeVisible();
    await expect(page.getByTestId('user-new-password')).toBeVisible();
    await expect(page.getByTestId('user-role-select')).toBeVisible();
    await expect(page.getByTestId('user-add-submit')).toBeVisible();
    await expect(page.getByTestId('user-modal-close')).toBeAttached();
  });

  test('non-admin user cannot see the users button', async ({ page }, testInfo) => {
    const nonAdminUser = uniqueUsername(testInfo, 'nouser');
    const password = 'TestPass123!';
    await data.createUser(nonAdminUser, password, 'user');

    await page.context().clearCookies();
    await loginThroughUi(page, nonAdminUser, password);
    await expect(page.getByTestId('profile-username')).toHaveText(nonAdminUser);

    await expect(page.getByTestId('open-users-button')).not.toBeVisible();
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

    const userActions = page.getByTestId(`user-actions-${tmpUser}`);
    await expect(userActions.getByTestId('user-toggle-role')).toBeVisible();
    await userActions.getByTestId('user-toggle-role').click();
    await expect(userActions.getByTestId('user-toggle-role')).toBeVisible();
  });

  test('changes user password', async ({ page }, testInfo) => {
    const tmpUser = uniqueUsername(testInfo, 'tmp_password');
    await data.createUser(tmpUser, 'OldPass123!', 'user');

    await openAdminDashboard(page);
    await openUserModal(page);

    page.on('dialog', (dialog) => dialog.accept('NewPass123!'));
    const userActions = page.getByTestId(`user-actions-${tmpUser}`);
    await userActions.getByTestId('user-change-password').click();
    await expect(userActions.getByTestId('user-change-password')).toBeVisible();
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
    const userActions = page.getByTestId(`user-actions-${uniqueUser}`);
    await userActions.getByTestId('user-delete').click();

    await expect(page.getByTestId('user-row-username').filter({ hasText: uniqueUser })).not.toBeVisible({
      timeout: 5000,
    });
  });

  test('shows row actions for secondary users', async ({ page }, testInfo) => {
    const secondaryUser = uniqueUsername(testInfo, 'actionuser');
    await data.createUser(secondaryUser, 'TestPass123!', 'user');

    await openAdminDashboard(page);
    await openUserModal(page);

    const userRows = page.getByTestId('user-row-username');
    const rowCount = await userRows.count();
    expect(rowCount).toBeGreaterThanOrEqual(2);

    const userActions = page.getByTestId(`user-actions-${secondaryUser}`);
    await expect(userActions.getByTestId('user-change-password')).toBeVisible();
    await expect(userActions.getByTestId('user-toggle-role')).toBeVisible();
    await expect(userActions.getByTestId('user-delete')).toBeVisible();
  });

  test('validates required username and password before creating a user', async ({ page }, testInfo) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await page.getByTestId('user-new-password').fill('SomePass123');
    await page.getByTestId('user-add-submit').click();
    await expect(page.getByText('Please fill in both username and password.')).toBeVisible();

    await page.getByTestId('user-new-password').clear();
    await page.getByTestId('user-new-username').fill(uniqueUsername(testInfo, 'emptyuser'));
    await page.getByTestId('user-add-submit').click();
    await expect(page.getByText('Please fill in both username and password.')).toBeVisible();
  });

  test('backend error on user creation shows error banner', async ({ page }) => {
    await openAdminDashboard(page);
    await page.route('**/users', async (route, request) => {
      if (request.method() === 'POST') {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Username already exists.' }),
        });
      } else {
        await route.continue();
      }
    });
    await openUserModal(page);

    await page.getByTestId('user-new-username').fill('existing_user');
    await page.getByTestId('user-new-password').fill('TestPass123');
    await page.getByTestId('user-add-submit').click();

    await expect(page.getByText('Username already exists.')).toBeVisible({ timeout: 5000 });
  });

  test('role selector defaults to user and can switch to admin', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    const roleSelect = page.getByTestId('user-role-select');
    await expect(roleSelect).toContainText('User');

    await roleSelect.click();
    await page.getByTestId('user-role-option-admin').click();

    await expect(roleSelect).toContainText('Admin');
  });

  test('storage cleanup button triggers API call', async ({ page }) => {
    await openAdminDashboard(page);
    let cleanupCalled = false;
    await page.route('**/runs/cleanup*', async (route, request) => {
      if (request.method() === 'POST') {
        cleanupCalled = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ cleaned_runs: 5 }),
        });
      } else {
        await route.continue();
      }
    });
    page.on('dialog', (dialog) => dialog.accept());

    await openUserModal(page);
    await page.getByTestId('retention-days-input').fill('60');
    await page.getByTestId('storage-cleanup-button').click();

    await expect(async () => {
      expect(cleanupCalled).toBe(true);
    }).toPass({ timeout: 5000 });
  });

  test('closes user management modal', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await page.keyboard.press('Escape');

    await expect(page.getByTestId('user-modal')).not.toBeVisible();
  });
});
