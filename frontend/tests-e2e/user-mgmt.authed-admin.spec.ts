import { test, expect } from '@playwright/test';

import {
  UserTestData,
  loginThroughUi,
  openAdminDashboard,
  openUserModal,
  uniqueUsername,
} from './helpers/users';

// spec: specs/ui-test-plan.md
// Runs under `chromium-authed-admin`; only non-admin checks clear cookies and
// exercise the login page directly.

test.describe('User Management (Admin Only)', () => {
  let data: UserTestData;

  test.beforeEach(async () => {
    data = await UserTestData.create();
  });

  test.afterEach(async () => {
    await data.dispose();
  });

  test('User management modal opens from header', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await expect(page.getByTestId('user-row-username').filter({ hasText: 'admin' })).toBeVisible();
    await expect(page.getByTestId('user-new-username')).toBeVisible();
    await expect(page.getByTestId('user-new-password')).toBeVisible();
    await expect(page.getByTestId('user-add-submit')).toBeVisible();
    await expect(page.getByTestId('user-modal-close')).toBeAttached();
  });

  test('Non-admin user cannot see the users button', async ({ page }, testInfo) => {
    const nonAdminUser = uniqueUsername(testInfo, 'nouser');
    const password = 'TestPass123!';
    await data.createUser(nonAdminUser, password, 'user');

    await page.context().clearCookies();
    await loginThroughUi(page, nonAdminUser, password);
    await expect(page.getByTestId('profile-username')).toHaveText(nonAdminUser);

    await expect(page.getByTestId('open-users-button')).not.toBeVisible();
  });

  test('Create a new user', async ({ page }, testInfo) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    const uniqueUser = uniqueUsername(testInfo, 'newuser');
    data.track(uniqueUser);
    await page.getByTestId('user-new-username').fill(uniqueUser);
    await page.getByTestId('user-new-password').fill('TestPass123!');
    await page.getByTestId('user-add-submit').click();

    await expect(page.getByTestId('user-row-username').filter({ hasText: uniqueUser })).toBeVisible({
      timeout: 5000,
    });
  });

  test('Close user management modal', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await page.keyboard.press('Escape');

    await expect(page.getByTestId('user-modal')).not.toBeVisible();
  });

  test('User list shows users with row actions', async ({ page }, testInfo) => {
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

  test('Create user with empty username shows error', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await page.getByTestId('user-new-password').fill('SomePass123');
    await page.getByTestId('user-add-submit').click();

    await expect(page.getByText('Please fill in both username and password.')).toBeVisible();
  });

  test('Create user with empty password shows error', async ({ page }, testInfo) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    await page.getByTestId('user-new-username').fill(uniqueUsername(testInfo, 'emptyuser'));
    await page.getByTestId('user-add-submit').click();

    await expect(page.getByText('Please fill in both username and password.')).toBeVisible();
  });

  test('Backend error on user creation shows error banner', async ({ page }) => {
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

  test('Role selector defaults to "user" and can switch to "admin"', async ({ page }) => {
    await openAdminDashboard(page);
    await openUserModal(page);

    const roleSelect = page.getByTestId('user-role-select');
    await expect(roleSelect).toContainText('User');

    await roleSelect.click();
    await page.getByTestId('user-role-option-admin').click();

    await expect(roleSelect).toContainText('Admin');
  });

  test('Storage cleanup button triggers API call', async ({ page }) => {
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
});
