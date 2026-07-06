// Role-matrix: anonymous user — UI + API assertions.
//
// R-UI-1.1  未登录只渲染登录页
// R-API-5.1 受保护端点不带 token → 401
// R-API-5.2 公开端点不带 token 可用
// R-API-5.3 artifact 端点不带 token → 401 且不泄露路径
// R-API-5.4 run adjunct 端点不带 token → 401 且不泄露 run id
// R-API-5.5 mutation 端点不带 token → 401 且不泄露资源标识
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
const PYTEST_SUITE = 'sample_tests';

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
    const schedulePreviewQuery = new URLSearchParams({
      expression: '*/5 * * * *',
      timezone: 'UTC',
    });
    const caseHistoryQuery = new URLSearchParams({
      tests_path: PYTEST_SUITE,
      suite: PYTEST_SUITE,
      name: 'test_example',
    });

    for (const endpoint of [
      '/auth/me',
      '/users',
      '/credentials',
      '/tests',
      '/suites',
      `/tests/${encodeURIComponent(PYTEST_SUITE)}/tree`,
      `/tests/${encodeURIComponent(PYTEST_SUITE)}/markers`,
      '/runs',
      `/runs/trend?tests_path=${encodeURIComponent(PYTEST_SUITE)}`,
      '/profiles',
      '/metrics',
      `/cases/history?${caseHistoryQuery.toString()}`,
      '/schedules',
      `/schedules/preview?${schedulePreviewQuery.toString()}`,
    ]) {
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

  test('R-API-5.4 diff/report/stream 端点不带 token → 401 且不泄露 run id', async ({ page }) => {
    test.setTimeout(120_000);

    const adminCtx = await loginAndGetContext('admin');
    let runId: string | null = null;
    try {
      const runResp = await adminCtx.post('/runs', {
        data: {
          tests_path: PYTEST_SUITE,
          runner: 'pytest',
          args: [],
          allure: false,
          timeout: 60,
          selected_files: [],
          selected_markers: [],
          extra_args: '',
          env: {},
        },
      });
      expect(runResp.status(), await runResp.text()).toBe(202);
      const run = (await runResp.json()) as RunResponse;
      runId = run.id;
      expect(run.runner).toBe('pytest');

      const status = await pollRunToTerminal(adminCtx, runId, 60_000, 1_000);
      expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(status);

      const endpoints = [
        `/runs/${encodeURIComponent(runId)}/diff`,
        `/runs/${encodeURIComponent(runId)}/report`,
        `/runs/${encodeURIComponent(runId)}/stream`,
      ];

      for (const endpoint of endpoints) {
        const resp = await page.request.get(endpoint);
        const bodyText = await resp.text();
        expect(resp.status(), `GET ${endpoint} should be 401`).toBe(401);
        expect(bodyText).not.toContain(runId);
      }
    } finally {
      if (runId) {
        await deleteRun(adminCtx, runId).catch(() => {});
      }
      await adminCtx.dispose();
    }
  });

  test('R-API-5.5 mutation 端点不带 token → 401 且不泄露资源标识', async ({ page }) => {
    const stamp = Date.now();
    const probeUser = `anonymous_mutation_user_${stamp}`;
    const probeCredential = `anonymous_credential_${stamp}`;
    const probeSuite = `anonymous_suite_${stamp}`;
    const probeProfile = `anonymous_profile_${stamp}`;
    const probeRun = `anonymous_run_${stamp}`;
    const probeSchedule = `anonymous_schedule_${stamp}`;
    const probeSecret = `anonymous_secret_${stamp}`;

    const mutationRequests = [
      {
        method: 'POST',
        endpoint: '/users',
        data: { username: probeUser, password: 'Anonymous-Test-2026!', role: 'user' },
        forbidden: [probeUser],
      },
      {
        method: 'PUT',
        endpoint: `/users/${encodeURIComponent(probeUser)}`,
        data: { role: 'admin' },
        forbidden: [probeUser],
      },
      {
        method: 'DELETE',
        endpoint: `/users/${encodeURIComponent(probeUser)}`,
        forbidden: [probeUser],
      },
      {
        method: 'POST',
        endpoint: '/credentials',
        data: { name: probeCredential, type: 'https_token', secret: probeSecret },
        forbidden: [probeCredential, probeSecret],
      },
      {
        method: 'DELETE',
        endpoint: `/credentials/${encodeURIComponent(probeCredential)}`,
        forbidden: [probeCredential],
      },
      {
        method: 'POST',
        endpoint: '/tests/link',
        data: { path: `/tmp/${probeSuite}` },
        forbidden: [probeSuite],
      },
      {
        method: 'POST',
        endpoint: '/tests/clone',
        data: { url: `https://example.com/${probeSuite}.git`, name: probeSuite },
        forbidden: [probeSuite],
      },
      {
        method: 'POST',
        endpoint: `/tests/${encodeURIComponent(probeSuite)}/pull`,
        data: {},
        forbidden: [probeSuite],
      },
      {
        method: 'POST',
        endpoint: `/tests/${encodeURIComponent(probeSuite)}/prepare`,
        data: {},
        forbidden: [probeSuite],
      },
      {
        method: 'DELETE',
        endpoint: `/tests/${encodeURIComponent(probeSuite)}`,
        forbidden: [probeSuite],
      },
      {
        method: 'POST',
        endpoint: '/profiles',
        data: { name: probeProfile, tests_path: PYTEST_SUITE, runner: 'pytest' },
        forbidden: [probeProfile],
      },
      {
        method: 'PUT',
        endpoint: `/profiles/${encodeURIComponent(probeProfile)}`,
        data: { name: probeProfile, tests_path: PYTEST_SUITE, runner: 'pytest' },
        forbidden: [probeProfile],
      },
      {
        method: 'DELETE',
        endpoint: `/profiles/${encodeURIComponent(probeProfile)}`,
        forbidden: [probeProfile],
      },
      {
        method: 'POST',
        endpoint: `/profiles/${encodeURIComponent(probeProfile)}/trigger`,
        data: {},
        forbidden: [probeProfile],
      },
      {
        method: 'POST',
        endpoint: '/runs',
        data: {
          tests_path: PYTEST_SUITE,
          runner: 'pytest',
          args: [],
          allure: false,
          timeout: 60,
          selected_files: [],
          selected_markers: [],
          extra_args: '',
          env: {},
        },
        forbidden: [PYTEST_SUITE],
      },
      {
        method: 'PUT',
        endpoint: `/runs/${encodeURIComponent(probeRun)}/lock`,
        data: { locked: true },
        forbidden: [probeRun],
      },
      {
        method: 'POST',
        endpoint: `/runs/${encodeURIComponent(probeRun)}/cancel`,
        data: {},
        forbidden: [probeRun],
      },
      {
        method: 'DELETE',
        endpoint: `/runs/${encodeURIComponent(probeRun)}`,
        forbidden: [probeRun],
      },
      {
        method: 'POST',
        endpoint: `/runs/${encodeURIComponent(probeRun)}/rerun`,
        data: {},
        forbidden: [probeRun],
      },
      {
        method: 'POST',
        endpoint: '/runs/cleanup',
        data: { retention_days: 7 },
        forbidden: [],
      },
      {
        method: 'POST',
        endpoint: '/schedules',
        data: {
          name: probeSchedule,
          profile_id: probeProfile,
          cron_expression: '0 3 * * *',
          timezone: 'UTC',
          enabled: true,
        },
        forbidden: [probeSchedule, probeProfile],
      },
      {
        method: 'PUT',
        endpoint: `/schedules/${encodeURIComponent(probeSchedule)}`,
        data: {
          name: probeSchedule,
          profile_id: probeProfile,
          cron_expression: '0 4 * * *',
          timezone: 'UTC',
          enabled: false,
        },
        forbidden: [probeSchedule, probeProfile],
      },
      {
        method: 'DELETE',
        endpoint: `/schedules/${encodeURIComponent(probeSchedule)}`,
        forbidden: [probeSchedule],
      },
      {
        method: 'POST',
        endpoint: `/schedules/${encodeURIComponent(probeSchedule)}/trigger`,
        data: {},
        forbidden: [probeSchedule],
      },
    ];

    for (const request of mutationRequests) {
      const resp = await page.request.fetch(request.endpoint, {
        method: request.method,
        data: request.data,
      });
      const bodyText = await resp.text();
      expect(resp.status(), `${request.method} ${request.endpoint} should be 401`).toBe(401);
      for (const value of request.forbidden) {
        expect(bodyText, `${request.method} ${request.endpoint} should not leak ${value}`).not.toContain(
          value,
        );
      }
    }
  });
});
