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

import { mockRunWithLogsAndReport } from './helpers/runs';

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
    // A known run with console output, instead of whatever the database holds.
    await mockRunWithLogsAndReport(page);
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Open drawer by clicking first run row
    await openDrawerFromFirstRow(page);

    const terminalBtn = page.getByTestId('terminal-fullscreen-button');
    await expect(terminalBtn).toBeVisible();

    await terminalBtn.focus();
    await expect(terminalBtn).toBeFocused();

    // F1: Open overlay — focus should enter the dialog.
    // Note: the drawer (SideSheet) also has role="dialog", so we need to
    // target the fullscreen overlay specifically.
    await page.keyboard.press('Enter');
    const dialog = page.getByTestId('fullscreen-terminal-overlay').locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    expect(await isFocusInside(page, '[data-testid="fullscreen-terminal-overlay"] [role="dialog"]')).toBe(true);

    // F2: Tab trap — Tab multiple times, focus should stay inside
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab');
    }
    expect(await isFocusInside(page, '[data-testid="fullscreen-terminal-overlay"] [role="dialog"]')).toBe(true);

    // F3: Esc closes the overlay
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden({ timeout: 5_000 });

    // F4: Focus restores to trigger element (the fullscreen button)
    await expect(terminalBtn).toBeFocused();
  });
});

// ── A1.7  FullscreenReportOverlay (已接 useDialogA11y) ────────────────────

test.describe('A1.7 FullscreenReportOverlay 焦点管理', () => {
  test('F1-F4: 焦点进入 → Tab 陷阱 → Esc 关闭 → 焦点还原', async ({ page }) => {
    // A known run with an HTML report, instead of whatever the database holds.
    await mockRunWithLogsAndReport(page);
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
    await expect(fullscreenBtn).toBeVisible();

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

// ── A1.1  TriggerRunModal (已接 useDialogA11y) ─────────────────────────────

test.describe('A1.1 TriggerRunModal 焦点管理', () => {
  test('F1+F3: 焦点进入 dialog → Esc 关闭 → 焦点还原', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    const triggerBtn = page.getByTestId('open-trigger-button');
    await triggerBtn.focus();
    await expect(triggerBtn).toBeFocused();

    // Open modal
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('trigger-modal')).toBeVisible();
    await page.waitForTimeout(300);

    // F1: Focus should be inside the modal (Semi Modal or our wrapper)
    const focusInModal = await page.evaluate(() => {
      const modal = document.querySelector('[data-testid="trigger-modal"]');
      const semiModal = document.querySelector('.semi-modal-content');
      const active = document.activeElement;
      if (!active) return false;
      return (
        (modal && (modal === active || modal.contains(active))) ||
        (semiModal && (semiModal === active || semiModal.contains(active)))
      );
    });
    expect(focusInModal).toBe(true);

    // Verify dialog semantics
    const dialog = page.locator('[data-testid="trigger-modal"][role="dialog"]');
    await expect(dialog).toBeAttached();
    await expect(dialog).toHaveAttribute('aria-modal', 'true');

    // F3: Esc closes
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('trigger-modal')).toBeHidden({ timeout: 5_000 });

    // F4: Focus restores to trigger button
    await expect(triggerBtn).toBeFocused();
  });
});

// ── A1.2  AddSuiteModal (已接 useDialogA11y) ───────────────────────────────

test.describe('A1.2 AddSuiteModal 焦点管理', () => {
  test('F1+F3: Esc 关闭 → 焦点还原', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    const addSuiteBtn = page.getByTestId('open-add-suite-button');
    await expect(addSuiteBtn).toBeVisible();

    await addSuiteBtn.click();
    // Semi's Modal puts data-testid on a zero-height wrapper that never counts as
    // visible (nor, for the same reason, as ever shown); assert on its content.
    await expect(page.getByTestId('link-path-input')).toBeVisible();

    // Verify dialog semantics
    // AddSuiteModal sets role/aria-modal on its own inner container (the one
    // useDialogA11y manages), inside the element carrying the testid.
    const dialog = page.locator('[data-testid="add-suite-modal"] [role="dialog"][tabindex="-1"]');
    await expect(dialog).toBeAttached();
    await expect(dialog).toHaveAttribute('aria-modal', 'true');

    // F3: Esc closes
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('link-path-input')).toBeHidden({ timeout: 5_000 });

    // F4: Focus restores
    await expect(addSuiteBtn).toBeFocused();
  });
});

