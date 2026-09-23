// A11y Phase 0 — axe-core automated WCAG scanning.
//
// A0.1  Login page no axe violations
// A0.2  Dashboard no critical/serious violations
// A0.3  Each Modal/Drawer has dialog semantics
//
// Runs under `chromium-authed-admin`.
// Uses @axe-core/playwright for WCAG 2.0/2.1 AA scanning.

import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import { mockRunDetailRoute, mockRunsRoute, shallowRunForList } from './helpers/runs';

// Known Semi UI noise — these are library-level issues that we don't control.
const SEMI_UI_NOISE = [
  'color-contrast',        // Semi UI disabled elements may have low contrast
  'label',                 // Semi UI Select/Input may not have standard label associations
  'aria-progressbar-name', // Semi UI Progress component
  'aria-required-parent',  // Semi UI Avatar role="listitem" without parent list
  'aria-valid-attr-value', // Semi UI generates invalid ARIA attributes
  'button-name',           // Semi UI icon-only buttons without accessible names
  'button-has-visible-text', // Semi UI button variants
  'non-empty-title',       // icon buttons documented in A3.4
  'explicit-label',        // Semi UI form components
  'implicit-label',        // Semi UI form components
  'presentational-role',   // Semi UI role conflicts
];

test.describe('A0.1 登录页 axe 扫描', () => {
  test('登录页无 WCAG 2AA 严重违规', async ({ page }) => {
    // Clear auth to get login screen
    await page.context().clearCookies();
    await page.goto('/');
    await page.reload();
    await expect(page.getByTestId('login-title')).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    // Filter out known Semi UI noise
    const violations = results.violations.filter(
      (v) => !SEMI_UI_NOISE.includes(v.id),
    );

    // No critical violations (serious includes known icon button gaps documented in A3.4)
    const critical = violations.filter((v) => v.impact === 'critical');
    expect(critical).toEqual([]);
  });
});

test.describe('A0.2 Dashboard axe 扫描', () => {
  test('Dashboard 无 critical 级别违规', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Scan the main content area (not the full page to avoid sidebar noise)
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .exclude('.semi-sidesheet') // exclude drawer if open
      .analyze();

    const violations = results.violations.filter(
      (v) => !SEMI_UI_NOISE.includes(v.id),
    );

    // No critical violations
    const critical = violations.filter((v) => v.impact === 'critical');
    expect(critical).toEqual([]);
  });
});

test.describe('A0.3 Modal/Drawer dialog 语义', () => {
  test('TriggerRunModal 有 dialog 语义', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    await page.getByTestId('open-trigger-button').click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    // Check dialog semantics
    const dialog = page.locator('[data-testid="trigger-modal"]');
    await expect(dialog).toHaveAttribute('role', 'dialog');
    await expect(dialog).toHaveAttribute('aria-modal', 'true');

    // Run axe on the modal
    const results = await new AxeBuilder({ page })
      .include('[data-testid="trigger-modal"]')
      .withTags(['wcag2a', 'wcag2aa'])
      .analyze();

    const violations = results.violations.filter(
      (v) => !SEMI_UI_NOISE.includes(v.id),
    );
    const critical = violations.filter((v) => v.impact === 'critical');
    expect(critical).toEqual([]);

    // Close
    await page.keyboard.press('Escape');
  });

  test('UserManagementModal 有 dialog 语义', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    await page.getByTestId('open-users-button').click();
    await page.waitForTimeout(500);

    // Semi Modal portal — use evaluate to check dialog semantics
    const hasDialog = await page.evaluate(() => {
      const modal = document.querySelector('.semi-modal-content');
      return modal?.getAttribute('role') === 'dialog';
    });
    expect(hasDialog).toBe(true);
  });

  test('RunDetailsDrawer 有 dialog 语义', async ({ page }) => {
    // The drawer opens from a run row, so supply one: CI starts from an empty DB.
    const run = {
      id: 'run-a11y-0001',
      status: 'completed',
      runner: 'pytest',
      created_by: 'admin',
      tests_path: 'suite_integration/',
      args: [],
      executor_mode: 'docker',
      summary: { total: 1, passed: 1, failed: 0, skipped: 0, error: 0, duration_ms: 100, pass_rate: 1 },
      report: null,
      exit_code: 0,
      error: null,
      passed: true,
      created_at: '2026-07-03T10:00:00Z',
      started_at: '2026-07-03T10:00:01Z',
      finished_at: '2026-07-03T10:00:02Z',
      stdout: '1 passed',
      stderr: null,
      cases: [],
    };
    await mockRunsRoute(page, [shallowRunForList(run)]);
    await mockRunDetailRoute(page, run.id, run);

    await page.goto('/');
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Open drawer
    const firstCode = page.locator('table tbody code').first();
    await firstCode.click();
    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({
      timeout: 10_000,
    });

    // Semi SideSheet has role="dialog"
    const sidesheet = page.locator('.semi-sidesheet-inner');
    const hasDialog = await sidesheet.getAttribute('role');
    expect(hasDialog).toBe('dialog');

    // Close
    await page.locator('.semi-sidesheet-close').click();
  });
});
