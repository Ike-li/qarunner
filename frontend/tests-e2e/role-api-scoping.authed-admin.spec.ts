// Role-matrix: API-level cross-role access control assertions.
//
// R-API-1  静默过滤（列表端点 owner-scope）
// R-API-2  越权单资源 403 vs 404
// R-API-3  admin 保护规则
// R-API-4  executor_mode 限制
//
// Runs under `chromium-authed-admin` (storageState = admin cookies).
// Creates temporary userA/userB in beforeAll, cleans up in afterAll.

import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_ADMIN, E2E_ADMIN_PASSWORD } from './fixtures/auth';

// Temp user credentials — unique per run to avoid cross-test interference.
const USER_A = `e2e_role_a_${Date.now()}`;
const USER_B = `e2e_role_b_${Date.now()}`;
const USER_A_PW = 'RoleA-Test-2026!';
const USER_B_PW = 'RoleB-Test-2026!';
const PLAYWRIGHT_SUITE = 'sample_playwright';
const PYTEST_SUITE = 'sample_tests';

let adminToken: string;
let userAToken: string;
let userBToken: string;

// Helper: login and return the bearer token.
async function getToken(
  request: APIRequestContext,
  username: string,
  password: string,
): Promise<string> {
  const resp = await request.post('/auth/login', {
    data: { username, password },
  });
  if (!resp.ok()) {
    throw new Error(`getToken(${username}) failed: ${resp.status()} ${await resp.text()}`);
  }
  return (await resp.json()).access_token;
}

// Helper: authenticated fetch via page.request with Bearer header.
function authedGet(page: Page, token: string, url: string) {
  return page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
}
function authedPost(page: Page, token: string, url: string, data: unknown) {
  return page.request.post(url, {
    data,
    headers: { Authorization: `Bearer ${token}` },
  });
}
function authedPut(page: Page, token: string, url: string, data: unknown) {
  return page.request.put(url, {
    data,
    headers: { Authorization: `Bearer ${token}` },
  });
}
function authedDelete(page: Page, token: string, url: string) {
  return page.request.delete(url, { headers: { Authorization: `Bearer ${token}` } });
}

// ── Setup & Teardown ─────────────────────────────────────────────────────

