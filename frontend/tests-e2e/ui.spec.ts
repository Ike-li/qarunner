import { test, expect } from '@playwright/test';

// FE-5 Stage E: selectors target stable data-testid hooks instead of CSS-module
// class substrings / display copy, so a11y structural tweaks no longer break e2e.
//
// Runs under the default `chromium` project intentionally: these are anonymous
// login-entry smoke tests and must not receive the authed storageState.

test.describe('qarunner unauthenticated login smoke', () => {
  // Run before each test
  test.beforeEach(async ({ page }) => {
    // Navigate to the base URL
    await page.goto('/');
  });

  test('Test Case 1: Login Form & Unauthorized Redirection', async ({ page }) => {
    // Verify that navigating to the site displays the full-screen Login overlay
    await expect(page.getByTestId('login-title')).toHaveText('qarunner');
    await expect(page.getByTestId('login-subtitle')).toBeVisible();
    await expect(page.getByTestId('login-username')).toBeVisible();
    await expect(page.getByTestId('login-password')).toBeVisible();
    await expect(page.getByTestId('login-submit')).toBeVisible();
  });

  test('Test Case 2: Failed Login Validation', async ({ page }) => {
    // Fill in incorrect login details
    await page.getByTestId('login-username').fill('wrong_user');
    await page.getByTestId('login-password').fill('wrong_password');
    await page.getByTestId('login-submit').click();

    // Verify styled error alert shows up
    const errorAlert = page.getByTestId('login-error');
    await expect(errorAlert).toBeVisible();
    await expect(errorAlert).toContainText('Incorrect username or password');
  });
});
