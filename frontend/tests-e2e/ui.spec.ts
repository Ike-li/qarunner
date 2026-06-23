import { test, expect } from '@playwright/test';

// The backend rejects known-weak admin passwords (SEC-2), so the seeded admin
// password is configurable. Override via E2E_ADMIN_PASSWORD to match the backend
// under test; defaults to the legacy 'admin123' for local/dev backends.
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

test.describe('qarunner Premium UI E2E Tests', () => {
  // Run before each test
  test.beforeEach(async ({ page }) => {
    // Navigate to the base URL
    await page.goto('/');
  });

  test('Test Case 1: Login Form & Unauthorized Redirection', async ({ page }) => {
    // Verify that navigating to the site redirects or displays the full-screen Login overlay
    await expect(page.locator('h1')).toHaveText('qarunner');
    await expect(page.getByText('QA TEST ORCHESTRATION')).toBeVisible();
    await expect(page.getByPlaceholder('Enter username')).toBeVisible();
    await expect(page.getByPlaceholder('Enter password')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();
  });

  test('Test Case 2: Failed Login Validation', async ({ page }) => {
    // Fill in incorrect login details
    await page.getByPlaceholder('Enter username').fill('wrong_user');
    await page.getByPlaceholder('Enter password').fill('wrong_password');
    await page.getByRole('button', { name: 'Sign In' }).click();

    // Verify styled error alert shows up
    const errorAlert = page.locator('div[class*="formErrorAlert"]');
    await expect(errorAlert).toBeVisible();
    await expect(errorAlert).toContainText('Incorrect username or password');
  });

  test('Test Case 3: Successful Sign In (Admin) & Dashboard Render', async ({ page }) => {
    // Log in with default Admin credentials
    await page.getByPlaceholder('Enter username').fill('admin');
    await page.getByPlaceholder('Enter password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: 'Sign In' }).click();

    // Verify profile capsule contains admin information
    await expect(page.locator('span[class*="profileUsername"]')).toHaveText('admin');
    await expect(page.locator('span[class*="profileRoleTag"]')).toHaveText('admin');

    // Verify stats cards are rendered
    await expect(page.getByText('Total Executions')).toBeVisible();
    await expect(page.getByText('Success Rate')).toBeVisible();
    await expect(page.getByText('Failed Runs')).toBeVisible();
    await expect(page.getByText('Active Queue')).toBeVisible();
    
    // Verify execution records table title is shown
    await expect(page.locator('h2', { hasText: 'Execution Records' })).toBeVisible();
  });

  test('Test Case 4: Run Trigger Modal Workflow', async ({ page }) => {
    // Log in
    await page.getByPlaceholder('Enter username').fill('admin');
    await page.getByPlaceholder('Enter password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: 'Sign In' }).click();

    // Click on Trigger Run
    await page.getByRole('button', { name: 'Trigger Run' }).click();

    // Verify modal is open
    await expect(page.locator('h2', { hasText: 'Trigger Automated Pytest Run' })).toBeVisible();
    await expect(page.getByText('Target Test Directory')).toBeVisible();
    await expect(page.getByPlaceholder('e.g. -v -k test_api --tb=short')).toBeVisible();

    // Fill some options
    await page.getByPlaceholder('e.g. -v -k test_api --tb=short').fill('-q');
    await page.getByPlaceholder('default: 1800').fill('120');

    // Click Cancel to verify close
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.locator('h2', { hasText: 'Trigger Automated Pytest Run' })).not.toBeVisible();
  });

  test('Test Case 5: User Management Panel (Admin Only)', async ({ page }) => {
    // Log in
    await page.getByPlaceholder('Enter username').fill('admin');
    await page.getByPlaceholder('Enter password').fill(ADMIN_PASSWORD);
    await page.getByRole('button', { name: 'Sign In' }).click();

    // Click on Users button in header
    await page.getByRole('button', { name: 'Users' }).click();

    // Verify User Management Modal details
    await expect(page.locator('h2', { hasText: 'User Management Panel' })).toBeVisible();
    await expect(page.locator('h3', { hasText: 'Platform Directory' })).toBeVisible();
    await expect(page.locator('h3', { hasText: 'Register New User' })).toBeVisible();

    // Check that 'admin' exists in the user directory
    await expect(page.locator('td[class*="tdUsername"]').filter({ hasText: 'admin' })).toBeVisible();

    // Register a new user with a dynamically generated unique username
    const uniqueUsername = `tester_${Math.floor(Math.random() * 1000000)}`;
    await page.getByPlaceholder('e.g. testing_lead').fill(uniqueUsername);
    await page.locator('input[type="password"]').fill('test_pass_123');
    await page.getByRole('button', { name: 'Add User Account' }).click();

    // Verify user is created and appears in the table list
    await expect(page.locator('td[class*="tdUsername"]').filter({ hasText: uniqueUsername })).toBeVisible();

    // Close user management modal
    await page.locator('button[class*="modalCloseButton"]').click();
    await expect(page.locator('h2', { hasText: 'User Management Panel' })).not.toBeVisible();
  });
});