test.beforeAll(async ({ request }) => {
  // Get tokens for all three roles
  adminToken = await getToken(request, E2E_ADMIN, E2E_ADMIN_PASSWORD);
  userAToken = await getToken(request, USER_A, USER_A_PW).catch(async () => {
    // userA may not exist yet — create it
    const createResp = await request.post('/users', {
      data: { username: USER_A, password: USER_A_PW, role: 'user' },
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    if (!createResp.ok()) {
      throw new Error(`Failed to create ${USER_A}: ${createResp.status()}`);
    }
    return getToken(request, USER_A, USER_A_PW);
  });
  userBToken = await getToken(request, USER_B, USER_B_PW).catch(async () => {
    const createResp = await request.post('/users', {
      data: { username: USER_B, password: USER_B_PW, role: 'user' },
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    if (!createResp.ok()) {
      throw new Error(`Failed to create ${USER_B}: ${createResp.status()}`);
    }
    return getToken(request, USER_B, USER_B_PW);
  });
});

test.afterAll(async ({ request }) => {
  // Cleanup: delete temp users (admin token required)
  if (adminToken) {
    await request.delete(`/users/${USER_A}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    }).catch(() => {});
    await request.delete(`/users/${USER_B}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    }).catch(() => {});
  }
});

// ── R-API-1  Silent filtering (list endpoints) ───────────────────────────

test.describe('R-API-1 静默过滤', () => {
  test('R-API-1.1 GET /runs 非 admin 只见自己的', async ({ page }) => {
    // Admin creates a profile + run
    const adminProfileResp = await authedPost(page, adminToken, '/profiles', {
      name: 'admin_scope_profile',
      tests_path: '/tmp/fake_tests',
      runner: 'pytest',
    });
    expect(adminProfileResp.ok()).toBeTruthy();
    const adminProfileId = (await adminProfileResp.json()).id;

    // Trigger a run as admin (needs tests_path, not profile_id)
    const adminRunResp = await authedPost(page, adminToken, '/runs', {
      tests_path: '/tmp/fake_tests',
      runner: 'pytest',
    });
    // 201 or 400 (if executor unavailable) — either way we try
    let adminRunId: string | null = null;
    if (adminRunResp.ok()) {
      adminRunId = (await adminRunResp.json()).id;
    }

    // userA creates a profile + run
    const userProfileResp = await authedPost(page, userAToken, '/profiles', {
      name: 'userA_scope_profile',
      tests_path: '/tmp/fake_tests',
      runner: 'pytest',
    });
    expect(userProfileResp.ok()).toBeTruthy();
    const userProfileId = (await userProfileResp.json()).id;

    const userRunResp = await authedPost(page, userAToken, '/runs', {
      tests_path: '/tmp/fake_tests',
      runner: 'pytest',
    });
    let userRunId: string | null = null;
    if (userRunResp.ok()) {
      userRunId = (await userRunResp.json()).id;
    }

    // userA GET /runs — should only see their own
    const listResp = await authedGet(page, userAToken, '/runs');
    expect(listResp.ok()).toBeTruthy();
    const runs = (await listResp.json()).runs ?? (await listResp.json());
    const runIds: string[] = Array.isArray(runs) ? runs.map((r: { id: string }) => r.id) : [];

    if (userRunId) {
      expect(runIds).toContain(userRunId);
    }
    if (adminRunId) {
      expect(runIds).not.toContain(adminRunId);
    }

    // Cleanup
    if (adminRunId) await authedDelete(page, adminToken, `/runs/${adminRunId}`);
    if (userRunId) await authedDelete(page, userAToken, `/runs/${userRunId}`);
    await authedDelete(page, adminToken, `/profiles/${adminProfileId}`);
    await authedDelete(page, userAToken, `/profiles/${userProfileId}`);
  });

  test('R-API-1.2 GET /profiles 非 admin 只见自己的', async ({ page }) => {
    // admin has some profiles; userA should NOT see them
    const adminProfResp = await authedPost(page, adminToken, '/profiles', {
      name: 'admin_only_profile',
      tests_path: '/tmp/fake',
      runner: 'pytest',
    });
    const adminProfId = (await adminProfResp.json()).id;

    const userAList = await authedGet(page, userAToken, '/profiles');
    expect(userAList.ok()).toBeTruthy();
    const profiles = (await userAList.json()).profiles ?? (await userAList.json());
    const profNames: string[] = Array.isArray(profiles)
      ? profiles.map((p: { name: string }) => p.name)
      : [];

    expect(profNames).not.toContain('admin_only_profile');

    // Cleanup
    await authedDelete(page, adminToken, `/profiles/${adminProfId}`);
  });
});

// ── R-API-2  Cross-role single-resource access ───────────────────────────

test.describe('R-API-2 越权单资源', () => {
  let adminProfileId: string;

  test.beforeEach(async ({ page }) => {
    const resp = await authedPost(page, adminToken, '/profiles', {
      name: `r2_admin_${Date.now()}`,
      tests_path: '/tmp/fake',
      runner: 'pytest',
    });
    expect(resp.ok()).toBeTruthy();
    adminProfileId = (await resp.json()).id;
  });

  test.afterEach(async ({ page }) => {
    await authedDelete(page, adminToken, `/profiles/${adminProfileId}`).catch(() => {});
  });

  test('R-API-2.1 PUT /profiles/{admin_profile} 以 userA token → 403', async ({ page }) => {
    const resp = await authedPut(page, userAToken, `/profiles/${adminProfileId}`, {
      name: 'hacked_name',
      tests_path: '/tmp/hacked',
    });
    expect(resp.status()).toBe(403);
  });

  test('R-API-2.2 PUT /profiles/{不存在 id} 以 userA token → 404', async ({ page }) => {
    const resp = await authedPut(page, userAToken, '/profiles/00000000-0000-0000-0000-000000000000', {
      name: 'ghost',
      tests_path: '/tmp/ghost',
    });
    expect(resp.status()).toBe(404);
  });

  test('R-API-2.3 DELETE /runs/{admin_run} 以 userA token → 403', async ({ page }) => {
    // Create a run as admin (needs tests_path, not profile_id)
    const runResp = await authedPost(page, adminToken, '/runs', {
      tests_path: '/tmp/fake_tests',
      runner: 'pytest',
    });
    if (!runResp.ok()) {
      // If run creation fails (executor issue), skip gracefully
      test.skip(true, `Admin run creation failed: ${runResp.status()}`);
      return;
    }
    const runId = (await runResp.json()).id;

    // userA tries to delete admin's run
    const delResp = await authedDelete(page, userAToken, `/runs/${runId}`);
    expect(delResp.status()).toBe(403);

    // Cleanup: admin deletes their own run
    await authedDelete(page, adminToken, `/runs/${runId}`);
  });

  test('R-API-2.4 DELETE /schedules/{admin_schedule} 以 userA token → 403', async ({ page }) => {
    // Create a schedule as admin
    const schedResp = await authedPost(page, adminToken, '/schedules', {
      name: 'admin_sched_r2',
      profile_id: adminProfileId,
      cron_expression: '0 3 * * *',
      timezone: 'UTC',
      enabled: false,
    });
    if (!schedResp.ok()) {
      test.skip(true, `Schedule creation failed: ${schedResp.status()}`);
      return;
    }
    const schedId = (await schedResp.json()).id;

    // userA tries to delete admin's schedule
    const delResp = await authedDelete(page, userAToken, `/schedules/${schedId}`);
    expect(delResp.status()).toBe(403);

    // Cleanup
    await authedDelete(page, adminToken, `/schedules/${schedId}`);
  });

  test('R-API-2.5 GET /runs/{admin_run}/artifacts* 以 userA token → 403', async ({ page }) => {
    test.setTimeout(180_000);

    let playwrightProfileId: string | null = null;
    let runId: string | null = null;
    try {
      const profileResp = await authedPost(page, adminToken, '/profiles', {
        name: `r2_artifacts_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      expect(profileResp.status()).toBe(201);
      playwrightProfileId = (await profileResp.json()).id;

      const runResp = await authedPost(
        page,
        adminToken,
        `/profiles/${encodeURIComponent(playwrightProfileId!)}/trigger`,
        {},
      );
      expect(runResp.status()).toBe(202);
      const triggeredRun = await runResp.json();
      runId = triggeredRun.id;
      expect(triggeredRun.runner).toBe('playwright');

      let terminalStatus = triggeredRun.status as string;
      await expect(async () => {
        const detailResp = await authedGet(page, adminToken, `/runs/${encodeURIComponent(runId!)}`);
        expect(detailResp.status()).toBe(200);
        const detail = await detailResp.json();
        terminalStatus = detail.status;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(terminalStatus);
      }).toPass({ timeout: 120_000, intervals: [2_000] });
      expect(terminalStatus).toBe('completed');

      const adminListResp = await authedGet(
        page,
        adminToken,
        `/runs/${encodeURIComponent(runId!)}/artifacts`,
      );
      expect(adminListResp.status()).toBe(200);
      const artifacts = ((await adminListResp.json()).artifacts ?? []) as Array<{
        path: string;
        content_type: string;
      }>;
      const trace = artifacts.find((artifact) => artifact.path.endsWith('trace.zip'));
      expect(trace).toBeTruthy();

      const artifactPath = trace!.path;
      const endpoints = [
        `/runs/${encodeURIComponent(runId!)}/artifacts`,
        `/runs/${encodeURIComponent(runId!)}/artifacts/${encodeURIComponent(artifactPath)}`,
        `/runs/${encodeURIComponent(runId!)}/artifacts.zip`,
      ];

      for (const endpoint of endpoints) {
        const adminResp = await authedGet(page, adminToken, endpoint);
        expect(adminResp.status()).toBe(200);

        const userResp = await authedGet(page, userAToken, endpoint);
        const userBody = await userResp.text();
        expect(userResp.status(), userBody).toBe(403);
        expect(userBody).not.toContain(artifactPath);
      }
    } finally {
      if (runId) {
        await authedDelete(page, adminToken, `/runs/${encodeURIComponent(runId)}`).catch(() => {});
      }
      if (playwrightProfileId) {
        await authedDelete(
          page,
          adminToken,
          `/profiles/${encodeURIComponent(playwrightProfileId)}`,
        ).catch(() => {});
      }
    }
  });

  test('R-API-2.6 GET /runs/{own_run}/artifacts* 以 owner user token → 200', async ({ page }) => {
    test.setTimeout(180_000);

    let playwrightProfileId: string | null = null;
    let runId: string | null = null;
    try {
      const profileResp = await authedPost(page, userAToken, '/profiles', {
        name: `r2_owner_artifacts_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      expect(profileResp.status()).toBe(201);
      playwrightProfileId = (await profileResp.json()).id;

      const runResp = await authedPost(
        page,
        userAToken,
        `/profiles/${encodeURIComponent(playwrightProfileId!)}/trigger`,
        {},
      );
      expect(runResp.status()).toBe(202);
      const triggeredRun = await runResp.json();
      runId = triggeredRun.id;
      expect(triggeredRun.runner).toBe('playwright');

      let terminalStatus = triggeredRun.status as string;
      await expect(async () => {
        const detailResp = await authedGet(page, userAToken, `/runs/${encodeURIComponent(runId!)}`);
        expect(detailResp.status()).toBe(200);
        const detail = await detailResp.json();
        terminalStatus = detail.status;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(terminalStatus);
      }).toPass({ timeout: 120_000, intervals: [2_000] });
      expect(terminalStatus).toBe('completed');

      const ownerListResp = await authedGet(
        page,
        userAToken,
        `/runs/${encodeURIComponent(runId!)}/artifacts`,
      );
      expect(ownerListResp.status()).toBe(200);
      const artifacts = ((await ownerListResp.json()).artifacts ?? []) as Array<{
        path: string;
        content_type: string;
      }>;
      const trace = artifacts.find((artifact) => artifact.path.endsWith('trace.zip'));
      expect(trace).toBeTruthy();

      const artifactPath = trace!.path;
      const endpoints = [
        `/runs/${encodeURIComponent(runId!)}/artifacts`,
        `/runs/${encodeURIComponent(runId!)}/artifacts/${encodeURIComponent(artifactPath)}`,
        `/runs/${encodeURIComponent(runId!)}/artifacts.zip`,
      ];

      for (const endpoint of endpoints) {
        const ownerResp = await authedGet(page, userAToken, endpoint);
        expect(ownerResp.status(), await ownerResp.text()).toBe(200);
      }
    } finally {
      if (runId) {
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(runId)}`).catch(() => {});
      }
      if (playwrightProfileId) {
        await authedDelete(
          page,
          userAToken,
          `/profiles/${encodeURIComponent(playwrightProfileId)}`,
        ).catch(() => {});
      }
    }
  });

  test('R-API-2.7 GET /runs/{run}/diff|report|stream 遵守 owner/admin 边界', async ({
    page,
  }) => {
    test.setTimeout(120_000);

    let runId: string | null = null;
    try {
      const runResp = await authedPost(page, userAToken, '/runs', {
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
        args: [],
        allure: false,
        timeout: 60,
        selected_files: [],
        selected_markers: [],
        extra_args: '',
        env: {},
      });
      expect(runResp.status(), await runResp.text()).toBe(202);
      const run = await runResp.json();
      runId = run.id;
      expect(run.runner).toBe('pytest');
      expect(run.created_by).toBe(USER_A);

      let terminalStatus = run.status as string;
      await expect(async () => {
        const detailResp = await authedGet(page, userAToken, `/runs/${encodeURIComponent(runId!)}`);
        expect(detailResp.status()).toBe(200);
        const detail = await detailResp.json();
        terminalStatus = detail.status;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(terminalStatus);
      }).toPass({ timeout: 60_000, intervals: [1_000] });

      const diffEndpoint = `/runs/${encodeURIComponent(runId!)}/diff`;
      const reportEndpoint = `/runs/${encodeURIComponent(runId!)}/report`;
      const streamEndpoint = `/runs/${encodeURIComponent(runId!)}/stream`;

      for (const token of [userAToken, adminToken]) {
        const diffResp = await authedGet(page, token, diffEndpoint);
        expect(diffResp.status(), await diffResp.text()).toBe(200);

        const streamResp = await authedGet(page, token, streamEndpoint);
        expect(streamResp.status(), await streamResp.text()).toBe(200);

        const reportResp = await authedGet(page, token, reportEndpoint);
        // The run disables Allure, so report may be absent. It must not fail
        // because owner/admin authorization was denied.
        expect([200, 404], await reportResp.text()).toContain(reportResp.status());
      }

      for (const endpoint of [diffEndpoint, reportEndpoint, streamEndpoint]) {
        const userBResp = await authedGet(page, userBToken, endpoint);
        const userBBody = await userBResp.text();
        expect(userBResp.status(), userBBody).toBe(403);
        expect(userBBody).not.toContain(runId!);
      }
    } finally {
      if (runId) {
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(runId)}`).catch(() => {});
      }
    }
  });
});

// ── R-API-3  Admin protection rules ──────────────────────────────────────

test.describe('R-API-3 admin 保护', () => {
  test('R-API-3.1 非管理员 POST /users → 403', async ({ page }) => {
    const resp = await authedPost(page, userAToken, '/users', {
      username: 'should_not_exist',
      password: 'whatever',
      role: 'user',
    });
    expect(resp.status()).toBe(403);
  });

  test('R-API-3.2 DELETE /users/{自己} → 400（不能删自己）', async ({ page }) => {
    const resp = await authedDelete(page, adminToken, `/users/${E2E_ADMIN}`);
    expect(resp.status()).toBe(400);
  });

  test('R-API-3.3 PUT /users/{最后一个 admin} 降级 → 400', async ({ page }) => {
    // The system has exactly one admin (seeded). Trying to demote them should fail.
    const resp = await authedPut(page, adminToken, `/users/${E2E_ADMIN}`, { role: 'user' });
    expect(resp.status()).toBe(400);
  });

  test('R-API-3.4 POST /runs/cleanup 非管理员 → 403', async ({ page }) => {
    const resp = await authedPost(page, userAToken, '/runs/cleanup', {
      retention_days: 30,
    });
    expect(resp.status()).toBe(403);
  });
});

// ── R-API-4  Executor mode restrictions ──────────────────────────────────

test.describe('R-API-4 executor_mode 限制', () => {
  test('R-API-4.1 非管理员 POST /runs executor_mode=subprocess → 400', async ({ page }) => {
    // RunRequest requires tests_path + runner; executor_mode=subprocess is the restricted field
    const resp = await authedPost(page, userAToken, '/runs', {
      tests_path: '/tmp/fake',
      runner: 'pytest',
      executor_mode: 'subprocess',
    });
    expect(resp.status()).toBe(400);
  });

  // R-API-4.2 并发超限 429 — skipped: creating 20 runs is too expensive for CI
  test.skip('R-API-4.2 非管理员并发超限 → 429', async () => {
    // TODO: needs a cheap way to create 20 queued+running runs
  });
});
