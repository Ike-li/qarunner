// A11y Phase 1 — Modal/Drawer focus management (useDialogA11y contract).
//
// Tests the 4 invariants (F1-F4) defined in a11y-test-plan §阶段1:
//   F1: 打开后焦点进入容器
//   F2: Tab 陷阱（焦点不逃逸）
//   F3: Esc 关闭
//   F4: 焦点还原到触发器
//
// A1.6/A1.7 (FullscreenTerminalOverlay/FullscreenReportOverlay) — 已接 hook, 必绿
// A1.1-A1.5 (TriggerRunModal/AddSuite/Schedule/UserMgmt/Drawer) — 未接 hook, test.fixme
//
// Runs under `chromium-authed-admin`.

import { test, expect } from '@playwright/test';

// Helper: check if document.activeElement is inside a container
async function isFocusInside(page: import('@playwright/test').Page, selector: string) {
  return page.evaluate((sel) => {
    const container = document.querySelector(sel);
    if (!container) return false;
    const active = document.activeElement;
    if (!active) return false;
    return container === active || container.contains(active);
  }, selector);
}

// Helper: open drawer by clicking the first run row's code cell
async function openDrawerFromFirstRow(page: import('@playwright/test').Page) {
  const firstCode = page.locator('table tbody code').first();
  await firstCode.click();
  await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({ timeout: 10_000 });
}

// ── A1.6  FullscreenTerminalOverlay (已接 useDialogA11y) ──────────────────

test.describe('A1.6 FullscreenTerminalOverlay 焦点管理', () => {
  test('F1-F4: 焦点进入 → Tab 陷阱 → Esc 关闭 → 焦点还原', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Open drawer by clicking first run row
    await openDrawerFromFirstRow(page);

    // Look for the terminal fullscreen button (title="Fullscreen Terminal" or visible text "Fullscreen")
    const terminalBtn = page.locator('button', { hasText: /^Fullscreen$|^全屏终端$/ }).first();
    const hasTerminalBtn = await terminalBtn.isVisible({ timeout: 3_000 }).catch(() => false);

    if (!hasTerminalBtn) {
      test.skip(true, 'No terminal fullscreen button found — run may not have terminal output');
      return;
    }

    // Record trigger element for F4
    const triggerTitle = await terminalBtn.getAttribute('title');

    // F1: Open overlay — focus should enter the dialog.
    // Note: the drawer (SideSheet) also has role="dialog", so we need to
    // target the fullscreen overlay specifically.
    await terminalBtn.click();
    const dialog = page.locator('[role="dialog"][aria-modal="true"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    expect(await isFocusInside(page, '[role="dialog"][aria-modal="true"]')).toBe(true);

    // F2: Tab trap — Tab multiple times, focus should stay inside
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab');
    }
    expect(await isFocusInside(page, '[role="dialog"][aria-modal="true"]')).toBe(true);

    // F3: Esc closes the overlay
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden({ timeout: 5_000 });

    // F4: Focus restores to trigger element (the fullscreen button)
    const activeTitle = await page.evaluate(() => {
      const el = document.activeElement;
      return el?.getAttribute('title') || el?.textContent?.trim();
    });
    expect(activeTitle).toBe(triggerTitle);
  });
});

// ── A1.7  FullscreenReportOverlay (已接 useDialogA11y) ────────────────────

test.describe('A1.7 FullscreenReportOverlay 焦点管理', () => {
  test('F1-F4: 焦点进入 → Tab 陷阱 → Esc 关闭 → 焦点还原', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Open drawer
    await openDrawerFromFirstRow(page);

    // Switch to report tab
    const reportTab = page.getByTestId('drawer-tab-report');
    await expect(reportTab).toBeVisible({ timeout: 10_000 });
    await reportTab.click();
    await page.waitForTimeout(500);

    // Look for the fullscreen report button
    const fullscreenBtn = page.getByTestId('report-fullscreen-button');
    const hasBtn = await fullscreenBtn.isVisible({ timeout: 3_000 }).catch(() => false);

    if (!hasBtn) {
      test.skip(true, 'No report available — run may not have generated a report');
      return;
    }

    // Record trigger for F4
    await fullscreenBtn.focus();
    await expect(fullscreenBtn).toBeFocused();

    // F1: Open overlay
    await page.keyboard.press('Enter');
    const overlay = page.getByTestId('fullscreen-report-overlay');
    await expect(overlay).toBeVisible({ timeout: 5_000 });
    expect(await isFocusInside(page, '[role="dialog"]')).toBe(true);

    // F2: Tab trap
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab');
    }
    expect(await isFocusInside(page, '[role="dialog"]')).toBe(true);

    // F3: Esc closes
    await page.keyboard.press('Escape');
    await expect(overlay).toBeHidden({ timeout: 5_000 });

    // F4: Focus restores to trigger
    await expect(fullscreenBtn).toBeFocused();
  });
});

// ── A1.1-A1.5 未接 useDialogA11y — 标 fixme ──────────────────────────────

test.describe.fixme('A1.1 TriggerRunModal 焦点管理', () => {
  test('F1-F4 需接入 useDialogA11y 后才能绿', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('open-trigger-button').focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('trigger-modal')).toBeVisible();
    // TODO: After useDialogA11y is integrated, assert F1-F4
  });
});

test.describe.fixme('A1.2 AddSuiteModal 焦点管理', () => {
  test('F1-F4 需接入 useDialogA11y 后才能绿', async ({ page }) => {
    await page.goto('/');
    // TODO: Open AddSuiteModal via keyboard and assert F1-F4
  });
});

test.describe.fixme('A1.3 ScheduleModal 焦点管理', () => {
  test('F1-F4 需接入 useDialogA11y 后才能绿', async ({ page }) => {
    await page.goto('/');
    // TODO: Open ScheduleModal via keyboard and assert F1-F4
  });
});

test.describe.fixme('A1.4 UserManagementModal 焦点管理', () => {
  test('F1-F4 需接入 useDialogA11y 后才能绿', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('open-users-button').click();
    await expect(page.getByTestId('user-modal')).toBeVisible();
    // TODO: After useDialogA11y is integrated, assert F1-F4
  });
});

test.describe.fixme('A1.5 RunDetailsDrawer 焦点管理', () => {
  test('F1-F4 需接入 useDialogA11y 后才能绿', async ({ page }) => {
    await page.goto('/');
    // TODO: Open drawer via keyboard and assert F1-F4
  });
});
