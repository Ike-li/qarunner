import { test, expect } from '@playwright/test';

// The backend rejects known-weak admin passwords (SEC-2), so the seeded admin
// password is configurable. Override via E2E_ADMIN_PASSWORD to match the backend
// under test; defaults to the legacy 'admin123' for local/dev backends.
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

// FE-5 Stage E: selectors target stable data-testid hooks instead of CSS-module
// class substrings / display copy, so a11y structural tweaks no longer break e2e.

test.describe('qarunner Premium UI E2E Tests', () => {
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

  test('Test Case 3: Successful Sign In (Admin) & Dashboard Render', async ({ page }) => {
    // Log in with default Admin credentials
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
    await page.getByTestId('login-submit').click();

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
    // Log in
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
    await page.getByTestId('login-submit').click();

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

  test('Test Case 5: User Management Panel (Admin Only)', async ({ page }) => {
    // Log in
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
    await page.getByTestId('login-submit').click();

    // Click on Users button in header
    await page.getByTestId('open-users-button').click();

    // Verify User Management Modal opened
    await expect(page.getByTestId('user-modal')).toBeVisible();

    // Check that 'admin' exists in the user directory
    await expect(page.getByTestId('user-row-username').filter({ hasText: 'admin' })).toBeVisible();

    // Register a new user with a dynamically generated unique username
    const uniqueUsername = `tester_${Math.floor(Math.random() * 1000000)}`;
    await page.getByTestId('user-new-username').fill(uniqueUsername);
    await page.getByTestId('user-new-password').fill('test_pass_123');
    await page.getByTestId('user-add-submit').click();

    // Verify user is created and appears in the table list
    await expect(page.getByTestId('user-row-username').filter({ hasText: uniqueUsername })).toBeVisible();

    // Close user management modal
    await page.getByTestId('user-modal-close').click();
    await expect(page.getByTestId('user-modal')).not.toBeVisible();
  });
});
