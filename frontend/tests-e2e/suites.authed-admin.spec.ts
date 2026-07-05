import { test, expect } from '@playwright/test';

// External test suites — stage 5 frontend (git tab + source-aware actions).
// Runs under `chromium-authed-admin` so it uses storageState instead of UI login.
// Selectors target stable data-testid hooks (same convention as ui.spec.ts).
// Network-dependent paths (a successful clone hits real git) are intentionally
// NOT asserted here; we cover the UI structure and the URL-allowlist rejection,
// which the backend answers with 400 *before* any git subprocess runs.

async function openDashboard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

async function openAddSuiteModal(page: import('@playwright/test').Page) {
  await page.getByTestId('open-add-suite-button').click();
  await expect(page.getByTestId('add-suite-modal')).toBeAttached();
}

test.describe('External test suites — Add Suite modal', () => {
  test.beforeEach(async ({ page }) => {
    await openDashboard(page);
  });

  test('opens with Git and Local tabs; git form fields present', async ({ page }) => {
    await openAddSuiteModal(page);

    // Both tabs exist; switch to Git before asserting Git-only fields.
    await expect(page.getByTestId('suite-tab-git')).toBeVisible();
    await expect(page.getByTestId('suite-tab-local')).toBeVisible();
    await page.getByTestId('suite-tab-git').click();
    await expect(page.getByTestId('clone-url-input')).toBeVisible();
    await expect(page.getByTestId('clone-ref-input')).toBeVisible();
    await expect(page.getByTestId('clone-cred-select')).toBeVisible();
    await expect(page.getByTestId('clone-submit')).toBeVisible();
  });

  test('switches to the Local tab and shows the link form', async ({ page }) => {
    await openAddSuiteModal(page);
    await page.getByTestId('suite-tab-local').click();

    await expect(page.getByTestId('link-path-input')).toBeVisible();
    await expect(page.getByTestId('link-submit')).toBeVisible();
  });

  test('rejects a non-allowlisted URL (400, no git spawned)', async ({ page }) => {
    await openAddSuiteModal(page);
    await page.getByTestId('suite-tab-git').click();
    // http:// is outside the https/git@ allowlist; the backend rejects it with
    // 400 before running git, so this needs no network.
    await page.getByTestId('clone-url-input').fill('http://insecure/repo.git');
    await page.getByTestId('clone-submit').click();

    await expect(page.getByTestId('clone-feedback')).toBeVisible();
  });
});
