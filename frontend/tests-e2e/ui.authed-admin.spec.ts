import { test, expect } from '@playwright/test';

import {
  UserTestData,
  openAdminDashboard,
  openUserModal,
  uniqueUsername,
} from './helpers/users';

// Premium UI admin smoke tests. Runs under `chromium-authed-admin`, so
// storageState supplies the admin session.

test.describe('qarunner Premium UI E2E Tests — admin', () => {
  let data: UserTestData;

  test.beforeEach(async ({ page }) => {
    data = await UserTestData.create();
    await openAdminDashboard(page);
  });

  test.afterEach(async () => {
    await data.dispose();
  });

  test('Test Case 3: Successful Sign In (Admin) & Dashboard Render', async ({ page }) => {
    // Verify profile capsule contains admin information
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('profile-role')).toHaveText('admin');

    // Verify stats cards are rendered
    await expect(page.getByTestId('stat-total')).toBeVisible();
    await expect(page.getByTestId('stat-success-rate')).toBeVisible();
    await expect(page.getByTestId('stat-failed')).toBeVisible();
    await expect(page.getByTestId('stat-active')).toBeVisible();

    // Verify execution records table title is shown
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
  });

  test('Test Case 4: Run Trigger Modal Workflow', async ({ page }) => {
    // Click on Trigger Run
    await page.getByTestId('open-trigger-button').click();

    // Verify modal is open
    await expect(page.getByTestId('trigger-modal')).toBeVisible();
    await expect(page.getByTestId('trigger-args-input')).toBeVisible();

    // Fill some options
    await page.getByTestId('trigger-args-input').fill('-q');
    await page.getByTestId('trigger-timeout-input').fill('120');

    // Click Cancel to verify close
    await page.getByTestId('trigger-cancel-button').click();
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible();
  });

  test('Test Case 5: User Management Panel (Admin Only)', async ({ page }, testInfo) => {
    await openUserModal(page);

    // Check that 'admin' exists in the user directory
    await expect(page.getByTestId('user-row-username').filter({ hasText: 'admin' })).toBeVisible();

    // Register a new user with a dynamically generated unique username
    const userName = uniqueUsername(testInfo, 'tester');
    data.track(userName);
    await page.getByTestId('user-new-username').fill(userName);
    await page.getByTestId('user-new-password').fill('TestPass123!');
    await page.getByTestId('user-add-submit').click();

    // Verify user is created and appears in the table list
    await expect(page.getByTestId('user-row-username').filter({ hasText: userName })).toBeVisible();

    // Close user management modal
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('user-modal')).not.toBeVisible();
  });
});
