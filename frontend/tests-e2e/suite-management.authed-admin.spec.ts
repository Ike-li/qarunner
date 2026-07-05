import { test, expect } from '@playwright/test';

// Runs under `chromium-authed-admin` so it uses storageState instead of UI login.

test.describe('Suite Management (Add Suite Modal)', () => {
  // spec: specs/ui-test-plan.md §6

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
  });

  // ── 6.1 Add suite modal opens from sidebar button ──────────────────────────────

  test('Add suite modal opens from sidebar button', async ({ page }) => {
    // 1. Click open-add-suite-button in the sidebar header
    await page.getByTestId('open-add-suite-button').click();
    // 2. Semi UI Modal's data-testid ends up on the outer wrapper which collapses
    //    to zero height (all children use position:fixed). Use toBeAttached() for
    //    the modal wrapper and toBeVisible() for child content elements instead.
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    // 3. Verify both suite-tab-local and suite-tab-git tabs are visible
    await expect(page.getByTestId('suite-tab-local')).toBeVisible();
    await expect(page.getByTestId('suite-tab-git')).toBeVisible();
  });

  // ── 6.2 Local tab shows path input and link button ─────────────────────────────

  test('Local tab shows path input and link button', async ({ page }) => {
    // 1. Open add-suite-modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    // 2. Click suite-tab-local
    await page.getByTestId('suite-tab-local').click();
    // 3. Verify link-path-input and link-submit button are visible
    await expect(page.getByTestId('link-path-input')).toBeVisible();
    await expect(page.getByTestId('link-submit')).toBeVisible();
  });

  // ── 6.3 Git tab shows clone URL, ref, credential fields ────────────────────────

  test('Git tab shows clone URL, ref, credential fields', async ({ page }) => {
    // 1. Open add-suite-modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    // 2. Click suite-tab-git
    await page.getByTestId('suite-tab-git').click();
    // 3. Verify clone-url-input, clone-ref-input, clone-cred-select, and clone-submit are visible
    await expect(page.getByTestId('clone-url-input')).toBeVisible();
    await expect(page.getByTestId('clone-ref-input')).toBeVisible();
    await expect(page.getByTestId('clone-cred-select')).toBeVisible();
    await expect(page.getByTestId('clone-submit')).toBeVisible();
  });

  // ── 6.4 In-credential creation UI is present on Git tab ────────────────────────

  test('In-credential creation UI is present on Git tab', async ({ page }) => {
    // 1. Open add-suite-modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    // 2. Click suite-tab-git
    await page.getByTestId('suite-tab-git').click();
    // 3. Verify credential creation inputs and save button are visible
    await expect(page.getByTestId('clone-newcred-name')).toBeVisible();
    await expect(page.getByTestId('clone-newcred-secret')).toBeVisible();
    await expect(page.getByTestId('clone-newcred-save')).toBeVisible();
  });

  // ── 6.5 Non-allowlisted URL is rejected without spawning git ───────────────────

  test('Non-allowlisted URL is rejected without spawning git', async ({ page }) => {
    // 1. Open add-suite-modal and switch to Git tab
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    await page.getByTestId('suite-tab-git').click();
    // 2. Enter 'http://insecure/repo.git' in clone-url-input
    await page.getByTestId('clone-url-input').fill('http://insecure/repo.git');
    // 3. Click clone-submit
    await page.getByTestId('clone-submit').click();
    // 4. Verify clone-feedback banner appears with error message
    await expect(page.getByTestId('clone-feedback')).toBeVisible();
    await expect(page.getByTestId('clone-feedback')).toContainText(/unsupported|error|reject|not allowlisted|invalid|insecure/i);
  });

  // ── 6.6 Close add suite modal via X button ───────────────────────────────

  test('Close add suite modal via X button', async ({ page }) => {
    // 1. Open add suite modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();

    // 2. Click the X close button
    await page.getByTestId('add-suite-modal-close').click();

    // 3. Verify modal is no longer attached (Semi UI removes modal from DOM)
    await expect(page.getByTestId('add-suite-modal')).not.toBeAttached();
  });

  // ── 6.7 Close add suite modal via Escape key ─────────────────────────────

  test('Close add suite modal via Escape key', async ({ page }) => {
    // 1. Open add suite modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();

    // 2. Press Escape
    await page.keyboard.press('Escape');

    // 3. Verify modal is no longer attached
    await expect(page.getByTestId('add-suite-modal')).not.toBeAttached();
  });

  // ── 6.8 Close add suite modal via mask click ─────────────────────────────

  test('Close add suite modal via mask click', async ({ page }) => {
    // 1. Open add suite modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();

    // 2. Click outside the panel. Semi UI's wrap receives the pointer event
    // above the visual mask layer.
    await page.locator('.semi-modal-wrap').click({ position: { x: 10, y: 10 } });

    // 3. Verify modal is no longer attached
    await expect(page.getByTestId('add-suite-modal')).not.toBeAttached();
  });

  // ── 6.9 Tab switching preserves form state ──────────────────────────

  test('Tab switching preserves form state (input value retained across tab switches)', async ({ page }) => {
    // 1. Open add suite modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();

    // 2. Local tab is active by default, enter a path
    await page.getByTestId('link-path-input').fill('/tmp/test-path');

    // 3. Switch to Git tab
    await page.getByTestId('suite-tab-git').click();
    await expect(page.getByTestId('clone-url-input')).toBeVisible();

    // 4. Switch back to Local tab
    await page.getByTestId('suite-tab-local').click();

    // 5. Verify the path value is retained (component state persists across tab switches)
    await expect(page.getByTestId('link-path-input')).toHaveValue('/tmp/test-path');
  });

  // ── 6.10 Successful local link submission shows success feedback ────

  test('Successful local link submission shows success feedback', async ({ page }) => {
    // 1. Mock POST /tests/link to return success
    await page.route('**/tests/link', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true, is_accessible: true, message: 'Suite linked successfully' }),
        });
      } else {
        await route.fallback();
      }
    });

    // 2. Open modal
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();

    // 3. Enter valid path
    await page.getByTestId('link-path-input').fill('/tmp/test-suite');

    // 4. Click link-submit
    await page.getByTestId('link-submit').click();

    // 5. Verify success feedback banner is visible
    await expect(page.getByText('Suite linked successfully')).toBeVisible();
  });

  // ── 6.11 Credential creation — save new credential ────────────────

  test('Credential creation — save new credential', async ({ page }) => {
    // 1. Mock credentials endpoints
    const mockCredentials = { credentials: [] as Array<{ id: string; name: string; type: string }> };

    await page.route('**/credentials', async (route) => {
      if (route.request().method() === 'POST') {
        const body = JSON.parse(route.request().postData() || '{}');
        const newCred = { id: 'cred-001', name: body.name, type: body.type || 'https_token' };
        mockCredentials.credentials.push(newCred);
        await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(newCred) });
      } else if (route.request().method() === 'GET') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockCredentials) });
      } else {
        await route.fallback();
      }
    });

    // 2. Open modal and switch to Git tab
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    await page.getByTestId('suite-tab-git').click();

    // 3. Fill new credential name
    await page.getByTestId('clone-newcred-name').fill('My GitHub Token');

    // 4. Fill new credential secret
    await page.getByTestId('clone-newcred-secret').fill('ghp_test123token');

    // 5. Click save
    await page.getByTestId('clone-newcred-save').click();

    // 6. Verify credential appears in clone-cred-select dropdown
    await expect(page.getByTestId('clone-cred-select')).toContainText('My GitHub Token');
  });

  test('Credential list load failure shows an error on the Git tab', async ({ page }) => {
    await page.route('**/credentials', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"credentials failed"}' });
      } else {
        await route.fallback();
      }
    });
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    await page.getByTestId('suite-tab-git').click();

    await expect(page.getByTestId('credential-load-error')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('credential-load-error')).toContainText(/Unable to load|无法加载/);
    await expect(page.getByTestId('clone-cred-select')).toBeVisible();
  });

  // ── 6.12 Network error during clone shows error feedback ──────────

  test('Network error during clone shows error feedback', async ({ page }) => {
    // 1. Mock POST /tests/clone to abort (simulate network failure)
    await page.route('**/tests/clone', async (route) => {
      if (route.request().method() === 'POST') {
        await route.abort('connectionrefused');
      } else {
        await route.fallback();
      }
    });

    // 2. Open modal and switch to Git tab
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('add-suite-modal')).toBeAttached();
    await page.getByTestId('suite-tab-git').click();

    // 3. Enter a clone URL
    await page.getByTestId('clone-url-input').fill('https://github.com/user/repo.git');

    // 4. Click clone-submit
    await page.getByTestId('clone-submit').click();

    // 5. Verify clone-feedback banner shows error message
    await expect(page.getByTestId('clone-feedback')).toBeVisible();
    await expect(page.getByTestId('clone-feedback')).toContainText(/error|failed|network|refused|Failed/i);
  });
});
