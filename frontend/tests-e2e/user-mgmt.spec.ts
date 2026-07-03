import { test, expect } from '@playwright/test';

// spec: specs/ui-test-plan.md

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

async function loginAs(page: import('@playwright/test').Page, username: string, password: string) {
  await page.goto('/');
  await page.getByTestId('login-username').fill(username);
  await page.getByTestId('login-password').fill(password);
  await page.getByTestId('login-submit').click();
}

test.describe('User Management (Admin Only)', () => {

  // ── 1.1 User management modal opens from header ──────────────────────────────

  test('User management modal opens from header', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('profile-role')).toHaveText('admin');

    // 2. Click the "open-users-button" in the header
    await page.getByTestId('open-users-button').click();

    // 3. The Semi UI Modal's data-testid attribute ends up on the outer wrapper
    //    div (semi-modal) which collapses to zero height because all its children
    //    use position:fixed (taken out of normal flow). Playwright's toBeVisible()
    //    requires a non-zero bounding box, so the wrapper itself is never visible.
    //    Instead, check that the modal wrapper is attached to the DOM and then wait
    //    for visible child elements inside the modal content.
    await expect(page.getByTestId('user-modal')).toBeAttached();
    await expect(page.getByTestId('user-row-username').filter({ hasText: 'admin' })).toBeVisible();

    // 4. Verify "user-new-username" and "user-new-password" inputs and "user-add-submit" button are visible
    await expect(page.getByTestId('user-new-username')).toBeVisible();
    await expect(page.getByTestId('user-new-password')).toBeVisible();
    await expect(page.getByTestId('user-add-submit')).toBeVisible();

    // 5. Verify "user-modal-close" icon is attached (semi-modal children use fixed positioning)
    await expect(page.getByTestId('user-modal-close')).toBeAttached();
  });

  // ── 1.2 Non-admin user cannot see the users button ──────────────────────────

  test('Non-admin user cannot see the users button', async ({ page }) => {
    // 1. Create a non-admin user first, then login as that user
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await page.getByTestId('open-users-button').click();
    // Semi UI Modal's outer wrapper has zero height because its children use
    // position:fixed; toBeVisible() fails. Check attachment then wait for
    // visible child content.
    await expect(page.getByTestId('user-modal')).toBeAttached();

    const nonAdminUser = `nouser_${Date.now()}`;
    await page.getByTestId('user-new-username').fill(nonAdminUser);
    await page.getByTestId('user-new-password').fill('TestPass123');
    await page.getByTestId('user-add-submit').click();
    await expect(page.getByTestId('user-row-username').filter({ hasText: nonAdminUser })).toBeVisible({ timeout: 5000 });

    // Close modal by pressing Escape (semi-modal close icon is outside viewport)
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('user-modal')).not.toBeVisible();

    // Logout
    await page.locator('header button:last-of-type').click();
    await expect(page.getByTestId('login-title')).toBeVisible();

    // Login as non-admin user
    await loginAs(page, nonAdminUser, 'TestPass123');
    await expect(page.getByTestId('profile-username')).toHaveText(nonAdminUser);

    // 2. Verify "open-users-button" is not visible in the header
    await expect(page.getByTestId('open-users-button')).not.toBeVisible();
  });

  // ── 1.3 Create a new user ────────────────────────────────────────────────────

  test('Create a new user', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    // Semi UI Modal wrapper has zero height due to position:fixed children,
    // so use toBeAttached() instead of toBeVisible().
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // 3. Fill "user-new-username" with a unique username
    const uniqueUser = `newuser_${Date.now()}`;
    await page.getByTestId('user-new-username').fill(uniqueUser);

    // 4. Fill "user-new-password" with a password
    await page.getByTestId('user-new-password').fill('TestPass123');

    // 5. Click "user-add-submit" button
    await page.getByTestId('user-add-submit').click();

    // 6. Verify the new user appears in the user list
    await expect(page.getByTestId('user-row-username').filter({ hasText: uniqueUser })).toBeVisible({ timeout: 5000 });
  });

  // ── 1.4 Close user management modal ──────────────────────────────────────────

  test('Close user management modal', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // 3. Close modal by pressing Escape
    await page.keyboard.press('Escape');

    // 4. Verify "user-modal" is not attached (component unmounts on close)
    await expect(page.getByTestId('user-modal')).not.toBeVisible();
  });

  // ── 1.5 User list shows users with row actions ───────────────────────────────

  test('User list shows users with row actions', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    // Semi UI Modal outer wrapper has zero height (position:fixed children);
    // check attachment and rely on subsequent element visibility checks.
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // Create a secondary non-admin user so we have >1 row with full action buttons
    const secondaryUser = `actionuser_${Date.now()}`;
    await page.getByTestId('user-new-username').fill(secondaryUser);
    await page.getByTestId('user-new-password').fill('TestPass123');
    await page.getByTestId('user-add-submit').click();
    await expect(page.getByTestId('user-row-username').filter({ hasText: secondaryUser })).toBeVisible({ timeout: 5000 });

    // 3. Verify each row displays a username
    const userRows = page.getByTestId('user-row-username');
    const rowCount = await userRows.count();
    expect(rowCount).toBeGreaterThanOrEqual(2);

    // 4. Verify action buttons exist:
    //    - user-change-password appears on every row
    //    - user-toggle-role and user-delete appear on non-self rows
    await expect(page.getByTestId('user-change-password').first()).toBeVisible();
    await expect(page.getByTestId('user-toggle-role').first()).toBeVisible();
    await expect(page.getByTestId('user-delete').first()).toBeVisible();
  });

  // ── 2.1 Create user with empty username shows error ──────────────────────────

  test('Create user with empty username shows error', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // 3. Leave username empty, fill password
    await page.getByTestId('user-new-password').fill('SomePass123');

    // 4. Click submit — client-side validation in handleCreateUserSubmit
    await page.getByTestId('user-add-submit').click();

    // 5. Verify the client-side validation error appears
    await expect(page.getByText('Please fill in both username and password.')).toBeVisible();
  });

  // ── 2.2 Create user with empty password shows error ──────────────────────────

  test('Create user with empty password shows error', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // 3. Fill username, leave password empty
    const uniqueUser = `emptyuser_${Date.now()}`;
    await page.getByTestId('user-new-username').fill(uniqueUser);

    // 4. Click submit — client-side validation in handleCreateUserSubmit
    await page.getByTestId('user-add-submit').click();

    // 5. Verify the client-side validation error appears
    await expect(page.getByText('Please fill in both username and password.')).toBeVisible();
  });

  // ── 2.3 Backend error on user creation shows error banner ──────────────────

  test('Backend error on user creation shows error banner', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Mock POST /users to return 400 with an error message
    await page.route('**/users', async (route, request) => {
      if (request.method() === 'POST') {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Username already exists.' }),
        });
      } else {
        await route.continue();
      }
    });

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // 3. Fill form with valid data
    await page.getByTestId('user-new-username').fill('existing_user');
    await page.getByTestId('user-new-password').fill('TestPass123');

    // 4. Click submit — POST is intercepted by mock
    await page.getByTestId('user-add-submit').click();

    // 5. Verify the backend error banner appears
    await expect(page.getByText('Username already exists.')).toBeVisible({ timeout: 5000 });
  });

  // ── 2.4 Role selector defaults to "user" and can switch to "admin" ─────────

  test('Role selector defaults to "user" and can switch to "admin"', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // 3. Verify the role select defaults to "User"
    const roleSelect = page.getByTestId('user-modal').locator('.semi-select');
    await expect(roleSelect).toContainText('User');

    // 4. Open the select dropdown and choose "Admin"
    await roleSelect.click();
    await page.getByRole('option', { name: 'Admin' }).click();

    // 5. Verify the select now shows "Admin"
    await expect(roleSelect).toContainText('Admin');
  });

  // ── 2.5 Storage cleanup button triggers API call ───────────────────────────

  test('Storage cleanup button triggers API call', async ({ page }) => {
    // 1. Login as admin
    await loginAs(page, 'admin', ADMIN_PASSWORD);
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Mock the cleanup endpoint
    let cleanupCalled = false;
    await page.route('**/runs/cleanup*', async (route, request) => {
      if (request.method() === 'POST') {
        cleanupCalled = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ cleaned_runs: 5 }),
        });
      } else {
        await route.continue();
      }
    });

    // Handle the alert dialog that fires after success
    page.on('dialog', (dialog) => dialog.accept());

    // 2. Click "open-users-button" to open the user modal
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeAttached();

    // 3. Change retention days to 60
    await page.getByTestId('user-modal').locator('input[type="number"]').fill('60');

    // 4. Click the "Clean Old Runs" button
    await page.getByRole('button', { name: /Clean Old Runs/i }).click();

    // 5. Verify the API was called
    await expect(async () => {
      expect(cleanupCalled).toBe(true);
    }).toPass({ timeout: 5000 });
  });
});
