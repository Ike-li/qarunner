// A11y Phase 3 — Form semantics, focus indicators, and icon button a11y names.
//
// A3.1  每个 input 有可访问 label 关联
// A3.2  错误提示与字段关联 (role="alert" / aria-live)
// A3.3  可聚焦元素有可见焦点指示器
// A3.4  图标按钮有可访问名 (aria-label)
//
// Runs under `chromium-authed-admin`.
// Known gaps marked with test.fixme — these require product code changes.

import { test, expect } from '@playwright/test';

// ── A3.1  Form input label associations ──────────────────────────────────

test.describe('A3.1 表单 input label 关联', () => {
  test('LoginScreen inputs 有 htmlFor 关联', async ({ page }) => {
    // Clear auth to get login screen
    await page.context().clearCookies();
    await page.goto('/');
    await page.reload();
    await expect(page.getByTestId('login-title')).toBeVisible();

    // Username: label[for="login-username"] + input#login-username
    const usernameLabel = page.locator('label[for="login-username"]');
    await expect(usernameLabel).toBeAttached();

    // Password: label[for="login-password"] + input#login-password
    const passwordLabel = page.locator('label[for="login-password"]');
    await expect(passwordLabel).toBeAttached();
  });

  test('TriggerRunModal 主字段有 label 关联', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Open trigger modal
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // Check labeled fields: testsPath, runner, profile, args, timeout, allure
    for (const id of [
      'trigger-tests-path',
      'trigger-runner',
      'trigger-profile',
      'trigger-args',
      'trigger-timeout',
      'trigger-allure',
    ]) {
      const label = page.locator(`label[for="${id}"]`);
      await expect(label).toBeAttached();
    }
  });

  test.fixme(
    'TriggerRunModal env var inputs 和 profile name/desc 无 label',
    async ({ page }) => {
      // Known gap: env var key/value inputs and profile name/desc have no labels.
      // After adding aria-label, these assertions should pass.
      await page.goto('/');
      await page.getByTestId('open-trigger-button').click();
      await expect(page.getByTestId('trigger-modal')).toBeVisible();

      // TODO: assert env var inputs have aria-label
      // TODO: assert profile name/desc inputs have aria-label
    },
  );

  test.fixme('AddSuiteModal inputs 无 label', async ({ page }) => {
    // Known gap: all 6 inputs (local path, git URL, git ref, credential, new cred name, secret)
    // have no programmatic labels — placeholder only.
    await page.goto('/');
    await page.getByTestId('open-trigger-button').click();
    // TODO: open AddSuiteModal and assert each input has label/aria-label
  });

  test.fixme('ScheduleModal inputs 无 label', async ({ page }) => {
    // Known gap: name, cron, timezone inputs have no labels.
    await page.goto('/');
    // TODO: open ScheduleModal and assert each input has label/aria-label
  });

  test.fixme('UserManagementModal inputs 无 label', async ({ page }) => {
    // Known gap: username, password, role, retention days inputs have no labels.
    await page.goto('/');
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeVisible();
    // TODO: assert each input has label/aria-label
  });
});

// ── A3.2  Error messages associated with fields ──────────────────────────

test.describe('A3.2 错误提示关联', () => {
  test('LoginScreen 错误有 role="alert"', async ({ page }) => {
    // Clear auth to get login screen
    await page.context().clearCookies();
    await page.goto('/');
    await page.reload();
    await expect(page.getByTestId('login-title')).toBeVisible();

    // Submit with wrong credentials to trigger error
    await page.getByTestId('login-username').fill('wrong');
    await page.getByTestId('login-password').fill('wrong');
    await page.getByTestId('login-submit').click();

    // LoginScreen uses a custom div with role="alert"
    const error = page.getByTestId('login-error');
    await expect(error).toBeVisible({ timeout: 5_000 });
    await expect(error).toHaveAttribute('role', 'alert');
  });

  test.fixme('TriggerRunModal 错误有 role="alert"', async ({ page }) => {
    // Known gap: uses Semi <Banner> without explicit role="alert".
    await page.goto('/');
    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // Submit empty form
    await page.getByTestId('trigger-submit-button').click();

    const error = page.getByTestId('trigger-form-error');
    await expect(error).toBeVisible();
    // TODO: after adding role="alert" to Banner, assert it
    // await expect(error).toHaveAttribute('role', 'alert');
  });

  test.fixme('其他 Modal 错误有 role="alert"', async ({ page }) => {
    // Known gap: ScheduleModal, UserManagementModal, AddSuiteModal
    // use Semi <Banner> without role="alert".
  });
});

// ── A3.3  Focus indicators ──────────────────────────────────────────────

