// Role-matrix: non-admin user — UI gate + network-level assertions.
//
// R-UI-2.1  user 角色可见 trigger/add-suite，不可见 users 管理
// R-UI-2.2  user 请求 GET /users → 403（后端 gate）
// R-UI-3.1  useUsers hook 对 user 不发 /users 请求
//
// Runs under `chromium-authed-user` (storageState = e2e_user cookies).

import { test, expect, type APIRequestContext } from '@playwright/test';

import {
  createProfile,
  deleteProfile,
  deleteRun,
  loginAndGetContext,
  pollRunToTerminal,
} from './helpers/api';

const PLAYWRIGHT_SUITE = 'sample_playwright';

interface RunDetail {
  id: string;
  runner: string;
  status: string;
  created_by: string;
}

interface Artifact {
  path: string;
}

async function triggerProfileRun(ctx: APIRequestContext, profileId: string): Promise<string> {
  const resp = await ctx.post(`/profiles/${encodeURIComponent(profileId)}/trigger`);
  expect(resp.status(), await resp.text()).toBe(202);
  return ((await resp.json()) as { id: string }).id;
}

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

// ── R-UI-4  Artifact drawer permission state ─────────────────────────────

test.describe('R-UI-4 artifact drawer 权限状态', () => {
  test('R-UI-4.1 user 打开非 owner Playwright run 时 artifact 403 显示权限态且无下载链接', async ({
    page,
  }) => {
    test.setTimeout(180_000);

    const adminCtx = await loginAndGetContext('admin');
    let profileId: string | null = null;
    let runId: string | null = null;
    try {
      profileId = await createProfile(adminCtx, {
        name: `rui4_artifacts_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      runId = await triggerProfileRun(adminCtx, profileId);
      const status = await pollRunToTerminal(adminCtx, runId, 120_000, 2_000);
      expect(status).toBe('completed');

      const detailResp = await adminCtx.get(`/runs/${encodeURIComponent(runId)}`);
      expect(detailResp.status(), await detailResp.text()).toBe(200);
      const runDetail = (await detailResp.json()) as RunDetail;
      expect(runDetail.runner).toBe('playwright');
      expect(runDetail.created_by).not.toBe('e2e_user');

      const artifactsResp = await adminCtx.get(`/runs/${encodeURIComponent(runId)}/artifacts`);
      expect(artifactsResp.status(), await artifactsResp.text()).toBe(200);
      const artifacts = ((await artifactsResp.json()).artifacts ?? []) as Artifact[];
      const trace = artifacts.find((artifact) => artifact.path.endsWith('trace.zip'));
      expect(trace).toBeTruthy();

      await page.route('**/runs', async (route) => {
        if (route.request().method() !== 'GET') {
          await route.fallback();
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ runs: [runDetail] }),
        });
      });
      await page.route(`**/runs/${runId}`, async (route) => {
        if (route.request().method() !== 'GET') {
          await route.fallback();
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(runDetail),
        });
      });

      await page.goto('/');
      await expect(page.getByTestId(`run-row-${runId}`)).toBeVisible({ timeout: 10_000 });
      await page.getByTestId(`run-row-${runId}`).click();
      await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 10_000 });

      const artifactResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname === `/runs/${runId}/artifacts` &&
          response.request().method() === 'GET'
        );
      });
      await page.getByTestId('drawer-tab-report').click();
      expect((await artifactResponse).status()).toBe(403);

      await expect(page.getByTestId('run-artifacts-forbidden')).toBeVisible({
        timeout: 10_000,
      });
      await expect(page.getByTestId('run-artifacts-forbidden')).toContainText(
        /do not have permission|没有权限/,
      );
      await expect(page.getByTestId('run-artifacts-download-all')).toHaveCount(0);
      await expect(page.locator('[data-testid^="run-artifact-link-"]')).toHaveCount(0);
      await expect(page.locator('[role="dialog"]')).not.toContainText(trace!.path);
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
