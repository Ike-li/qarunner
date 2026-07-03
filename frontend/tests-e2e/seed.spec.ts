import { test, expect } from '@playwright/test';

/**
 * Seed test for Playwright Test Agents.
 * This test establishes the baseline environment for all generated tests:
 * - Navigates to the app
 * - Logs in as admin
 * - Verifies dashboard is accessible
 *
 * Agents (planner/generator) will run this test first to understand
 * the app's authentication flow and basic structure.
 */

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

test.describe('qarunner seed', () => {
  test('seed - login and verify dashboard', async ({ page }) => {
    // Navigate to the application
    await page.goto('/');

    // Verify login page is displayed
    await expect(page.getByTestId('login-title')).toHaveText('qarunner');
    await expect(page.getByTestId('login-username')).toBeVisible();
    await expect(page.getByTestId('login-password')).toBeVisible();

    // Login as admin
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
    await page.getByTestId('login-submit').click();

    // Verify dashboard loaded successfully
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('profile-role')).toHaveText('admin');
    await expect(page.getByTestId('stat-total')).toBeVisible();
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
  });
});