test.describe('A3.3 焦点指示器', () => {
  test('LoginScreen input focus 有可见指示器', async ({ page }) => {
    // Clear auth to get login screen
    await page.context().clearCookies();
    await page.goto('/');
    await page.reload();
    await expect(page.getByTestId('login-title')).toBeVisible();

    const usernameInput = page.getByTestId('login-username');
    await usernameInput.focus();
    await expect(usernameInput).toBeFocused();

    // Check that focus produces a visible indicator (outline or box-shadow)
    const styles = await usernameInput.evaluate((el) => {
      const s = getComputedStyle(el);
      return { outlineStyle: s.outlineStyle, boxShadow: s.boxShadow };
    });
    // At least one should be non-none
    const hasIndicator =
      styles.outlineStyle !== 'none' || styles.boxShadow !== 'none';
    expect(hasIndicator).toBe(true);
  });

  test('Header 按钮 focus 有可见指示器', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Check the trigger button (has text, easy to locate)
    const triggerBtn = page.getByTestId('open-trigger-button');
    await triggerBtn.focus();
    await expect(triggerBtn).toBeFocused();

    // Semi UI Button: check if focus ring is visible
    const styles = await triggerBtn.evaluate((el) => {
      const s = getComputedStyle(el);
      return { outlineStyle: s.outlineStyle, boxShadow: s.boxShadow };
    });
    const hasIndicator =
      styles.outlineStyle !== 'none' || styles.boxShadow !== 'none';
    // Note: Semi UI may or may not show focus ring depending on theme.
    // This assertion documents the current state.
    expect(hasIndicator).toBe(true);
  });

  test.fixme('所有可聚焦元素有 :focus-visible 指示器', async ({ page }) => {
    // Known gap: no :focus-visible CSS rule exists in the codebase.
    // Semi UI components rely on library defaults.
    // After adding a global :focus-visible rule, this should pass.
  });
});

// ── A3.4  Icon buttons have accessible names ────────────────────────────

test.describe('A3.4 图标按钮可访问名', () => {
  test('LoginScreen icon 按钮有 aria-label', async ({ page }) => {
    // Clear auth to get login screen
    await page.context().clearCookies();
    await page.goto('/');
    await page.reload();
    await expect(page.getByTestId('login-title')).toBeVisible();

    // Theme toggle: has aria-label with "Switch to"
    const themeBtn = page.getByRole('button', { name: /switch to.*mode/i });
    await expect(themeBtn).toBeAttached();
    const themeLabel = await themeBtn.getAttribute('aria-label');
    expect(themeLabel).toBeTruthy();

    // Language toggle: has aria-label with "切换" or "Switch to English"
    const langBtn = page.getByRole('button', {
      name: /切换为中文|switch to english/i,
    });
    await expect(langBtn).toBeAttached();
    const langLabel = await langBtn.getAttribute('aria-label');
    expect(langLabel).toBeTruthy();
  });

  test('FullscreenOverlay icon 按钮有 aria-label', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Open drawer
    const firstCode = page.locator('table tbody code').first();
    await firstCode.click();
    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({
      timeout: 10_000,
    });

    // Open fullscreen terminal
    const terminalBtn = page
      .locator('button', { hasText: /^Fullscreen$|^全屏终端$/ })
      .first();
    const hasTerminal = await terminalBtn
      .isVisible({ timeout: 3_000 })
      .catch(() => false);

    if (hasTerminal) {
      await terminalBtn.click();
      const dialog = page.locator('[role="dialog"][aria-modal="true"]');
      await expect(dialog).toBeVisible({ timeout: 5_000 });

      // Check icon buttons inside the overlay
      // Close button should have aria-label
      const closeBtn = dialog.locator('button').last();
      const closeLabel = await closeBtn.getAttribute('aria-label');
      expect(closeLabel).toBeTruthy();

      // Font increase/decrease buttons should have aria-label
      const fontBtns = dialog.locator(
        'button[aria-label*="font"], button[aria-label*="Font"]',
      );
      const fontCount = await fontBtns.count();
      expect(fontCount).toBeGreaterThanOrEqual(2);

      // Esc to close
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden({ timeout: 5_000 });
    }
  });

  test.fixme('Header theme/logout 按钮无 aria-label', async ({ page }) => {
    // Known gap: Header theme toggle and logout button use <Tooltip> but
    // Tooltip does NOT inject aria-label. Screen readers cannot identify them.
    // After adding aria-label to these buttons, this test should pass.
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // TODO: assert theme button has aria-label
    // TODO: assert logout button has aria-label
  });

  test.fixme('Sidebar icon 按钮无 aria-label', async ({ page }) => {
    // Known gap: All sidebar icon buttons (add suite, quick play, git pull,
    // git prepare, remove suite, profile run/edit/schedule/delete) use
    // <Tooltip> without aria-label.
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // TODO: assert each sidebar icon button has aria-label
  });

  test.fixme('FullscreenTerminalOverlay 部分按钮无 aria-label', async ({
    page,
  }) => {
    // Known gap: Word Wrap toggle and Scroll Lock toggle have title but no aria-label.
    await page.goto('/');
    // TODO: open fullscreen terminal, assert word-wrap and scroll-lock have aria-label
  });

  test.fixme('RunDetailsDrawer fullscreen 按钮无 aria-label', async ({
    page,
  }) => {
    // Known gap: fullscreen terminal button has title but no aria-label.
    await page.goto('/');
    // TODO: open drawer, assert fullscreen button has aria-label
  });

  test.fixme('TriggerRunModal delete profile 按钮无 aria-label', async ({
    page,
  }) => {
    // Known gap: delete profile icon button has title but no aria-label.
    await page.goto('/');
    // TODO: open trigger modal with a saved profile, assert delete button has aria-label
  });

  test.fixme('UserManagementModal delete user 按钮无 aria-label', async ({
    page,
  }) => {
    // Known gap: delete user icon button has no title or aria-label.
    await page.goto('/');
    // TODO: open user modal, assert delete button has aria-label
  });
});
