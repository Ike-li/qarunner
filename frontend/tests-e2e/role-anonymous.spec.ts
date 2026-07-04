// Role-matrix: anonymous user — UI + API assertions.
//
// R-UI-1.1  未登录只渲染登录页
// R-API-5.1 受保护端点不带 token → 401
// R-API-5.2 公开端点不带 token 可用
//
// Runs under the default `chromium` project (no storageState).

import { test, expect } from '@playwright/test';

// ── R-UI-1.1  Anonymous sees only the login screen ────────────────────────

test.describe('R-UI-1 anonymous login page', () => {
  test('R-UI-1.1 未登录访问根 URL 渲染登录页，dashboard 不可见', async ({ page }) => {
    await page.goto('/');

    // Login form present
    await expect(page.getByTestId('login-title')).toBeVisible();
    await expect(page.getByTestId('login-username')).toBeVisible();
    await expect(page.getByTestId('login-password')).toBeVisible();
    await expect(page.getByTestId('login-submit')).toBeVisible();

    // Dashboard elements NOT visible (SPA gate: auth=false → LoginScreen)
    await expect(page.getByTestId('stat-total')).toBeHidden();
    await expect(page.getByTestId('open-trigger-button')).toBeHidden();
    await expect(page.getByTestId('open-users-button')).toBeHidden();
  });
});

// ── R-API-5  Anonymous API access ─────────────────────────────────────────

test.describe('R-API-5 anonymous API', () => {
  // page.request does NOT carry auth cookies when there is no storageState,
  // so these calls are effectively anonymous.

  test('R-API-5.1 受保护端点不带 token → 401', async ({ page }) => {
    for (const endpoint of ['/runs', '/profiles', '/schedules', '/auth/me']) {
      const resp = await page.request.get(endpoint);
      expect(resp.status(), `GET ${endpoint} should be 401`).toBe(401);
    }
  });

  test('R-API-5.2 公开端点不带 token 可用 — 错误凭据返回 401 而非 500', async ({ page }) => {
    // POST /auth/login is public (no token required).
    // With bad credentials it should return 401, not 500 or any other error.
    const badLogin = await page.request.post('/auth/login', {
      data: { username: 'nonexistent', password: 'wrong' },
    });
    expect(badLogin.status()).toBe(401);

    // GET /health is on the backend (:8000) and not proxied through Vite,
    // so we verify it directly.
    const healthResp = await page.request.get('http://localhost:8000/health');
    expect(healthResp.status()).toBe(200);
    const body = await healthResp.json();
    expect(body.status).toBe('ok');
  });
});
