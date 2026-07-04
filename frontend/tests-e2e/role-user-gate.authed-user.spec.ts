// Role-matrix: non-admin user — UI gate + network-level assertions.
//
// R-UI-2.1  user 角色可见 trigger/add-suite，不可见 users 管理
// R-UI-2.2  user 请求 GET /users → 403（后端 gate）
// R-UI-3.1  useUsers hook 对 user 不发 /users 请求
//
// Runs under `chromium-authed-user` (storageState = e2e_user cookies).

import { test, expect } from '@playwright/test';

// ── R-UI-2  Non-admin header buttons ──────────────────────────────────────

test.describe('R-UI-2 user 角色头部按钮', () => {
  test('R-UI-2.1 user 登录后头部按钮集合正确', async ({ page }) => {
    await page.goto('/');

    // Regular user CAN see trigger + add-suite
    await expect(page.getByTestId('open-trigger-button')).toBeVisible();
    await expect(page.getByTestId('open-add-suite-button')).toBeVisible();

    // Regular user CANNOT see user management
    await expect(page.getByTestId('open-users-button')).toBeHidden();

    // Role badge shows "user"
    await expect(page.getByTestId('profile-role')).toHaveText('user');
  });

  test('R-UI-2.2 user 调 GET /users → 403', async ({ page }) => {
    const resp = await page.request.get('/users');
    expect(resp.status()).toBe(403);
  });
});

// ── R-UI-3  useUsers hook does not fire for non-admin ──────────────────────

test.describe('R-UI-3 useUsers hook 守门', () => {
  test('R-UI-3.1 user 登录后无 /users 网络请求', async ({ page }) => {
    const usersRequests: string[] = [];

    page.on('request', (req) => {
      if (req.url().includes('/users')) {
        usersRequests.push(req.url());
      }
    });

    await page.goto('/');

    // Wait for dashboard to settle — any initial fetches should be done by now.
    // We look for the stats card to confirm the dashboard rendered.
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // Small grace period for any straggling requests
    await page.waitForTimeout(1_000);

    // useUsers.fetchUsers() should have early-returned because isAdmin=false
    expect(
      usersRequests,
      'useUsers should NOT call GET /users for a non-admin role',
    ).toHaveLength(0);
  });
});