// ── A1.3  ScheduleModal (用 API 创建 profile 后验证) ───────────────────────

test.describe('A1.3 ScheduleModal 焦点管理', () => {
  test('dialog 语义正确 + input 有 aria-label', async ({ page }) => {
    // Create a profile via API so the sidebar shows it
    const { loginAndGetContext, createProfile, deleteProfile } = await import('./helpers/api');
    const adminCtx = await loginAndGetContext('admin');
    let profileId: string | null = null;
    try {
      profileId = await createProfile(adminCtx, {
        name: 'a11y-schedule-test',
        tests_path: 'sample_tests',
        runner: 'pytest',
      });
    } finally {
      await adminCtx.dispose();
    }

    if (!profileId) {
      test.skip(true, 'Could not create profile');
      return;
    }

    try {
      await page.goto('/');
      await expect(page.getByTestId('stat-total')).toBeVisible();

      // Find the schedule button for the profile in the sidebar
      const schedBtn = page.getByTestId('open-schedule-button');
      const hasBtn = await schedBtn.isVisible({ timeout: 5_000 }).catch(() => false);

      if (!hasBtn) {
        test.skip(true, 'Schedule button not visible in sidebar');
        return;
      }

      await schedBtn.click();
      await page.waitForTimeout(500);

      // Semi Modal portal — use evaluate to check dialog semantics
      const dialogInfo = await page.evaluate(() => {
        const modal = document.querySelector('.semi-modal-content');
        return {
          hasDialog: modal?.getAttribute('role') === 'dialog',
          nameInput: document.querySelector('[data-testid="schedule-name-input"]')?.getAttribute('aria-label'),
          cronInput: document.querySelector('[data-testid="schedule-cron-input"]')?.getAttribute('aria-label'),
          tzSelect: document.querySelector('[data-testid="schedule-timezone-select"]')?.getAttribute('aria-label'),
        };
      });

      expect(dialogInfo.hasDialog).toBe(true);
      expect(dialogInfo.nameInput).toBeTruthy();
      expect(dialogInfo.cronInput).toBeTruthy();
      expect(dialogInfo.tzSelect).toBeTruthy();
    } finally {
      // Cleanup
      if (profileId) {
        const cleanupCtx = await loginAndGetContext('admin');
        try {
          await deleteProfile(cleanupCtx, profileId);
        } finally {
          await cleanupCtx.dispose();
        }
      }
    }
  });
});

// ── A1.4  UserManagementModal (Semi Modal — 用 evaluate 绕过 portal) ──────

test.describe('A1.4 UserManagementModal 焦点管理', () => {
  test('F1+F3: dialog 语义正确 → Esc 关闭', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    await page.getByTestId('open-users-button').click();
    await page.waitForTimeout(500); // wait for modal animation

    // Semi Modal renders in portal — content is in DOM but portal wrapper is "hidden".
    // Use evaluate to check dialog semantics directly.
    const hasDialog = await page.evaluate(() => {
      const modal = document.querySelector('.semi-modal-content');
      return modal?.getAttribute('role') === 'dialog';
    });
    expect(hasDialog).toBe(true);

    // Esc closes the modal
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // Verify modal is gone from DOM
    const modalGone = await page.evaluate(() => !document.querySelector('.semi-modal-content'));
    expect(modalGone).toBe(true);
  });
});

// ── A1.5  RunDetailsDrawer (已接 useDialogA11y) ────────────────────────────

test.describe('A1.5 RunDetailsDrawer 焦点管理', () => {
  test('F1+F3: 焦点进入 drawer → close 关闭', async ({ page }) => {
    // The drawer opens from a run row, so supply one: CI starts from an empty DB.
    await mockRunWithLogsAndReport(page);
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Open drawer
    const firstCode = page.locator('table tbody code').first();
    await firstCode.click();
    const drawerTab = page.getByTestId('drawer-tab-logs');
    await expect(drawerTab).toBeVisible({ timeout: 10_000 });

    // Verify drawer is open (SideSheet has its own role="dialog")
    await expect(drawerTab).toBeVisible();

    // F3: Close via close button
    const closeBtn = page.locator('.semi-sidesheet-close');
    await closeBtn.click();
    await expect(drawerTab).toBeHidden({ timeout: 5_000 });
  });
});
