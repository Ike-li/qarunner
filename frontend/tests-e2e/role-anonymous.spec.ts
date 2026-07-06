// Role-matrix: anonymous user — UI + API assertions.
//
// R-UI-1.1  未登录只渲染登录页
// R-API-5.1 受保护端点不带 token → 401
// R-API-5.2 公开端点不带 token 可用
// R-API-5.3 artifact 端点不带 token → 401 且不泄露路径
//
// Runs under the default `chromium` project (no storageState).

import { test, expect, type APIRequestContext } from '@playwright/test';

import {
  createProfile,
  deleteProfile,
  deleteRun,
  loginAndGetContext,
  pollRunToTerminal,
} from './helpers/api';

const baseUrl = process.env.BASE_URL || 'http://localhost:5173';
const backendUrl =
  process.env.E2E_BACKEND_URL ||
  process.env.VITE_BACKEND_URL ||
  (new URL(baseUrl).hostname === 'frontend'
    ? 'http://backend:8000'
    : 'http://localhost:8000');
const PLAYWRIGHT_SUITE = 'sample_playwright';

interface RunResponse {
  id: string;
  runner: string;
}

interface Artifact {
  path: string;
}

async function triggerPlaywrightRun(
  ctx: APIRequestContext,
  profileId: string,
): Promise<string> {
  const resp = await ctx.post(`/profiles/${encodeURIComponent(profileId)}/trigger`);
  expect(resp.status(), await resp.text()).toBe(202);
  const run = (await resp.json()) as RunResponse;
  expect(run.runner).toBe('playwright');
  return run.id;
}

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
    // so we verify it directly. In the official Playwright Docker network the
    // backend service is reachable as `backend`, not container-local localhost.
    const healthResp = await page.request.get(`${backendUrl}/health`);
    expect(healthResp.status()).toBe(200);
    const body = await healthResp.json();
    expect(body.status).toBe('ok');
  });

  test('R-API-5.3 artifact 端点不带 token → 401 且不泄露路径', async ({ page }) => {
    test.setTimeout(180_000);

    const adminCtx = await loginAndGetContext('admin');
    let profileId: string | null = null;
    let runId: string | null = null;
    try {
      profileId = await createProfile(adminCtx, {
        name: `anonymous_artifacts_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      runId = await triggerPlaywrightRun(adminCtx, profileId);
      const status = await pollRunToTerminal(adminCtx, runId, 120_000, 2_000);
      expect(status).toBe('completed');

      const artifactsResp = await adminCtx.get(`/runs/${encodeURIComponent(runId)}/artifacts`);
      expect(artifactsResp.status(), await artifactsResp.text()).toBe(200);
      const artifacts = ((await artifactsResp.json()).artifacts ?? []) as Artifact[];
      const trace = artifacts.find((artifact) => artifact.path.endsWith('trace.zip'));
      expect(trace).toBeTruthy();
      const artifactPath = trace!.path;

      const endpoints = [
        `/runs/${encodeURIComponent(runId)}/artifacts`,
        `/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactPath)}`,
        `/runs/${encodeURIComponent(runId)}/artifacts.zip`,
      ];

      for (const endpoint of endpoints) {
        const resp = await page.request.get(endpoint);
        const bodyText = await resp.text();
        expect(resp.status(), `GET ${endpoint} should be 401`).toBe(401);
        expect(bodyText).not.toContain(artifactPath);
      }
    } finally {
      if (runId) {
        await deleteRun(adminCtx, runId).catch(() => {});
      }
      if (profileId) {
        await deleteProfile(adminCtx, profileId).catch(() => {});
      }
      await adminCtx.dispose();
    }
  });
});
