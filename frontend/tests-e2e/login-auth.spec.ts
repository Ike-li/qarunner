import { test, expect } from '@playwright/test';

// The backend rejects known-weak admin passwords (SEC-2), so the seeded admin
// password is configurable. Override via E2E_ADMIN_PASSWORD to match the backend
// under test; defaults to the legacy 'admin123' for local/dev backends.
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

test.describe('Login and Authentication', () => {
  // spec: specs/ui-test-plan.md §1

  test.beforeEach(async ({ page }) => {
    // Navigate to the application root URL
    await page.goto('/');
  });

  // ── 1.1 Login form renders correctly for unauthenticated users ──────────────

  test('Login form renders correctly for unauthenticated users', async ({ page }) => {
    // 1. Navigate to the application root URL — handled by beforeEach
    // 2. Verify that login-title displays the platform name 'qarunner'
    await expect(page.getByTestId('login-title')).toHaveText('qarunner');
    // 3. Verify that login-subtitle is visible
    await expect(page.getByTestId('login-subtitle')).toBeVisible();
    // 4. Verify that login-username input field is visible and empty
    await expect(page.getByTestId('login-username')).toBeVisible();
    await expect(page.getByTestId('login-username')).toHaveValue('');
    // 5. Verify that login-password input field is visible and empty
    await expect(page.getByTestId('login-password')).toBeVisible();
    await expect(page.getByTestId('login-password')).toHaveValue('');
    // 6. Verify that login-submit button is visible
    await expect(page.getByTestId('login-submit')).toBeVisible();
    // 7. Verify that theme toggle button is visible in top-right corner
    await expect(page.getByRole('button', { name: /switch to (light|dark) mode/i })).toBeVisible();
    // 8. Verify that language toggle button is visible in top-right corner
    await expect(page.getByRole('button', { name: /switch to english|切换为中文/i })).toBeVisible();
  });

  // ── 1.2 Failed login with wrong credentials shows error banner ──────────────

  test('Failed login with wrong credentials shows error banner', async ({ page }) => {
    // 1. Navigate to the app — handled by beforeEach
    // 2. Enter 'wrong_user' as username
    await page.getByTestId('login-username').fill('wrong_user');
    // 3. Enter 'wrong_password' as password
    await page.getByTestId('login-password').fill('wrong_password');
    // 4. Click the login-submit button
    await page.getByTestId('login-submit').click();
    // 5. Verify login-error alert becomes visible with error message
    await expect(page.getByTestId('login-error')).toBeVisible();
    await expect(page.getByTestId('login-error')).toContainText('Incorrect username or password');
    // 6. Verify the login form remains visible
    await expect(page.getByTestId('login-username')).toBeVisible();
    await expect(page.getByTestId('login-password')).toBeVisible();
    await expect(page.getByTestId('login-submit')).toBeVisible();
  });

  // ── 1.3 Successful login as admin redirects to dashboard ────────────────────

  test('Successful login as admin redirects to dashboard', async ({ page }) => {
    // 1. Navigate to the app — handled by beforeEach
    // 2. Enter 'admin' as username and the configured admin password
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
    // 3. Click login-submit button
    await page.getByTestId('login-submit').click();
    // 4. Verify profile-username displays 'admin'
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    // 5. Verify profile-role displays 'admin'
    await expect(page.getByTestId('profile-role')).toHaveText('admin');
    // 6. Verify stat-total is visible
    await expect(page.getByTestId('stat-total')).toBeVisible();
    // 7. Verify stat-success-rate is visible
    await expect(page.getByTestId('stat-success-rate')).toBeVisible();
    // 8. Verify stat-failed is visible
    await expect(page.getByTestId('stat-failed')).toBeVisible();
    // 9. Verify stat-active is visible
    await expect(page.getByTestId('stat-active')).toBeVisible();
    // 10. Verify execution-records-title is visible
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
  });

  // ── 1.4 Login form field validation - empty username ────────────────────────

  test('Login form field validation - empty username', async ({ page }) => {
    // 1. Navigate to the app — handled by beforeEach
    // 2. Leave username empty, enter any password
    await page.getByTestId('login-password').fill('somepassword');
    // 3. Click login-submit — HTML5 'required' validation should block submission
    await page.getByTestId('login-submit').click();
    // 4. Verify the form was not submitted and login screen is still shown
    await expect(page.getByTestId('login-title')).toBeVisible();
    await expect(page.getByTestId('login-username')).toBeVisible();
  });

  // ── 1.5 Login form field validation - empty password ────────────────────────

  test('Login form field validation - empty password', async ({ page }) => {
    // 1. Navigate to the app — handled by beforeEach
    // 2. Enter any username, leave password empty
    await page.getByTestId('login-username').fill('admin');
    // 3. Click login-submit — HTML5 'required' validation should block submission
    await page.getByTestId('login-submit').click();
    // 4. Verify the form was not submitted and login screen is still shown
    await expect(page.getByTestId('login-title')).toBeVisible();
    await expect(page.getByTestId('login-password')).toBeVisible();
  });

  // ── 1.6 Theme toggle on login screen switches between dark and light ─────────

  test('Theme toggle on login screen switches between dark and light', async ({ page }) => {
    // 1. Navigate to the app — handled by beforeEach (default dark theme)
    // 2. Click the theme toggle button
    const themeToggle = page.getByRole('button', { name: /switch to (light|dark) mode/i });
    // Note the current aria-label before toggling
    const initialLabel = await themeToggle.getAttribute('aria-label');
    await themeToggle.click();
    // 3. Verify the theme switched — the aria-label should have changed
    await expect(themeToggle).not.toHaveAttribute('aria-label', initialLabel);
    // The data-theme attribute on <html> should have changed
    await expect(page.locator('html')).toHaveAttribute('data-theme', /^(dark|light)$/);
    // 4. Click the theme toggle button again to switch back
    await themeToggle.click();
    // Verify theme toggle is still visible
    await expect(themeToggle).toBeVisible();
  });

  // ── 1.7 Language toggle on login screen switches between EN and ZH ───────────

  test('Language toggle on login screen switches between EN and ZH', async ({ page }) => {
    // 1. Navigate to the app — handled by beforeEach (default English)
    await expect(page.getByTestId('login-username')).toHaveAttribute('placeholder', 'Enter username');
    // 2. Click the language toggle button to switch to Chinese
    const langToggle = page.getByRole('button', { name: /switch to english|切换为中文/i });
    await langToggle.click();
    // 3. Verify UI labels and placeholders switched to Chinese
    await expect(page.getByTestId('login-username')).toHaveAttribute('placeholder', '请输入用户名');
    await expect(page.getByTestId('login-password')).toHaveAttribute('placeholder', '请输入密码');
    // 4. Click the language toggle button again to switch back to English
    await langToggle.click();
    // 5. Verify UI switched back to English
    await expect(page.getByTestId('login-username')).toHaveAttribute('placeholder', 'Enter username');
    await expect(page.getByTestId('login-password')).toHaveAttribute('placeholder', 'Enter password');
  });
});
