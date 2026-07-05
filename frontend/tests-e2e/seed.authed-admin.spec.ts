import { test, expect } from '@playwright/test';

import { openAdminDashboard } from './helpers/users';

/**
 * Seed test for Playwright Test Agents.
 * This test establishes the baseline environment for all generated tests:
 * - Uses the shared admin storageState from global setup
 * - Navigates to the app through the standard dashboard helper
 * - Verifies core dashboard landmarks are accessible
 *
 * Agents (planner/generator) will run this test first to understand
 * the app's authenticated dashboard structure.
 */

test.describe('qarunner seed', () => {
  test('seed - verify authed admin dashboard', async ({ page }) => {
    await openAdminDashboard(page);

    await expect(page.getByTestId('stat-total')).toBeVisible();
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
  });
});
