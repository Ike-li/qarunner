import { test, expect } from '@playwright/test';

// Trigger Run modal E2E tests — verifies the modal open/close, form fill,
// and submit flow using route-mocked backend for determinism.

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

async function login(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByTestId('login-username').fill('admin');
  await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

test.describe('Trigger Run modal', () => {
  test.beforeEach(async ({ page }) => {
    // Mock /runs to avoid needing real run data.
    await page.route('**/runs', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: [] });
    });
    // Mock /suites and /tests so the sidebar loads.
    await page.route('**/suites', async (route) => {
      await route.fulfill({ json: [] });
    });
    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['suite_a/'] });
    });
    // Mock /profiles to return an empty list.
    await page.route('**/profiles', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: [] });
    });
    await login(page);
  });

  test('open and close the trigger modal', async ({ page }) => {
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    await page.getByTestId('trigger-cancel-button').click();
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible();
  });

  test('fill args and timeout fields', async ({ page }) => {
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    await page.getByTestId('trigger-args-input').fill('-k smoke --verbose');
    await page.getByTestId('trigger-timeout-input').fill('120');

    // Verify the inputs hold their values.
    await expect(page.getByTestId('trigger-args-input')).toHaveValue('-k smoke --verbose');
    await expect(page.getByTestId('trigger-timeout-input')).toHaveValue('120');
  });

  test('submit with valid data triggers run (POST succeeds)', async ({ page }) => {
    // Mock the POST /runs endpoint to succeed.
    let postBody: unknown = null;
    await page.route('**/runs', async (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        await route.fulfill({
          json: {
            id: 'run-mock-0001',
            status: 'queued',
            runner: 'pytest',
            created_by: 'admin',
            tests_path: postBody?.tests_path ?? 'suite_a/',
            args: postBody?.args ?? [],
            executor_mode: 'subprocess',
            summary: null,
            report: null,
            exit_code: null,
            error: null,
            passed: false,
            created_at: '2026-07-02T10:00:00Z',
            started_at: null,
            finished_at: null,
            stdout: null,
            stderr: null,
            locked: false,
          },
        });
      } else {
        await route.fallback();
      }
    });

    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    await page.getByTestId('trigger-timeout-input').fill('60');
    await page.getByTestId('trigger-submit-button').click();

    // Modal should close on success.
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible({ timeout: 10000 });
  });

  test('submit with backend error shows form error banner', async ({ page }) => {
    // Mock POST /runs to return a 400 error.
    await page.route('**/runs', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 400,
          json: { detail: 'No test files found in suite' },
        });
      } else {
        await route.fallback();
      }
    });

    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    await page.getByTestId('trigger-timeout-input').fill('60');
    await page.getByTestId('trigger-submit-button').click();

    // Error banner should appear.
    await expect(page.getByTestId('trigger-form-error')).toBeVisible({ timeout: 10000 });
  });

  test('runner select has pytest and playwright options', async ({ page }) => {
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    const runnerSelect = page.getByTestId('trigger-runner-select');
    await expect(runnerSelect).toBeVisible();

    // Click to open the dropdown and verify options exist.
    await runnerSelect.click();
    const options = page.locator('.semi-select-option-list .semi-select-option');
    await expect(options.filter({ hasText: 'pytest' })).toBeVisible();
    await expect(options.filter({ hasText: 'playwright' })).toBeVisible();
  });
});
