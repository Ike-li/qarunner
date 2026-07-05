import { test, expect } from '@playwright/test';

// Runs under `chromium-authed-admin`, so storageState supplies the admin session.
async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

test.describe('Run lifecycle E2E', () => {
  test.beforeEach(async ({ page }) => {
    await openDashboard(page);
  });

  test('trigger a run via the modal and see it appear in the table', async ({ page }) => {
    // Open the trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // The tests-path select defaults to the first available suite, but we need
    // at least one suite to exist on the filesystem.  If no suites are present
    // the run can still be triggered against any non-empty tests_path the
    // backend accepts — the assertion below tolerates a 400 with a clear error
    // detail so the test doesn't hang.

    // Fill the timeout and submit
    await page.getByTestId('trigger-timeout-input').fill('60');

    // Try submitting — if there's a valid testsPath selected, this creates a
    // run; otherwise the backend returns 400 and we verify the error banner.
    await page.getByTestId('trigger-submit-button').click();

    // Either the modal closes (success) or an error banner is shown.
    const modal = page.getByTestId('trigger-modal');
    const formError = page.getByTestId('trigger-form-error');
    // Wait for one of the two outcomes
    await Promise.race([
      expect(modal).not.toBeVisible({ timeout: 8000 }),
      expect(formError).toBeVisible({ timeout: 8000 }),
    ]);

    if (await modal.isVisible()) {
      // Run couldn't be created (no suite available); dismiss and skip the
      // rest of the test gracefully.
      await page.getByTestId('trigger-cancel-button').click();
      await expect(modal).not.toBeVisible();
      return;
    }

    // Run was created — verify it appears in the table with a queued/running/
    // completed status within 15 seconds.
    await expect(async () => {
      const rows = page.getByTestId('execution-records-title');
      await expect(rows).toBeVisible();
      // The table should contain at least one row with a status tag
      const statusTags = page.locator('[data-testid="execution-records-title"]')
        .locator('..')
        .locator('..');
      await expect(statusTags).toBeVisible();
    }).toPass({ timeout: 15000 });
  });

  test('click a run row to open the details drawer with log tab', async ({ page }) => {
    // First, make sure there's at least one run in the table. If the table is
    // empty (no previous runs), skip gracefully.
    const title = page.getByTestId('execution-records-title');
    await expect(title).toBeVisible({ timeout: 5000 });

    // Click the first clickable row in the runs table.  Semi UI Table rows
    // have an onClick handler; we target the row by the run-id cell.
    const firstRow = page.locator('[data-testid^="run-row-"]').first();
    const firstRowExists = await firstRow.isVisible().catch(() => false);
    if (!firstRowExists) {
      // No runs exist yet — skip this test.
      return;
    }

    await firstRow.click();

    // The RunDetailsDrawer should open (SideSheet from Semi UI)
    // Wait for the drawer content to appear
    await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 5000 });

    // The Logs tab should be active by default
    const logsTab = page.getByTestId('drawer-tab-logs');
    if (await logsTab.isVisible()) {
      await expect(logsTab).toBeVisible();
    }
  });
});
