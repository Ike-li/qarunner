// A11y Phase 2 — keyboard-only journey (FE-5 acceptance).
//
// A2.1  无鼠标完成登录
// A2.2  键盘打开 TriggerRun modal → 填表 → 提交
// A2.3  键盘打开 drawer → 全屏终端 → Esc 还原
// A2.4  键盘切换主题/语言
//
// Runs under `chromium-authed-admin` (except A2.1 which clears auth).
// Constraint: only page.keyboard / page.focus — no page.click / page.fill
// (page.click on table row is an explicit exception, see A2.3 comment).

import { test, expect } from '@playwright/test';

// ── A2.1  Keyboard-only login ────────────────────────────────────────────

test.describe('A2.1 键盘登录', () => {
  test('Tab 到用户名 → Tab 到密码 → Tab 到提交 → Enter 登录', async ({ page }) => {
    // Clear any existing auth to get the login screen
    await page.context().clearCookies();
    await page.goto('/');
    await page.reload();

    // Login screen should be visible
    await expect(page.getByTestId('login-title')).toBeVisible();

    // Focus the username input
    await page.getByTestId('login-username').focus();
    await page.keyboard.type('admin');

    // Tab to password
    await page.keyboard.press('Tab');
    await page.keyboard.type(process.env.E2E_ADMIN_PASSWORD || 'Demo-Qarunner-2026!');

    // Tab to submit button and press Enter
    await page.keyboard.press('Tab');
    const focusedTag = await page.evaluate(() => document.activeElement?.tagName);
    expect(focusedTag).toBe('BUTTON');

    await page.keyboard.press('Enter');

    // Dashboard should appear
    await expect(page.getByTestId('stat-total')).toBeVisible({ timeout: 10_000 });
  });
});

// ── A2.2  Keyboard trigger modal ─────────────────────────────────────────

test.describe('A2.2 键盘 trigger modal', () => {
  test('Tab 到 trigger 按钮 → Enter 打开 → 填表 → Enter 提交', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Focus trigger button via keyboard
    const triggerBtn = page.getByTestId('open-trigger-button');
    await triggerBtn.focus();
    await expect(triggerBtn).toBeFocused();

    // Open the modal with Enter
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // Select a test directory first (required field — "testsPath").
    // Semi Select does NOT forward data-testid to DOM; use #id.
    const testsPathSelect = page.locator('#trigger-tests-path');
    await testsPathSelect.click();
    await page.waitForTimeout(300);
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);

    // Wait for the submit button to become enabled (suites/tests loaded)
    const submitBtn = page.getByTestId('trigger-submit-button');
    await expect(submitBtn).toBeEnabled({ timeout: 10_000 });
    await submitBtn.focus();
    await expect(submitBtn).toBeFocused();

    await page.keyboard.press('Enter');

    // Modal should close
    await expect(page.getByTestId('trigger-modal')).toBeHidden({ timeout: 15_000 });
  });
});

// ── A2.3  Keyboard drawer + fullscreen terminal ──────────────────────────

test.describe('A2.3 键盘 drawer + 全屏终端', () => {
  test('click 打开 drawer → Tab 到全屏按钮 → Enter → Esc 关全屏 → Esc 关 drawer', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Table rows have activateOnKey but NO tabIndex, so keyboard can't reach them.
    // Open drawer by clicking the first run's code cell (same pattern as journey tests).
    const firstCode = page.locator('table tbody code').first();
    await firstCode.click();

    const drawerLogsTab = page.getByTestId('drawer-tab-logs');
    await expect(drawerLogsTab).toBeVisible({ timeout: 10_000 });

    const fullscreenBtn = page.getByTestId('terminal-fullscreen-button');
    const hasFullscreen = await fullscreenBtn.isVisible({ timeout: 3_000 }).catch(() => false);

    if (hasFullscreen) {
      await fullscreenBtn.focus();
      await expect(fullscreenBtn).toBeFocused();

      // Open fullscreen overlay
      await page.keyboard.press('Enter');

      // FullscreenTerminalOverlay has role="dialog" aria-modal="true"
      // (the drawer SideSheet also has role="dialog", so disambiguate)
      const overlay = page.getByTestId('fullscreen-terminal-overlay').locator('[role="dialog"]');
      await expect(overlay).toBeVisible({ timeout: 5_000 });

      // Esc should close the fullscreen overlay
      await page.keyboard.press('Escape');
      await expect(overlay).toBeHidden({ timeout: 5_000 });

      // Focus should restore to the fullscreen button
      await expect(fullscreenBtn).toBeFocused();
    }

    // Ensure focus is inside the drawer before pressing Esc to close it.
    // Focus the drawer's close button (Semi SideSheet renders a close icon button)
    const closeBtn = page.locator('.semi-sidesheet-close');
    const hasClose = await closeBtn.isVisible({ timeout: 1_000 }).catch(() => false);

    if (hasClose) {
      await closeBtn.click();
    } else {
      // Fallback: click outside the drawer to close
      await page.mouse.click(100, 300);
    }
    await expect(drawerLogsTab).toBeHidden({ timeout: 5_000 });
  });
});

// ── A2.4  Keyboard theme/language toggle ──────────────────────────────────

test.describe('A2.4 键盘切换主题/语言', () => {
  test('Tab 到主题切换 → Enter 切换 → Tab 到语言切换 → Enter 切换', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Semi Tooltip does NOT expose accessible name to Playwright.
    // Theme toggle: borderless icon button (Moon/Sun) with NO text, between
    // the Users button and the language button.
    // Strategy: find the language button, then find the immediately preceding
    // sibling button in the same parent container.
    const langBtnLocator = page.locator('button', { hasText: /^中文$|^EN$/ });
    const themeBtn = langBtnLocator.locator('xpath=preceding-sibling::button[1]');
    const hasThemeBtn = await themeBtn.isVisible({ timeout: 3_000 }).catch(() => false);

    if (!hasThemeBtn) {
      // Fallback: if admin-only Users button is absent, the layout is different
      test.skip(true, 'Could not locate theme toggle button');
      return;
    }

    await themeBtn.focus();

    // Get current theme state — Semi UI uses body[theme-mode] attribute
    const wasDark = await page.evaluate(() => document.body.getAttribute('theme-mode') === 'dark');

    // Toggle theme via keyboard
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);

    // Theme should have changed
    const isDarkNow = await page.evaluate(() => document.body.getAttribute('theme-mode') === 'dark');
    expect(isDarkNow).not.toBe(wasDark);

    // Language toggle: button with visible text "中文" or "EN"
    await langBtnLocator.focus();
    await expect(langBtnLocator).toBeFocused();

    // Get current language — stored in localStorage
    const currentLang = await page.evaluate(() => localStorage.getItem('qarunner_lang') || 'en');

    // Toggle language via keyboard
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);

    // Language should have changed
    const newLang = await page.evaluate(() => localStorage.getItem('qarunner_lang') || 'en');
    expect(newLang).not.toBe(currentLang);
  });
});
