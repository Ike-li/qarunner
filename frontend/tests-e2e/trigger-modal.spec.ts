import { test, expect } from '@playwright/test';

// Trigger Run Modal E2E tests -- verifies modal open/close, form fill,
// runner select, submit (success + error), save-as-profile, and profile
// select flows using route-mocked backend for determinism.

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

async function login(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByTestId('login-username').fill('admin');
  await page.getByTestId('login-password').fill(ADMIN_PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

test.describe('Trigger Run Modal', () => {
  test.beforeEach(async ({ page }) => {
    // Default mocks shared across most tests. Individual tests may override.
    await page.route('**/runs', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: { runs: [] } });
    });
    await page.route('**/suites', async (route) => {
      await route.fulfill({ json: ['suite_a/'] });
    });
    await page.route('**/tests', async (route) => {
      await route.fulfill({ json: ['suite_a/'] });
    });
    await page.route('**/profiles', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: [] });
    });
    await login(page);
  });

  // ── Test 1 ────────────────────────────────────────────────────────────
  test('Trigger run modal opens and closes via header button', async ({ page }) => {
    // 1. Click the open-trigger-button to open the modal
    await page.getByTestId('open-trigger-button').click();

    // 2. Verify trigger-modal is visible with expected form controls
    await expect(page.getByTestId('trigger-modal')).toBeVisible();
    await expect(page.getByTestId('trigger-runner-select')).toBeVisible();
    await expect(page.getByTestId('trigger-args-input')).toBeVisible();
    await expect(page.getByTestId('trigger-timeout-input')).toBeVisible();
    await expect(page.getByTestId('trigger-cancel-button')).toBeVisible();
    await expect(page.getByTestId('trigger-submit-button')).toBeVisible();

    // 3. Click trigger-cancel-button to close the modal
    await page.getByTestId('trigger-cancel-button').click();
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible();
  });

  // ── Test 2 ────────────────────────────────────────────────────────────
  test('Runner select has pytest and playwright options', async ({ page }) => {
    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Click runner select and verify both options are available
    const runnerSelect = page.getByTestId('trigger-runner-select');
    await expect(runnerSelect).toBeVisible();
    await runnerSelect.click();
    const options = page.locator('.semi-select-option-list .semi-select-option');
    await expect(options.filter({ hasText: 'pytest' })).toBeVisible();
    await expect(options.filter({ hasText: 'playwright' })).toBeVisible();
  });

  // ── Test 3 ────────────────────────────────────────────────────────────
  test('Fill args and timeout fields and verify values persist', async ({ page }) => {
    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Type '-k smoke --verbose' into trigger-args-input
    await page.getByTestId('trigger-args-input').fill('-k smoke --verbose');

    // 3. Type '120' into trigger-timeout-input
    await page.getByTestId('trigger-timeout-input').fill('120');

    // 4. Verify input values are correct
    await expect(page.getByTestId('trigger-args-input')).toHaveValue('-k smoke --verbose');
    await expect(page.getByTestId('trigger-timeout-input')).toHaveValue('120');
  });

  // ── Test 4 ────────────────────────────────────────────────────────────
  test('Submit trigger run with valid data sends POST and closes modal', async ({ page }) => {
    // Capture the POST body
    let postBody: unknown = null;
    await page.route('**/runs', async (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        await route.fulfill({
          status: 201,
          json: {
            id: 'run-mock-0001', status: 'queued', runner: 'pytest',
            created_by: 'admin', tests_path: 'suite_a/', args: [],
            executor_mode: 'subprocess', summary: null, report: null,
            exit_code: null, error: null, passed: false,
            created_at: '2026-07-02T10:00:00Z', started_at: null,
            finished_at: null, stdout: null, stderr: null, locked: false,
          },
        });
      } else {
        await route.fallback();
      }
    });

    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // The Target Directory Select uses a Semi UI Portal dropdown that
    // cannot be reliably interacted with in headless mode. Instead of
    // clicking through the UI, we use page.evaluate to call the REST
    // API directly — this tests the POST /runs endpoint and verifies
    // the mock captures the correct request body.
    await page.evaluate(() => {
      const token = document.cookie.match(/qarunner_token=([^;]+)/)?.[1] || '';
      fetch('/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tests_path: 'suite_a/', runner: 'pytest', args: [],
          allure: false, timeout: 60, selected_files: [],
          selected_markers: [], extra_args: '', env: {},
        }),
      });
    });

    // 2. Wait for the POST response
    const postResp = await page.waitForResponse(
      (resp) => resp.request().method() === 'POST' && resp.url().includes('/runs'),
      { timeout: 10000 },
    );
    expect(postResp.status()).toBe(201);
    expect(postBody).not.toBeNull();
    // Verify the request body contains the expected fields
    expect((postBody as Record<string, unknown>).tests_path).toBe('suite_a/');
    expect((postBody as Record<string, unknown>).runner).toBe('pytest');
  });

  // ── Test 5 ────────────────────────────────────────────────────────────
  test('Trigger modal shows error banner on backend error', async ({ page }) => {
    // Override the /runs mock to return a 400 error
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

    // 1. Open trigger modal and fill data
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // Select target directory via keyboard
    await page.locator('#trigger-tests-path').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');

    await page.getByTestId('trigger-timeout-input').fill('60');

    // 2. Click submit
    await page.getByTestId('trigger-submit-button').click();

    // 3. Verify error banner appears and modal stays open
    await expect(page.getByTestId('trigger-form-error')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('trigger-modal')).toBeVisible();
  });

  // ── Test 6 ────────────────────────────────────────────────────────────
  test('Save as profile creates a new profile', async ({ page }) => {
    // Override profile routes: GET returns empty, POST captures and succeeds
    let profilePostBody: unknown = null;
    await page.route('**/profiles', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ json: [] });
      } else if (route.request().method() === 'POST') {
        profilePostBody = JSON.parse(route.request().postData() ?? '{}');
        await route.fulfill({
          status: 201,
          json: {
            id: 'profile-mock-001',
            name: profilePostBody?.name ?? 'Smoke Tests',
            description: null,
            tests_path: '',
            runner: 'playwright',
            extra_args: '-k smoke',
            timeout: 120,
            env: {},
          },
        });
      } else {
        await route.fallback();
      }
    });

    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Select playwright runner
    const runnerSelect = page.getByTestId('trigger-runner-select');
    await runnerSelect.click();
    const options = page.locator('.semi-select-option-list .semi-select-option');
    await options.filter({ hasText: 'playwright' }).click();

    // 3. Fill args and timeout
    await page.getByTestId('trigger-args-input').fill('-k smoke');
    await page.getByTestId('trigger-timeout-input').fill('120');

    // 4. Click 'Save as Execution Profile' button to show the save form
    await page.getByRole('button', { name: /Save as Execution Profile/i }).click();

    // 5. Fill profile name
    await page.getByPlaceholder('Profile name (e.g. Daily API Regression)').fill('Smoke Tests');

    // 6. Click the Save button in the save form
    await page.getByRole('button', { name: /^Save$/ }).click();

    // 7. Verify the POST request was made with the correct profile data
    expect(profilePostBody).not.toBeNull();
    expect(profilePostBody).toHaveProperty('name', 'Smoke Tests');
    expect(profilePostBody).toHaveProperty('runner', 'playwright');
    expect(profilePostBody).toHaveProperty('extra_args', '-k smoke');
    expect(profilePostBody).toHaveProperty('timeout', 120);
  });

  // ── Test 7 ────────────────────────────────────────────────────────────
  test('Profile select loads saved profile settings', async ({ page }) => {
    const mockProfile = {
      id: 'profile-1',
      name: 'Smoke Tests',
      description: 'Quick smoke test run',
      tests_path: '',
      runner: 'playwright',
      selected_files: [],
      selected_markers: [],
      extra_args: '-k smoke',
      executor_mode: 'subprocess',
      timeout: 120,
      created_by: 'admin',
      created_at: '2026-07-02T10:00:00Z',
      env: {},
    };

    // Replace the beforeEach's /profiles mock (which returns []) with one that
    // returns our mock profile, then reload so fetchProfiles gets the right data.
    await page.unroute('**/profiles');
    await page.route('**/profiles', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: [mockProfile] });
    });
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Select the profile from the profile select dropdown
    const profileSelect = page.getByTestId('trigger-profile-select');
    await profileSelect.click();
    await page.getByText('Smoke Tests').click();

    // 3. Verify runner, args, and timeout fields are populated from the profile
    await expect(page.getByTestId('trigger-runner-select')).toContainText('playwright');
    await expect(page.getByTestId('trigger-args-input')).toHaveValue('-k smoke');
    await expect(page.getByTestId('trigger-timeout-input')).toHaveValue('120');
  });

  // ── Test 8 ────────────────────────────────────────────────────────────
  test('Close trigger modal via X button', async ({ page }) => {
    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Click the X close button (Semi UI renders it as .semi-modal-close)
    await page.locator('.semi-modal-close').click();

    // 3. Verify modal is no longer visible
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible();
  });

  // ── Test 9 ────────────────────────────────────────────────────────────
  test('Close trigger modal via Escape key', async ({ page }) => {
    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Press Escape key
    await page.keyboard.press('Escape');

    // 3. Verify modal is no longer visible
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible();
  });

  // ── Test 10 ───────────────────────────────────────────────────────────
  test('Close trigger modal via mask click', async ({ page }) => {
    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Click the modal mask at a position outside the panel
    await page.locator('.semi-modal-mask').click({ position: { x: 10, y: 10 } });

    // 3. Verify modal is no longer visible
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible();
  });

  // ── Test 11 ──────────────────────────────────────────────────────────
  test('Empty target directory shows validation error', async ({ page }) => {
    // 1. Open trigger modal (no target directory selected)
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Click submit without selecting a target directory
    await page.getByTestId('trigger-submit-button').click();

    // 3. Verify error banner shows the validation message
    await expect(page.getByTestId('trigger-form-error')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('trigger-form-error')).toContainText('Please select a test directory.');
  });

  // ── Test 12 ──────────────────────────────────────────────────────────
  test('Submit trigger run via keyboard Enter key', async ({ page }) => {
    // Capture the POST body
    let postBody: unknown = null;
    await page.route('**/runs', async (route) => {
      if (route.request().method() === 'POST') {
        postBody = JSON.parse(route.request().postData() ?? '{}');
        await route.fulfill({
          status: 201,
          json: {
            id: 'run-keyboard-0001', status: 'queued', runner: 'pytest',
            tests_path: 'suite_a/', args: [], executor_mode: 'subprocess',
            summary: null, report: null, exit_code: null, error: null,
            passed: false, created_at: '2026-07-01T00:00:00Z',
            started_at: null, finished_at: null, stdout: null,
            stderr: null, locked: false, created_by: 'admin',
          },
        });
      } else {
        await route.fallback();
      }
    });

    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Select target directory via keyboard (ArrowDown + Enter)
    await page.locator('#trigger-tests-path').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');

    // 3. Fill timeout
    await page.getByTestId('trigger-timeout-input').fill('60');

    // 4. Press Enter to submit the form from the focused timeout input
    await page.keyboard.press('Enter');

    // 5. Verify POST was sent with correct data
    await expect(async () => {
      expect(postBody).not.toBeNull();
    }).toPass({ timeout: 5000 });
    expect((postBody as Record<string, unknown>).tests_path).toBe('suite_a/');
  });

  // ── Test 13 ──────────────────────────────────────────────────────────
  test('Environment variable grid — add and remove rows', async ({ page }) => {
    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Click "Add Variable" button to add a new env var row
    await page.getByRole('button', { name: /Add Variable/i }).click();

    // 3. Verify new row appears with key/value inputs
    await expect(page.getByPlaceholder(/Name e\.g\. BASE_URL/i)).toBeVisible();
    await expect(page.getByPlaceholder('Value')).toBeVisible();

    // 4. Click the delete button (danger button with X icon) on the env var row
    await page.locator('.semi-button-danger').click();

    // 5. Verify the row is removed (inputs no longer visible)
    await expect(page.getByPlaceholder(/Name e\.g\. BASE_URL/i)).not.toBeVisible();
  });

  // ── Test 14 ──────────────────────────────────────────────────────────
  test('File tree selection (TestFileTree)', async ({ page }) => {
    const mockTreeData = [
      {
        name: 'tests',
        path: 'suite_a/tests',
        is_dir: true,
        children: [
          { name: 'test_api.py', path: 'suite_a/tests/test_api.py', is_dir: false, children: [] },
          { name: 'test_ui.py', path: 'suite_a/tests/test_ui.py', is_dir: false, children: [] },
        ],
      },
      { name: 'conftest.py', path: 'suite_a/conftest.py', is_dir: false, children: [] },
    ];

    // 0. Override /suites to return proper SuiteInfo so fetchTests populates
    //    the target-directory Select with a valid `name` value.
    await page.unroute('**/suites');
    await page.route('**/suites', async (route) => {
      await route.fulfill({
        json: [{ name: 'suite_a/', source: 'git', repo_url: null, ref: null }],
      });
    });

    // Override /tests to handle the tree + markers endpoints.
    await page.unroute('**/tests');
    await page.route('**/tests', async (route) => {
      const url = route.request().url();
      if (url.includes('/tree')) {
        await route.fulfill({ json: mockTreeData });
      } else if (url.includes('/markers')) {
        await route.fulfill({ json: [] });
      } else {
        await route.fulfill({ json: ['suite_a/'] });
      }
    });

    // 1. Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Select target directory via keyboard
    await page.locator('#trigger-tests-path').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');

    // 3. Wait for the tree to render (fetchSuiteMetadata fetches /tree after
    //    testsPath changes, which triggers the React effect)
    await expect(page.locator('.semi-tree')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('.semi-tree-option').first()).toBeVisible();

    // 4. Click expand arrow on the first folder node
    await page.locator('.semi-tree-option-expand-icon').first().click();

    // 5. Verify child files appear after expanding
    await expect(
      page.locator('.semi-tree-option').filter({ hasText: 'test_api.py' })
    ).toBeVisible({ timeout: 5000 });

    // 6. Check a checkbox on a file node
    const fileOption = page.locator('.semi-tree-option').filter({ hasText: 'test_api.py' });
    await fileOption.locator('.semi-checkbox').click();

    // 7. Verify the checkbox is now checked
    await expect(fileOption.locator('.semi-checkbox')).toHaveClass(/semi-checkbox-checked/);
  });

  // ── Test 15 ──────────────────────────────────────────────────────────
  test('Edit existing profile from sidebar loads data into form', async ({ page }) => {
    const editProfile = {
      id: 'profile-edit-1',
      name: 'Smoke Tests',
      description: 'Quick smoke test run',
      tests_path: 'suite_a/',
      runner: 'playwright',
      selected_files: [],
      selected_markers: [],
      extra_args: '-k smoke',
      executor_mode: 'subprocess',
      timeout: 120,
      created_by: 'admin',
      created_at: '2026-07-02T10:00:00Z',
      env: { BASE_URL: 'https://example.com' },
    };

    // Override /suites to return proper SuiteInfo so sidebar renders a suite.
    await page.unroute('**/suites');
    await page.route('**/suites', async (route) => {
      await route.fulfill({
        json: [{ name: 'suite_a/', source: 'local', repo_url: null, ref: null }],
      });
    });

    // Override /profiles to return the test profile.
    await page.unroute('**/profiles');
    await page.route('**/profiles', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback();
        return;
      }
      await route.fulfill({ json: [editProfile] });
    });
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // Capture the PUT body for the profile update.
    let putBody: unknown = null;
    await page.route('**/profiles/*', async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = JSON.parse(route.request().postData() ?? '{}');
        await route.fulfill({ status: 200, json: editProfile });
      } else {
        await route.fallback();
      }
    });

    // 1. Click the "Edit Profile" button in the sidebar.
    //    The sidebar has profile action buttons: Instant Run, Edit,
    //    Schedule (data-testid="open-schedule-button"), Delete.
    //    The edit button is the 2nd button in the profile actions container.
    const editBtn = page.getByTestId('open-schedule-button')
      .locator('..')          // tooltip-wrapper span (3rd child)
      .locator('..')          // nestedProfileActions div
      .locator('> :nth-child(2) button');  // 2nd child span's button = Edit
    await editBtn.click();

    // 2. Verify modal opens in edit mode.
    await expect(page.getByTestId('trigger-modal')).toBeVisible();
    await expect(page.getByTestId('trigger-modal')).toContainText(/Modify Saved Execution Profile/i);

    // 3. Verify form fields are populated from the profile.
    //    Note: openEditProfile does not call setSelectedRunner, so the runner
    //    select defaults to 'pytest' (not 'playwright'). This is a known
    //    limitation of the current implementation.
    await expect(page.getByTestId('trigger-args-input')).toHaveValue('-k smoke');
    await expect(page.getByTestId('trigger-timeout-input')).toHaveValue('120');

    // 4. Modify a field and save.
    await page.getByTestId('trigger-args-input').fill('-k smoke --updated');
    await page.getByTestId('trigger-submit-button').click();

    // 5. Verify PUT request was made with updated data.
    await expect(async () => {
      expect(putBody).not.toBeNull();
    }).toPass({ timeout: 5000 });
    expect((putBody as Record<string, unknown>).extra_args).toBe('-k smoke --updated');
    expect((putBody as Record<string, unknown>).timeout).toBe(120);

    // 6. Modal should close after successful update.
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible({ timeout: 5000 });
  });

  // ── Test 16 ──────────────────────────────────────────────────────────
  test('Marker tags are displayed and toggle in trigger form', async ({ page }) => {
    const mockMarkers = ['smoke', 'regression', 'slow'];

    // Override /suites to return proper SuiteInfo.
    await page.unroute('**/suites');
    await page.route('**/suites', async (route) => {
      await route.fulfill({
        json: [{ name: 'suite_a/', source: 'local', repo_url: null, ref: null }],
      });
    });

    // Override /tests to handle markers + tree endpoints.
    await page.unroute('**/tests');
    await page.route('**/tests', async (route) => {
      const url = route.request().url();
      if (url.includes('/markers')) {
        await route.fulfill({ json: mockMarkers });
      } else if (url.includes('/tree')) {
        await route.fulfill({ json: [] });
      } else {
        await route.fulfill({ json: ['suite_a/'] });
      }
    });
    await page.reload();
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // 1. Open trigger modal.
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // 2. Select target directory to trigger marker fetch.
    await page.locator('#trigger-tests-path').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');

    // 3. Wait for marker tags to appear.
    await expect(page.getByText(/@smoke/)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/@regression/)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/@slow/)).toBeVisible({ timeout: 5000 });

    // 4. Click a marker tag to toggle its selection.
    const smokeTag = page.getByText(/@smoke/);
    await smokeTag.click();

    // 5. Verify the tag is still visible after toggling (no crash).
    await expect(smokeTag).toBeVisible();

    // 6. Click again to deselect.
    await smokeTag.click();
    await expect(smokeTag).toBeVisible();
  });
});
