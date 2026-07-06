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
const ADMIN_PROFILE_TESTS_PATH = '/tmp/fake';

let adminToken: string;
let userAToken: string;
let userBToken: string;

interface CredentialResponse {
  id: string;
  name: string;
  type: string;
  created_by: string;
  secret?: string;
}

interface RunResponse {
  id: string;
  runner: string;
  status: string;
  created_by: string;
  locked?: boolean;
  env?: Record<string, string>;
  cases?: Array<{ suite: string; name: string; status: string }>;
}

interface RunTrendResponse {
  points: Array<{ run_id: string }>;
}

interface CaseHistoryResponse {
  points: Array<{ status: string }>;
  flaky: boolean;
  flip_count: number;
}

interface ScheduleResponse {
  id: string;
  name: string;
  profile_id: string;
  created_by: string;
}

interface LinkSuiteResponse {
  success: boolean;
  suite_name: string;
}

interface MetricsSummary {
  total_runs: number;
}

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
  test('R-API-1.1 GET /runs 非 admin 只见自己的且不泄露他人 env', async ({ page }) => {
    test.setTimeout(120_000);

    const stamp = Date.now();
    const adminEnvSecret = `admin_run_env_secret_${stamp}`;
    const userEnvSecret = `user_run_env_secret_${stamp}`;
    let adminProfileId: string | null = null;
    let userProfileId: string | null = null;
    let adminRunId: string | null = null;
    let userRunId: string | null = null;

    const waitTerminal = async (token: string, runId: string) => {
      await expect(async () => {
        const detailResp = await authedGet(page, token, `/runs/${encodeURIComponent(runId)}`);
        expect(detailResp.status(), await detailResp.text()).toBe(200);
        const detail = (await detailResp.json()) as RunResponse;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(detail.status);
      }).toPass({ timeout: 60_000, intervals: [1_000] });
    };

    try {
      const adminProfileResp = await authedPost(page, adminToken, '/profiles', {
        name: `admin_scope_profile_${stamp}`,
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
      });
      expect(adminProfileResp.status(), await adminProfileResp.text()).toBe(201);
      adminProfileId = (await adminProfileResp.json()).id;

      const adminRunResp = await authedPost(page, adminToken, '/runs', {
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
        args: [],
        allure: false,
        timeout: 60,
        selected_files: [],
        selected_markers: [],
        extra_args: '',
        env: { ADMIN_ONLY_RUN_TOKEN: adminEnvSecret },
      });
      expect(adminRunResp.status(), await adminRunResp.text()).toBe(202);
      adminRunId = ((await adminRunResp.json()) as RunResponse).id;

      const userProfileResp = await authedPost(page, userAToken, '/profiles', {
        name: `userA_scope_profile_${stamp}`,
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
      });
      expect(userProfileResp.status(), await userProfileResp.text()).toBe(201);
      userProfileId = (await userProfileResp.json()).id;

      const userRunResp = await authedPost(page, userAToken, '/runs', {
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
        args: [],
        allure: false,
        timeout: 60,
        selected_files: [],
        selected_markers: [],
        extra_args: '',
        env: { USER_RUN_TOKEN: userEnvSecret },
      });
      expect(userRunResp.status(), await userRunResp.text()).toBe(202);
      userRunId = ((await userRunResp.json()) as RunResponse).id;

      const listResp = await authedGet(page, userAToken, '/runs');
      const listBody = await listResp.text();
      expect(listResp.status(), listBody).toBe(200);
      expect(listBody).toContain(userRunId);
      expect(listBody).toContain(userEnvSecret);
      expect(listBody).not.toContain(adminRunId);
      expect(listBody).not.toContain(adminEnvSecret);

      const runs = (JSON.parse(listBody) as { runs: RunResponse[] }).runs ?? [];
      const runIds = runs.map((run) => run.id);
      expect(runIds).toContain(userRunId);
      expect(runIds).not.toContain(adminRunId);
    } finally {
      if (adminRunId) {
        await waitTerminal(adminToken, adminRunId);
        await authedDelete(page, adminToken, `/runs/${encodeURIComponent(adminRunId)}`).catch(
          () => {},
        );
      }
      if (userRunId) {
        await waitTerminal(userAToken, userRunId);
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(userRunId)}`).catch(
          () => {},
        );
      }
      if (adminProfileId) {
        await authedDelete(page, adminToken, `/profiles/${encodeURIComponent(adminProfileId)}`).catch(
          () => {},
        );
      }
      if (userProfileId) {
        await authedDelete(page, userAToken, `/profiles/${encodeURIComponent(userProfileId)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-1.2 GET /profiles 非 admin 只见自己的且不泄露他人 env/webhook', async ({
    page,
  }) => {
    const stamp = Date.now();
    const adminProfileName = `admin_only_profile_${stamp}`;
    const adminEnvSecret = `admin_env_secret_${stamp}`;
    const adminWebhook = `https://open.feishu.cn/open-apis/bot/v2/hook/admin_${stamp}`;
    let adminProfId: string | null = null;

    try {
      const adminProfResp = await authedPost(page, adminToken, '/profiles', {
        name: adminProfileName,
        tests_path: '/tmp/fake',
        runner: 'pytest',
        env: { ADMIN_ONLY_TOKEN: adminEnvSecret },
        webhook_url: adminWebhook,
      });
      expect(adminProfResp.status(), await adminProfResp.text()).toBe(201);
      adminProfId = (await adminProfResp.json()).id;

      const userAList = await authedGet(page, userAToken, '/profiles');
      const userAListBody = await userAList.text();
      expect(userAList.status(), userAListBody).toBe(200);
      expect(userAListBody).not.toContain(adminProfileName);
      expect(userAListBody).not.toContain(adminEnvSecret);
      expect(userAListBody).not.toContain(adminWebhook);

      const profiles = (JSON.parse(userAListBody) as Array<{ name: string }>).map(
        (profile) => profile.name,
      );
      expect(profiles).not.toContain(adminProfileName);
    } finally {
      if (adminProfId) {
        await authedDelete(page, adminToken, `/profiles/${encodeURIComponent(adminProfId)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-1.3 GET /credentials 非 admin 只见自己的且不回显 secret', async ({ page }) => {
    const createdCredentialIds: string[] = [];
    const stamp = Date.now();
    const adminSecret = `ghp_admin_secret_${stamp}`;
    const userASecret = `ghp_userA_secret_${stamp}`;

    try {
      const adminCreateResp = await authedPost(page, adminToken, '/credentials', {
        name: `admin_credential_${stamp}`,
        type: 'https_token',
        secret: adminSecret,
      });
      expect(adminCreateResp.status(), await adminCreateResp.text()).toBe(201);
      const adminCredential = (await adminCreateResp.json()) as CredentialResponse;
      createdCredentialIds.push(adminCredential.id);
      expect(adminCredential.created_by).toBe(E2E_ADMIN);
      expect(adminCredential.secret).toBeUndefined();
      expect(JSON.stringify(adminCredential)).not.toContain(adminSecret);

      const userACreateResp = await authedPost(page, userAToken, '/credentials', {
        name: `userA_credential_${stamp}`,
        type: 'https_token',
        secret: userASecret,
      });
      expect(userACreateResp.status(), await userACreateResp.text()).toBe(201);
      const userACredential = (await userACreateResp.json()) as CredentialResponse;
      createdCredentialIds.push(userACredential.id);
      expect(userACredential.created_by).toBe(USER_A);
      expect(userACredential.secret).toBeUndefined();
      expect(JSON.stringify(userACredential)).not.toContain(userASecret);

      const userAListResp = await authedGet(page, userAToken, '/credentials');
      expect(userAListResp.status(), await userAListResp.text()).toBe(200);
      const userAList = (await userAListResp.json()) as { credentials: CredentialResponse[] };
      const userACredentialIds = userAList.credentials.map((credential) => credential.id);
      expect(userACredentialIds).toContain(userACredential.id);
      expect(userACredentialIds).not.toContain(adminCredential.id);
      expect(JSON.stringify(userAList)).not.toContain(userASecret);
      expect(JSON.stringify(userAList)).not.toContain(adminSecret);
      expect(userAList.credentials.every((credential) => credential.secret === undefined)).toBe(
        true,
      );

      const adminListResp = await authedGet(page, adminToken, '/credentials');
      expect(adminListResp.status(), await adminListResp.text()).toBe(200);
      const adminList = (await adminListResp.json()) as { credentials: CredentialResponse[] };
      const adminCredentialIds = adminList.credentials.map((credential) => credential.id);
      expect(adminCredentialIds).toContain(adminCredential.id);
      expect(adminCredentialIds).toContain(userACredential.id);
      expect(JSON.stringify(adminList)).not.toContain(userASecret);
      expect(JSON.stringify(adminList)).not.toContain(adminSecret);
      expect(adminList.credentials.every((credential) => credential.secret === undefined)).toBe(
        true,
      );
    } finally {
      for (const credentialId of createdCredentialIds) {
        await authedDelete(page, adminToken, `/credentials/${credentialId}`).catch(() => {});
      }
    }
  });

  test('R-API-1.4 GET /runs/trend 非 admin 即使指定 profile_id 也不泄露他人 run_id', async ({
    page,
  }) => {
    test.setTimeout(180_000);

    let adminProfileId: string | null = null;
    let adminRunId: string | null = null;
    let userProfileId: string | null = null;
    let userRunId: string | null = null;

    try {
      const adminProfileResp = await authedPost(page, adminToken, '/profiles', {
        name: `r1_trend_admin_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      expect(adminProfileResp.status(), await adminProfileResp.text()).toBe(201);
      adminProfileId = (await adminProfileResp.json()).id;

      const userProfileResp = await authedPost(page, userAToken, '/profiles', {
        name: `r1_trend_user_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      expect(userProfileResp.status(), await userProfileResp.text()).toBe(201);
      userProfileId = (await userProfileResp.json()).id;

      const adminRunResp = await authedPost(
        page,
        adminToken,
        `/profiles/${encodeURIComponent(adminProfileId!)}/trigger`,
        {},
      );
      expect(adminRunResp.status(), await adminRunResp.text()).toBe(202);
      const adminRun = (await adminRunResp.json()) as RunResponse;
      adminRunId = adminRun.id;
      expect(adminRun.runner).toBe('playwright');
      expect(adminRun.created_by).toBe(E2E_ADMIN);

      const userRunResp = await authedPost(
        page,
        userAToken,
        `/profiles/${encodeURIComponent(userProfileId!)}/trigger`,
        {},
      );
      expect(userRunResp.status(), await userRunResp.text()).toBe(202);
      const userRun = (await userRunResp.json()) as RunResponse;
      userRunId = userRun.id;
      expect(userRun.runner).toBe('playwright');
      expect(userRun.created_by).toBe(USER_A);

      for (const [token, runId] of [
        [adminToken, adminRunId],
        [userAToken, userRunId],
      ] as const) {
        let terminalStatus = '';
        await expect(async () => {
          const detailResp = await authedGet(page, token, `/runs/${encodeURIComponent(runId!)}`);
          expect(detailResp.status(), await detailResp.text()).toBe(200);
          const detail = (await detailResp.json()) as RunResponse;
          terminalStatus = detail.status;
          expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(terminalStatus);
        }).toPass({ timeout: 120_000, intervals: [2_000] });
        expect(terminalStatus).toBe('completed');
      }

      const ownTrendResp = await authedGet(
        page,
        userAToken,
        `/runs/trend?tests_path=${encodeURIComponent(PLAYWRIGHT_SUITE)}&profile_id=${encodeURIComponent(
          userProfileId!,
        )}&limit=100`,
      );
      expect(ownTrendResp.status(), await ownTrendResp.text()).toBe(200);
      const ownTrend = (await ownTrendResp.json()) as RunTrendResponse;
      expect(ownTrend.points.map((point) => point.run_id)).toContain(userRunId);

      const crossOwnerTrendResp = await authedGet(
        page,
        userAToken,
        `/runs/trend?tests_path=${encodeURIComponent(PLAYWRIGHT_SUITE)}&profile_id=${encodeURIComponent(
          adminProfileId!,
        )}&limit=100`,
      );
      expect(crossOwnerTrendResp.status(), await crossOwnerTrendResp.text()).toBe(200);
      const crossOwnerTrend = (await crossOwnerTrendResp.json()) as RunTrendResponse;
      expect(crossOwnerTrend.points.map((point) => point.run_id)).not.toContain(adminRunId);

      const adminTrendResp = await authedGet(
        page,
        adminToken,
        `/runs/trend?tests_path=${encodeURIComponent(PLAYWRIGHT_SUITE)}&profile_id=${encodeURIComponent(
          adminProfileId!,
        )}&limit=100`,
      );
      expect(adminTrendResp.status(), await adminTrendResp.text()).toBe(200);
      const adminTrend = (await adminTrendResp.json()) as RunTrendResponse;
      expect(adminTrend.points.map((point) => point.run_id)).toContain(adminRunId);
    } finally {
      if (adminRunId) {
        await authedDelete(page, adminToken, `/runs/${encodeURIComponent(adminRunId)}`).catch(
          () => {},
        );
      }
      if (userRunId) {
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(userRunId)}`).catch(
          () => {},
        );
      }
      if (adminProfileId) {
        await authedDelete(page, adminToken, `/profiles/${encodeURIComponent(adminProfileId)}`).catch(
          () => {},
        );
      }
      if (userProfileId) {
        await authedDelete(page, userAToken, `/profiles/${encodeURIComponent(userProfileId)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-1.5 GET /cases/history 非 admin 即使指定 profile_id 也不泄露他人 case history', async ({
    page,
  }) => {
    test.setTimeout(180_000);

    let adminProfileId: string | null = null;
    let adminRunId: string | null = null;
    let userProfileId: string | null = null;
    let userRunId: string | null = null;

    try {
      const adminProfileResp = await authedPost(page, adminToken, '/profiles', {
        name: `r1_history_admin_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      expect(adminProfileResp.status(), await adminProfileResp.text()).toBe(201);
      adminProfileId = (await adminProfileResp.json()).id;

      const userProfileResp = await authedPost(page, userAToken, '/profiles', {
        name: `r1_history_user_${Date.now()}`,
        tests_path: PLAYWRIGHT_SUITE,
        runner: 'playwright',
      });
      expect(userProfileResp.status(), await userProfileResp.text()).toBe(201);
      userProfileId = (await userProfileResp.json()).id;

      const adminRunResp = await authedPost(
        page,
        adminToken,
        `/profiles/${encodeURIComponent(adminProfileId!)}/trigger`,
        {},
      );
      expect(adminRunResp.status(), await adminRunResp.text()).toBe(202);
      const adminRun = (await adminRunResp.json()) as RunResponse;
      adminRunId = adminRun.id;
      expect(adminRun.runner).toBe('playwright');
      expect(adminRun.created_by).toBe(E2E_ADMIN);

      const userRunResp = await authedPost(
        page,
        userAToken,
        `/profiles/${encodeURIComponent(userProfileId!)}/trigger`,
        {},
      );
      expect(userRunResp.status(), await userRunResp.text()).toBe(202);
      const userRun = (await userRunResp.json()) as RunResponse;
      userRunId = userRun.id;
      expect(userRun.runner).toBe('playwright');
      expect(userRun.created_by).toBe(USER_A);

      for (const [token, runId] of [
        [adminToken, adminRunId],
        [userAToken, userRunId],
      ] as const) {
        let terminalStatus = '';
        await expect(async () => {
          const detailResp = await authedGet(page, token, `/runs/${encodeURIComponent(runId!)}`);
          expect(detailResp.status(), await detailResp.text()).toBe(200);
          const detail = (await detailResp.json()) as RunResponse;
          terminalStatus = detail.status;
          expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(terminalStatus);
        }).toPass({ timeout: 120_000, intervals: [2_000] });
        expect(terminalStatus).toBe('completed');
      }

      const adminDetailResp = await authedGet(
        page,
        adminToken,
        `/runs/${encodeURIComponent(adminRunId!)}`,
      );
      expect(adminDetailResp.status(), await adminDetailResp.text()).toBe(200);
      const adminDetail = (await adminDetailResp.json()) as RunResponse;
      expect(adminDetail.cases?.length ?? 0).toBeGreaterThan(0);
      const adminCase = adminDetail.cases![0];

      const historyEndpoint = (profileId: string) => {
        const qs = new URLSearchParams({
          tests_path: PLAYWRIGHT_SUITE,
          suite: adminCase.suite,
          name: adminCase.name,
          profile_id: profileId,
          limit: '20',
        });
        return `/cases/history?${qs.toString()}`;
      };

      const ownHistoryResp = await authedGet(page, userAToken, historyEndpoint(userProfileId!));
      expect(ownHistoryResp.status(), await ownHistoryResp.text()).toBe(200);
      const ownHistory = (await ownHistoryResp.json()) as CaseHistoryResponse;
      expect(ownHistory.points.length).toBeGreaterThan(0);

      const crossOwnerHistoryResp = await authedGet(
        page,
        userAToken,
        historyEndpoint(adminProfileId!),
      );
      expect(crossOwnerHistoryResp.status(), await crossOwnerHistoryResp.text()).toBe(200);
      const crossOwnerHistory = (await crossOwnerHistoryResp.json()) as CaseHistoryResponse;
      expect(crossOwnerHistory.points).toEqual([]);
      expect(crossOwnerHistory.flaky).toBe(false);
      expect(crossOwnerHistory.flip_count).toBe(0);

      const adminHistoryResp = await authedGet(page, adminToken, historyEndpoint(adminProfileId!));
      expect(adminHistoryResp.status(), await adminHistoryResp.text()).toBe(200);
      const adminHistory = (await adminHistoryResp.json()) as CaseHistoryResponse;
      expect(adminHistory.points.length).toBeGreaterThan(0);
    } finally {
      if (adminRunId) {
        await authedDelete(page, adminToken, `/runs/${encodeURIComponent(adminRunId)}`).catch(
          () => {},
        );
      }
      if (userRunId) {
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(userRunId)}`).catch(
          () => {},
        );
      }
      if (adminProfileId) {
        await authedDelete(page, adminToken, `/profiles/${encodeURIComponent(adminProfileId)}`).catch(
          () => {},
        );
      }
      if (userProfileId) {
        await authedDelete(page, userAToken, `/profiles/${encodeURIComponent(userProfileId)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-1.6 GET /metrics 非 admin 只聚合自己的 runs', async ({ page }) => {
    test.setTimeout(120_000);

    const readMetrics = async (token: string): Promise<MetricsSummary> => {
      const resp = await authedGet(page, token, '/metrics');
      expect(resp.status(), await resp.text()).toBe(200);
      return (await resp.json()) as MetricsSummary;
    };

    const beforeUserMetrics = await readMetrics(userAToken);
    const beforeAdminMetrics = await readMetrics(adminToken);
    let adminRunId: string | null = null;
    let userRunId: string | null = null;

    try {
      const adminRunResp = await authedPost(page, adminToken, '/runs', {
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
      expect(adminRunResp.status(), await adminRunResp.text()).toBe(202);
      const adminRun = (await adminRunResp.json()) as RunResponse;
      adminRunId = adminRun.id;
      expect(adminRun.created_by).toBe(E2E_ADMIN);

      const userRunResp = await authedPost(page, userAToken, '/runs', {
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
      expect(userRunResp.status(), await userRunResp.text()).toBe(202);
      const userRun = (await userRunResp.json()) as RunResponse;
      userRunId = userRun.id;
      expect(userRun.created_by).toBe(USER_A);

      const afterUserMetrics = await readMetrics(userAToken);
      expect(afterUserMetrics.total_runs).toBe(beforeUserMetrics.total_runs + 1);

      const afterAdminMetrics = await readMetrics(adminToken);
      expect(afterAdminMetrics.total_runs).toBe(beforeAdminMetrics.total_runs + 2);
    } finally {
      for (const [token, runId] of [
        [adminToken, adminRunId],
        [userAToken, userRunId],
      ] as const) {
        if (!runId) continue;
        await expect(async () => {
          const detailResp = await authedGet(page, token, `/runs/${encodeURIComponent(runId)}`);
          expect(detailResp.status(), await detailResp.text()).toBe(200);
          const detail = (await detailResp.json()) as RunResponse;
          expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(detail.status);
        }).toPass({ timeout: 60_000, intervals: [1_000] });
        await authedDelete(page, token, `/runs/${encodeURIComponent(runId)}`).catch(() => {});
      }
    }
  });

  test('R-API-1.7 GET /schedules 非 admin 只见自己的且 profile_id 不泄露他人 schedule', async ({
    page,
  }) => {
    let adminProfileId: string | null = null;
    let userProfileId: string | null = null;
    let adminScheduleId: string | null = null;
    let userScheduleId: string | null = null;
    const stamp = Date.now();
    const adminScheduleName = `r1_schedule_admin_${stamp}`;
    const userScheduleName = `r1_schedule_user_${stamp}`;

    try {
      const adminProfileResp = await authedPost(page, adminToken, '/profiles', {
        name: `r1_schedule_admin_profile_${stamp}`,
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
      });
      expect(adminProfileResp.status(), await adminProfileResp.text()).toBe(201);
      adminProfileId = (await adminProfileResp.json()).id;

      const userProfileResp = await authedPost(page, userAToken, '/profiles', {
        name: `r1_schedule_user_profile_${stamp}`,
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
      });
      expect(userProfileResp.status(), await userProfileResp.text()).toBe(201);
      userProfileId = (await userProfileResp.json()).id;

      const adminScheduleResp = await authedPost(page, adminToken, '/schedules', {
        name: adminScheduleName,
        profile_id: adminProfileId,
        cron_expression: '0 3 * * *',
        timezone: 'UTC',
        enabled: true,
      });
      expect(adminScheduleResp.status(), await adminScheduleResp.text()).toBe(201);
      const adminSchedule = (await adminScheduleResp.json()) as ScheduleResponse;
      adminScheduleId = adminSchedule.id;
      expect(adminSchedule.created_by).toBe(E2E_ADMIN);

      const userScheduleResp = await authedPost(page, userAToken, '/schedules', {
        name: userScheduleName,
        profile_id: userProfileId,
        cron_expression: '0 4 * * *',
        timezone: 'UTC',
        enabled: true,
      });
      expect(userScheduleResp.status(), await userScheduleResp.text()).toBe(201);
      const userSchedule = (await userScheduleResp.json()) as ScheduleResponse;
      userScheduleId = userSchedule.id;
      expect(userSchedule.created_by).toBe(USER_A);

      const userListResp = await authedGet(page, userAToken, '/schedules');
      const userListBody = await userListResp.text();
      expect(userListResp.status(), userListBody).toBe(200);
      expect(userListBody).not.toContain(adminScheduleName);
      expect(userListBody).not.toContain(adminProfileId);
      expect(userListBody).toContain(userScheduleName);
      const userSchedules = JSON.parse(userListBody) as ScheduleResponse[];
      const userScheduleIds = userSchedules.map((schedule) => schedule.id);
      expect(userScheduleIds).toContain(userScheduleId);
      expect(userScheduleIds).not.toContain(adminScheduleId);

      const filteredByAdminProfileResp = await authedGet(
        page,
        userAToken,
        `/schedules?profile_id=${encodeURIComponent(adminProfileId!)}`,
      );
      const filteredByAdminProfileBody = await filteredByAdminProfileResp.text();
      expect(filteredByAdminProfileResp.status(), filteredByAdminProfileBody).toBe(200);
      expect(filteredByAdminProfileBody).not.toContain(adminScheduleName);
      expect(filteredByAdminProfileBody).not.toContain(adminProfileId);
      const filteredByAdminProfile = JSON.parse(filteredByAdminProfileBody) as ScheduleResponse[];
      expect(filteredByAdminProfile.map((schedule) => schedule.id)).not.toContain(adminScheduleId);

      const filteredOwnProfileResp = await authedGet(
        page,
        userAToken,
        `/schedules?profile_id=${encodeURIComponent(userProfileId!)}`,
      );
      expect(filteredOwnProfileResp.status(), await filteredOwnProfileResp.text()).toBe(200);
      const filteredOwnProfile = (await filteredOwnProfileResp.json()) as ScheduleResponse[];
      expect(filteredOwnProfile.map((schedule) => schedule.id)).toContain(userScheduleId);

      const adminListResp = await authedGet(page, adminToken, '/schedules');
      expect(adminListResp.status(), await adminListResp.text()).toBe(200);
      const adminSchedules = (await adminListResp.json()) as ScheduleResponse[];
      const adminScheduleIds = adminSchedules.map((schedule) => schedule.id);
      expect(adminScheduleIds).toContain(adminScheduleId);
      expect(adminScheduleIds).toContain(userScheduleId);
    } finally {
      if (adminScheduleId) {
        await authedDelete(
          page,
          adminToken,
          `/schedules/${encodeURIComponent(adminScheduleId)}`,
        ).catch(() => {});
      }
      if (userScheduleId) {
        await authedDelete(
          page,
          userAToken,
          `/schedules/${encodeURIComponent(userScheduleId)}`,
        ).catch(() => {});
      }
      if (adminProfileId) {
        await authedDelete(page, adminToken, `/profiles/${encodeURIComponent(adminProfileId)}`).catch(
          () => {},
        );
      }
      if (userProfileId) {
        await authedDelete(page, userAToken, `/profiles/${encodeURIComponent(userProfileId)}`).catch(
          () => {},
        );
      }
    }
  });
});

// ── R-API-2  Cross-role single-resource access ───────────────────────────

test.describe('R-API-2 越权单资源', () => {
  let adminProfileId: string;
  let adminProfileName: string;

  test.beforeEach(async ({ page }) => {
    adminProfileName = `r2_admin_${Date.now()}`;
    const resp = await authedPost(page, adminToken, '/profiles', {
      name: adminProfileName,
      tests_path: ADMIN_PROFILE_TESTS_PATH,
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
    const body = await resp.text();
    expect(resp.status(), body).toBe(403);
    expect(body).not.toContain(adminProfileId);
    expect(body).not.toContain(adminProfileName);
    expect(body).not.toContain(ADMIN_PROFILE_TESTS_PATH);
  });

  test('R-API-2.2 PUT /profiles/{不存在 id} 以 userA token → 404', async ({ page }) => {
    const resp = await authedPut(page, userAToken, '/profiles/00000000-0000-0000-0000-000000000000', {
      name: 'ghost',
      tests_path: '/tmp/ghost',
    });
    expect(resp.status()).toBe(404);
  });

  test('R-API-2.3 DELETE|trigger /profiles/{profile} 遵守 owner/admin 边界', async ({
    page,
  }) => {
    let profileId: string | null = null;
    try {
      const profileResp = await authedPost(page, userAToken, '/profiles', {
        name: `r2_profile_scope_${Date.now()}`,
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
      });
      expect(profileResp.status(), await profileResp.text()).toBe(201);
      const profile = await profileResp.json();
      profileId = profile.id;
      const profileName = profile.name as string;
      expect(profile.created_by).toBe(USER_A);

      const userBTriggerResp = await authedPost(
        page,
        userBToken,
        `/profiles/${encodeURIComponent(profileId!)}/trigger`,
        {},
      );
      const userBTriggerBody = await userBTriggerResp.text();
      expect(userBTriggerResp.status(), userBTriggerBody).toBe(403);
      expect(userBTriggerBody).not.toContain(profileId!);
      expect(userBTriggerBody).not.toContain(profileName);
      expect(userBTriggerBody).not.toContain(PYTEST_SUITE);

      const userBDeleteResp = await authedDelete(
        page,
        userBToken,
        `/profiles/${encodeURIComponent(profileId!)}`,
      );
      const userBDeleteBody = await userBDeleteResp.text();
      expect(userBDeleteResp.status(), userBDeleteBody).toBe(403);
      expect(userBDeleteBody).not.toContain(profileId!);
      expect(userBDeleteBody).not.toContain(profileName);
      expect(userBDeleteBody).not.toContain(PYTEST_SUITE);

      const adminDeleteResp = await authedDelete(
        page,
        adminToken,
        `/profiles/${encodeURIComponent(profileId!)}`,
      );
      expect(adminDeleteResp.status(), await adminDeleteResp.text()).toBe(200);
      profileId = null;
    } finally {
      if (profileId) {
        await authedDelete(page, adminToken, `/profiles/${encodeURIComponent(profileId)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-2.4 DELETE /runs/{admin_run} 以 userA token → 403', async ({ page }) => {
    test.setTimeout(120_000);

    let runId: string | null = null;
    try {
      const runResp = await authedPost(page, adminToken, '/runs', {
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
      const run = (await runResp.json()) as RunResponse;
      runId = run.id;
      expect(run.created_by).toBe(E2E_ADMIN);

      const delResp = await authedDelete(page, userAToken, `/runs/${encodeURIComponent(runId)}`);
      const delBody = await delResp.text();
      expect(delResp.status(), delBody).toBe(403);
      expect(delBody).not.toContain(runId);
    } finally {
      if (runId) {
        await expect(async () => {
          const detailResp = await authedGet(page, adminToken, `/runs/${encodeURIComponent(runId!)}`);
          expect(detailResp.status(), await detailResp.text()).toBe(200);
          const detail = (await detailResp.json()) as RunResponse;
          expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(detail.status);
        }).toPass({ timeout: 60_000, intervals: [1_000] });
        await authedDelete(page, adminToken, `/runs/${encodeURIComponent(runId)}`).catch(() => {});
      }
    }
  });

  test('R-API-2.5 DELETE /schedules/{admin_schedule} 以 userA token → 403', async ({ page }) => {
    // Create a schedule as admin
    const scheduleName = `admin_sched_r2_${Date.now()}`;
    const schedResp = await authedPost(page, adminToken, '/schedules', {
      name: scheduleName,
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
    const delBody = await delResp.text();
    expect(delResp.status(), delBody).toBe(403);
    expect(delBody).not.toContain(schedId);
    expect(delBody).not.toContain(scheduleName);
    expect(delBody).not.toContain(adminProfileId);

    // Cleanup
    await authedDelete(page, adminToken, `/schedules/${schedId}`);
  });

  test('R-API-2.6 GET|PUT|trigger /schedules/{user_schedule} 遵守 owner/admin 边界', async ({
    page,
  }) => {
    let userProfileId: string | null = null;
    let scheduleId: string | null = null;

    try {
      const profileResp = await authedPost(page, userAToken, '/profiles', {
        name: `r2_schedule_profile_${Date.now()}`,
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
      });
      expect(profileResp.status(), await profileResp.text()).toBe(201);
      userProfileId = (await profileResp.json()).id;

      const scheduleResp = await authedPost(page, userAToken, '/schedules', {
        name: `r2_schedule_${Date.now()}`,
        profile_id: userProfileId,
        cron_expression: '0 3 * * *',
        timezone: 'UTC',
        enabled: true,
      });
      expect(scheduleResp.status(), await scheduleResp.text()).toBe(201);
      const schedule = (await scheduleResp.json()) as ScheduleResponse;
      scheduleId = schedule.id;
      const scheduleName = schedule.name;
      expect(schedule.created_by).toBe(USER_A);

      const userBGetResp = await authedGet(page, userBToken, `/schedules/${scheduleId}`);
      const userBGetBody = await userBGetResp.text();
      expect(userBGetResp.status(), userBGetBody).toBe(403);
      expect(userBGetBody).not.toContain(scheduleId);
      expect(userBGetBody).not.toContain(scheduleName);
      expect(userBGetBody).not.toContain(userProfileId);

      const userBPutResp = await authedPut(page, userBToken, `/schedules/${scheduleId}`, {
        name: 'hijacked_schedule',
        profile_id: userProfileId,
        cron_expression: '0 4 * * *',
        timezone: 'UTC',
        enabled: false,
      });
      const userBPutBody = await userBPutResp.text();
      expect(userBPutResp.status(), userBPutBody).toBe(403);
      expect(userBPutBody).not.toContain(scheduleId);
      expect(userBPutBody).not.toContain(scheduleName);
      expect(userBPutBody).not.toContain(userProfileId);

      const userBTriggerResp = await authedPost(
        page,
        userBToken,
        `/schedules/${scheduleId}/trigger`,
        {},
      );
      const userBTriggerBody = await userBTriggerResp.text();
      expect(userBTriggerResp.status(), userBTriggerBody).toBe(403);
      expect(userBTriggerBody).not.toContain(scheduleId);
      expect(userBTriggerBody).not.toContain(scheduleName);
      expect(userBTriggerBody).not.toContain(userProfileId);

      const ownerGetResp = await authedGet(page, userAToken, `/schedules/${scheduleId}`);
      expect(ownerGetResp.status(), await ownerGetResp.text()).toBe(200);
      const ownerSchedule = (await ownerGetResp.json()) as ScheduleResponse;
      expect(ownerSchedule.created_by).toBe(USER_A);
      expect(ownerSchedule.profile_id).toBe(userProfileId);

      const adminGetResp = await authedGet(page, adminToken, `/schedules/${scheduleId}`);
      expect(adminGetResp.status(), await adminGetResp.text()).toBe(200);
      const adminSchedule = (await adminGetResp.json()) as ScheduleResponse;
      expect(adminSchedule.created_by).toBe(USER_A);
      expect(adminSchedule.profile_id).toBe(userProfileId);
    } finally {
      if (scheduleId) {
        await authedDelete(page, userAToken, `/schedules/${encodeURIComponent(scheduleId)}`).catch(
          () => {},
        );
      }
      if (userProfileId) {
        await authedDelete(page, userAToken, `/profiles/${encodeURIComponent(userProfileId)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-2.7 GET /runs/{admin_run}/artifacts* 以 userA token → 403', async ({ page }) => {
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

  test('R-API-2.8 GET /runs/{own_run}/artifacts* 以 owner user token → 200', async ({ page }) => {
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

  test('R-API-2.9 GET /runs/{run}/diff|report|stream 遵守 owner/admin 边界', async ({
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

  test('R-API-2.16 GET /runs/{run}/report/{path} 遵守 owner/admin 边界', async ({
    page,
  }) => {
    test.setTimeout(120_000);

    let runId: string | null = null;
    try {
      const runResp = await authedPost(page, userAToken, '/runs', {
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
        args: [],
        allure: true,
        timeout: 60,
        selected_files: [],
        selected_markers: [],
        extra_args: '',
        env: {},
      });
      expect(runResp.status(), await runResp.text()).toBe(202);
      const run = (await runResp.json()) as RunResponse;
      runId = run.id;
      expect(run.runner).toBe('pytest');
      expect(run.created_by).toBe(USER_A);

      await expect(async () => {
        const detailResp = await authedGet(page, userAToken, `/runs/${encodeURIComponent(runId!)}`);
        expect(detailResp.status(), await detailResp.text()).toBe(200);
        const detail = (await detailResp.json()) as RunResponse;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(detail.status);
      }).toPass({ timeout: 60_000, intervals: [1_000] });

      const reportAssetEndpoint = `/runs/${encodeURIComponent(runId!)}/report/index.html`;

      for (const token of [userAToken, adminToken]) {
        const reportAssetResp = await authedGet(page, token, reportAssetEndpoint);
        const reportAssetBody = await reportAssetResp.text();
        // Live Allure generation is best-effort in this environment; owner/admin
        // may see the generated asset or a report-availability 404, but must not
        // be rejected by authorization.
        expect([200, 404], reportAssetBody).toContain(reportAssetResp.status());
        expect(reportAssetResp.status(), reportAssetBody).not.toBe(403);
      }

      const userBResp = await authedGet(page, userBToken, reportAssetEndpoint);
      const userBBody = await userBResp.text();
      expect(userBResp.status(), userBBody).toBe(403);
      expect(userBBody).not.toContain(runId!);
      expect(userBBody).not.toContain('index.html');
    } finally {
      if (runId) {
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(runId)}`).catch(() => {});
      }
    }
  });

  test('R-API-2.10 DELETE /credentials/{credential} 遵守 owner/admin 边界', async ({ page }) => {
    const stamp = Date.now();
    const credentialName = `userA_delete_scope_${stamp}`;
    const secret = `ghp_delete_scope_${stamp}`;
    let credentialId: string | null = null;

    try {
      const createResp = await authedPost(page, userAToken, '/credentials', {
        name: credentialName,
        type: 'https_token',
        secret,
      });
      expect(createResp.status(), await createResp.text()).toBe(201);
      const credential = (await createResp.json()) as CredentialResponse;
      credentialId = credential.id;
      expect(credential.created_by).toBe(USER_A);
      expect(JSON.stringify(credential)).not.toContain(secret);

      const userBDeleteResp = await authedDelete(
        page,
        userBToken,
        `/credentials/${credentialId}`,
      );
      const userBDeleteBody = await userBDeleteResp.text();
      expect(userBDeleteResp.status(), userBDeleteBody).toBe(403);
      expect(userBDeleteBody).not.toContain(credentialId);
      expect(userBDeleteBody).not.toContain(credentialName);
      expect(userBDeleteBody).not.toContain(secret);

      const userAListResp = await authedGet(page, userAToken, '/credentials');
      expect(userAListResp.status(), await userAListResp.text()).toBe(200);
      const userAList = (await userAListResp.json()) as { credentials: CredentialResponse[] };
      expect(userAList.credentials.map((item) => item.id)).toContain(credentialId);

      const adminDeleteResp = await authedDelete(page, adminToken, `/credentials/${credentialId}`);
      expect(adminDeleteResp.status(), await adminDeleteResp.text()).toBe(200);
      credentialId = null;
    } finally {
      if (credentialId) {
        await authedDelete(page, adminToken, `/credentials/${credentialId}`).catch(() => {});
      }
    }
  });

  test('R-API-2.11 /tests/link|delete 遵守 suite owner/admin 边界', async ({ page }) => {
    const suiteName = `r2_suite_scope_${Date.now()}`;
    let linked = false;

    try {
      const ownerLinkResp = await authedPost(page, userAToken, '/tests/link', {
        path: `/tmp/${suiteName}`,
      });
      expect(ownerLinkResp.status(), await ownerLinkResp.text()).toBe(200);
      const ownerLink = (await ownerLinkResp.json()) as LinkSuiteResponse;
      linked = true;
      expect(ownerLink.success).toBe(true);
      expect(ownerLink.suite_name).toBe(suiteName);

      const userBRelinkResp = await authedPost(page, userBToken, '/tests/link', {
        path: `/tmp/attacker/${suiteName}`,
      });
      const userBRelinkBody = await userBRelinkResp.text();
      expect(userBRelinkResp.status(), userBRelinkBody).toBe(403);
      expect(userBRelinkBody).not.toContain(suiteName);

      const userBDeleteResp = await authedDelete(page, userBToken, `/tests/${suiteName}`);
      const userBDeleteBody = await userBDeleteResp.text();
      expect(userBDeleteResp.status(), userBDeleteBody).toBe(403);
      expect(userBDeleteBody).not.toContain(suiteName);

      const adminDeleteResp = await authedDelete(page, adminToken, `/tests/${suiteName}`);
      expect(adminDeleteResp.status(), await adminDeleteResp.text()).toBe(200);
      linked = false;
    } finally {
      if (linked) {
        await authedDelete(page, adminToken, `/tests/${suiteName}`).catch(() => {});
      }
    }
  });

  test('R-API-2.17 POST /tests/clone 禁止使用他人 credential_ref', async ({ page }) => {
    const stamp = Date.now();
    const credentialName = `userB_clone_credential_${stamp}`;
    const secret = `ghp_clone_scope_${stamp}`;
    const suiteName = `r2_clone_scope_${stamp}`;
    const repoUrl = `https://example.com/org/${suiteName}.git`;
    let credentialId: string | null = null;

    try {
      const createResp = await authedPost(page, userBToken, '/credentials', {
        name: credentialName,
        type: 'https_token',
        secret,
      });
      expect(createResp.status(), await createResp.text()).toBe(201);
      const credential = (await createResp.json()) as CredentialResponse;
      credentialId = credential.id;
      expect(credential.created_by).toBe(USER_B);
      expect(JSON.stringify(credential)).not.toContain(secret);

      const cloneResp = await authedPost(page, userAToken, '/tests/clone', {
        url: repoUrl,
        name: suiteName,
        credential_ref: credentialId,
      });
      const cloneBody = await cloneResp.text();
      expect(cloneResp.status(), cloneBody).toBe(403);
      expect(cloneBody).not.toContain(credentialId);
      expect(cloneBody).not.toContain(credentialName);
      expect(cloneBody).not.toContain(secret);
      expect(cloneBody).not.toContain(suiteName);
      expect(cloneBody).not.toContain(repoUrl);
    } finally {
      if (credentialId) {
        await authedDelete(page, adminToken, `/credentials/${credentialId}`).catch(() => {});
      }
    }
  });

  test('R-API-2.12 POST /runs/{run}/rerun 遵守 owner/admin 边界', async ({ page }) => {
    test.setTimeout(120_000);

    const cleanupRuns: Array<{ token: string; id: string }> = [];

    const waitTerminal = async (token: string, runId: string) => {
      await expect(async () => {
        const detailResp = await authedGet(page, token, `/runs/${encodeURIComponent(runId)}`);
        expect(detailResp.status(), await detailResp.text()).toBe(200);
        const detail = (await detailResp.json()) as RunResponse;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(detail.status);
      }).toPass({ timeout: 60_000, intervals: [1_000] });
    };

    try {
      const originalResp = await authedPost(page, userAToken, '/runs', {
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
      expect(originalResp.status(), await originalResp.text()).toBe(202);
      const original = (await originalResp.json()) as RunResponse;
      cleanupRuns.push({ token: userAToken, id: original.id });
      expect(original.created_by).toBe(USER_A);

      const userBRerunResp = await authedPost(
        page,
        userBToken,
        `/runs/${encodeURIComponent(original.id)}/rerun`,
        {},
      );
      const userBRerunBody = await userBRerunResp.text();
      expect(userBRerunResp.status(), userBRerunBody).toBe(403);
      expect(userBRerunBody).not.toContain(original.id);

      const ownerRerunResp = await authedPost(
        page,
        userAToken,
        `/runs/${encodeURIComponent(original.id)}/rerun`,
        {},
      );
      expect(ownerRerunResp.status(), await ownerRerunResp.text()).toBe(202);
      const ownerRerun = (await ownerRerunResp.json()) as RunResponse;
      cleanupRuns.push({ token: userAToken, id: ownerRerun.id });
      expect(ownerRerun.created_by).toBe(USER_A);

      const adminRerunResp = await authedPost(
        page,
        adminToken,
        `/runs/${encodeURIComponent(original.id)}/rerun`,
        {},
      );
      expect(adminRerunResp.status(), await adminRerunResp.text()).toBe(202);
      const adminRerun = (await adminRerunResp.json()) as RunResponse;
      cleanupRuns.push({ token: adminToken, id: adminRerun.id });
      expect(adminRerun.created_by).toBe(E2E_ADMIN);
    } finally {
      for (const run of cleanupRuns) {
        await waitTerminal(run.token, run.id);
        await authedDelete(page, run.token, `/runs/${encodeURIComponent(run.id)}`).catch(() => {});
      }
    }
  });

  test('R-API-2.13 PUT /runs/{run}/lock|POST /runs/{run}/cancel 遵守 owner/admin 边界', async ({
    page,
  }) => {
    test.setTimeout(120_000);

    let runId: string | undefined;

    const waitTerminal = async (token: string, id: string) => {
      await expect(async () => {
        const detailResp = await authedGet(page, token, `/runs/${encodeURIComponent(id)}`);
        expect(detailResp.status(), await detailResp.text()).toBe(200);
        const detail = (await detailResp.json()) as RunResponse;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(detail.status);
      }).toPass({ timeout: 60_000, intervals: [1_000] });
    };

    try {
      const createResp = await authedPost(page, userAToken, '/runs', {
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
      expect(createResp.status(), await createResp.text()).toBe(202);
      const created = (await createResp.json()) as RunResponse;
      runId = created.id;
      expect(created.created_by).toBe(USER_A);

      await waitTerminal(userAToken, runId);

      const userBLockResp = await authedPut(
        page,
        userBToken,
        `/runs/${encodeURIComponent(runId)}/lock`,
        { locked: true },
      );
      const userBLockBody = await userBLockResp.text();
      expect(userBLockResp.status(), userBLockBody).toBe(403);
      expect(userBLockBody).not.toContain(runId);

      const afterDeniedLockResp = await authedGet(
        page,
        userAToken,
        `/runs/${encodeURIComponent(runId)}`,
      );
      expect(afterDeniedLockResp.status(), await afterDeniedLockResp.text()).toBe(200);
      expect(((await afterDeniedLockResp.json()) as RunResponse).locked).toBe(false);

      const ownerLockResp = await authedPut(
        page,
        userAToken,
        `/runs/${encodeURIComponent(runId)}/lock`,
        { locked: true },
      );
      expect(ownerLockResp.status(), await ownerLockResp.text()).toBe(200);
      const ownerLocked = (await ownerLockResp.json()) as RunResponse;
      expect(ownerLocked.created_by).toBe(USER_A);
      expect(ownerLocked.locked).toBe(true);

      const userBUnlockResp = await authedPut(
        page,
        userBToken,
        `/runs/${encodeURIComponent(runId)}/lock`,
        { locked: false },
      );
      const userBUnlockBody = await userBUnlockResp.text();
      expect(userBUnlockResp.status(), userBUnlockBody).toBe(403);
      expect(userBUnlockBody).not.toContain(runId);

      const stillLockedResp = await authedGet(
        page,
        userAToken,
        `/runs/${encodeURIComponent(runId)}`,
      );
      expect(stillLockedResp.status(), await stillLockedResp.text()).toBe(200);
      expect(((await stillLockedResp.json()) as RunResponse).locked).toBe(true);

      const adminUnlockResp = await authedPut(
        page,
        adminToken,
        `/runs/${encodeURIComponent(runId)}/lock`,
        { locked: false },
      );
      expect(adminUnlockResp.status(), await adminUnlockResp.text()).toBe(200);
      expect(((await adminUnlockResp.json()) as RunResponse).locked).toBe(false);

      const userBCancelResp = await authedPost(
        page,
        userBToken,
        `/runs/${encodeURIComponent(runId)}/cancel`,
        {},
      );
      const userBCancelBody = await userBCancelResp.text();
      expect(userBCancelResp.status(), userBCancelBody).toBe(403);
      expect(userBCancelBody).not.toContain(runId);

      const ownerCancelResp = await authedPost(
        page,
        userAToken,
        `/runs/${encodeURIComponent(runId)}/cancel`,
        {},
      );
      expect(ownerCancelResp.status(), await ownerCancelResp.text()).toBe(409);

      const adminCancelResp = await authedPost(
        page,
        adminToken,
        `/runs/${encodeURIComponent(runId)}/cancel`,
        {},
      );
      expect(adminCancelResp.status(), await adminCancelResp.text()).toBe(409);
    } finally {
      if (runId) {
        await authedPut(page, adminToken, `/runs/${encodeURIComponent(runId)}/lock`, {
          locked: false,
        }).catch(() => {});
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(runId)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-2.14 /tests/{suite}/pull|prepare 遵守 suite owner/admin 边界', async ({
    page,
  }) => {
    const suiteName = `r2_suite_lifecycle_${Date.now()}`;
    let linked = false;

    try {
      const ownerLinkResp = await authedPost(page, userAToken, '/tests/link', {
        path: `/tmp/${suiteName}`,
      });
      expect(ownerLinkResp.status(), await ownerLinkResp.text()).toBe(200);
      const ownerLink = (await ownerLinkResp.json()) as LinkSuiteResponse;
      linked = true;
      expect(ownerLink.success).toBe(true);
      expect(ownerLink.suite_name).toBe(suiteName);

      const userBPullResp = await authedPost(
        page,
        userBToken,
        `/tests/${encodeURIComponent(suiteName)}/pull`,
        {},
      );
      const userBPullBody = await userBPullResp.text();
      expect(userBPullResp.status(), userBPullBody).toBe(403);
      expect(userBPullBody).not.toContain(suiteName);

      const userBPrepareResp = await authedPost(
        page,
        userBToken,
        `/tests/${encodeURIComponent(suiteName)}/prepare`,
        {},
      );
      const userBPrepareBody = await userBPrepareResp.text();
      expect(userBPrepareResp.status(), userBPrepareBody).toBe(403);
      expect(userBPrepareBody).not.toContain(suiteName);

      const ownerPullResp = await authedPost(
        page,
        userAToken,
        `/tests/${encodeURIComponent(suiteName)}/pull`,
        {},
      );
      const ownerPullBody = await ownerPullResp.text();
      expect(ownerPullResp.status(), ownerPullBody).toBe(400);
      expect(ownerPullBody).toContain('Only git suites');

      const ownerPrepareResp = await authedPost(
        page,
        userAToken,
        `/tests/${encodeURIComponent(suiteName)}/prepare`,
        {},
      );
      expect(ownerPrepareResp.status(), await ownerPrepareResp.text()).toBe(200);
      const ownerPrepare = (await ownerPrepareResp.json()) as LinkSuiteResponse;
      expect(ownerPrepare.success).toBe(true);
      expect(ownerPrepare.suite_name).toBe(suiteName);

      const adminPullResp = await authedPost(
        page,
        adminToken,
        `/tests/${encodeURIComponent(suiteName)}/pull`,
        {},
      );
      const adminPullBody = await adminPullResp.text();
      expect(adminPullResp.status(), adminPullBody).toBe(400);
      expect(adminPullBody).toContain('Only git suites');

      const adminPrepareResp = await authedPost(
        page,
        adminToken,
        `/tests/${encodeURIComponent(suiteName)}/prepare`,
        {},
      );
      expect(adminPrepareResp.status(), await adminPrepareResp.text()).toBe(200);
      const adminPrepare = (await adminPrepareResp.json()) as LinkSuiteResponse;
      expect(adminPrepare.success).toBe(true);
      expect(adminPrepare.suite_name).toBe(suiteName);
    } finally {
      if (linked) {
        await authedDelete(page, adminToken, `/tests/${encodeURIComponent(suiteName)}`).catch(
          () => {},
        );
      }
    }
  });

  test('R-API-2.15 GET /runs/{run} 遵守 owner/admin 边界', async ({ page }) => {
    test.setTimeout(120_000);

    let runId: string | undefined;
    const runEnvSecret = `r2_run_detail_env_secret_${Date.now()}`;

    const waitTerminal = async (token: string, id: string) => {
      await expect(async () => {
        const detailResp = await authedGet(page, token, `/runs/${encodeURIComponent(id)}`);
        expect(detailResp.status(), await detailResp.text()).toBe(200);
        const detail = (await detailResp.json()) as RunResponse;
        expect(['completed', 'failed', 'timeout', 'interrupted']).toContain(detail.status);
      }).toPass({ timeout: 60_000, intervals: [1_000] });
    };

    try {
      const createResp = await authedPost(page, userAToken, '/runs', {
        tests_path: PYTEST_SUITE,
        runner: 'pytest',
        args: [],
        allure: false,
        timeout: 60,
        selected_files: [],
        selected_markers: [],
        extra_args: '',
        env: { RUN_DETAIL_SECRET: runEnvSecret },
      });
      expect(createResp.status(), await createResp.text()).toBe(202);
      const created = (await createResp.json()) as RunResponse;
      runId = created.id;
      expect(created.created_by).toBe(USER_A);

      const userBGetResp = await authedGet(page, userBToken, `/runs/${encodeURIComponent(runId)}`);
      const userBGetBody = await userBGetResp.text();
      expect(userBGetResp.status(), userBGetBody).toBe(403);
      expect(userBGetBody).not.toContain(runId);
      expect(userBGetBody).not.toContain(runEnvSecret);

      const ownerGetResp = await authedGet(page, userAToken, `/runs/${encodeURIComponent(runId)}`);
      expect(ownerGetResp.status(), await ownerGetResp.text()).toBe(200);
      const ownerRun = (await ownerGetResp.json()) as RunResponse;
      expect(ownerRun.id).toBe(runId);
      expect(ownerRun.created_by).toBe(USER_A);
      expect(ownerRun.env?.RUN_DETAIL_SECRET).toBe(runEnvSecret);

      const adminGetResp = await authedGet(page, adminToken, `/runs/${encodeURIComponent(runId)}`);
      expect(adminGetResp.status(), await adminGetResp.text()).toBe(200);
      const adminRun = (await adminGetResp.json()) as RunResponse;
      expect(adminRun.id).toBe(runId);
      expect(adminRun.created_by).toBe(USER_A);
      expect(adminRun.env?.RUN_DETAIL_SECRET).toBe(runEnvSecret);
    } finally {
      if (runId) {
        await waitTerminal(userAToken, runId);
        await authedDelete(page, userAToken, `/runs/${encodeURIComponent(runId)}`).catch(
          () => {},
        );
      }
    }
  });
});

// ── R-API-3  Admin protection rules ──────────────────────────────────────

test.describe('R-API-3 admin 保护', () => {
  test('R-API-3.1 非管理员 POST /users → 403', async ({ page }) => {
    const deniedUsername = `should_not_exist_${Date.now()}`;
    const deniedPassword = `Denied-User-${Date.now()}!`;
    const resp = await authedPost(page, userAToken, '/users', {
      username: deniedUsername,
      password: deniedPassword,
      role: 'user',
    });
    const body = await resp.text();
    expect(resp.status(), body).toBe(403);
    expect(body).not.toContain(deniedUsername);
    expect(body).not.toContain(deniedPassword);

    const usersResp = await authedGet(page, adminToken, '/users');
    const usersBody = await usersResp.text();
    expect(usersResp.status(), usersBody).toBe(200);
    expect(usersBody).not.toContain(deniedUsername);
    expect(usersBody).not.toContain(deniedPassword);
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
    const retentionDays = 987;
    const resp = await authedPost(
      page,
      userAToken,
      `/runs/cleanup?retention_days=${retentionDays}`,
      {},
    );
    const body = await resp.text();
    expect(resp.status(), body).toBe(403);
    expect(body).not.toContain(String(retentionDays));
  });

  test('R-API-3.5 非管理员 PUT|DELETE /users/{username} → 403 且不改变用户', async ({
    page,
  }) => {
    const putResp = await authedPut(page, userAToken, `/users/${USER_B}`, {
      role: 'admin',
    });
    const putBody = await putResp.text();
    expect(putResp.status(), putBody).toBe(403);
    expect(putBody).not.toContain(USER_B);

    const deleteResp = await authedDelete(page, userAToken, `/users/${USER_B}`);
    const deleteBody = await deleteResp.text();
    expect(deleteResp.status(), deleteBody).toBe(403);
    expect(deleteBody).not.toContain(USER_B);

    const userBMeResp = await authedGet(page, userBToken, '/auth/me');
    expect(userBMeResp.status(), await userBMeResp.text()).toBe(200);
    const userBMe = await userBMeResp.json();
    expect(userBMe.username).toBe(USER_B);
    expect(userBMe.role).toBe('user');
  });

  test('R-API-3.6 管理员 GET /users 不泄露密码字段或测试密码', async ({ page }) => {
    const resp = await authedGet(page, adminToken, '/users');
    const bodyText = await resp.text();
    expect(resp.status(), bodyText).toBe(200);
    expect(bodyText).not.toContain(USER_A_PW);
    expect(bodyText).not.toContain(USER_B_PW);
    expect(bodyText.toLowerCase()).not.toContain('password');
    expect(bodyText.toLowerCase()).not.toContain('hash');

    const users = ((JSON.parse(bodyText) as { users: Array<Record<string, unknown>> }).users ?? []);
    const userA = users.find((user) => user.username === USER_A);
    const userB = users.find((user) => user.username === USER_B);
    expect(userA).toBeTruthy();
    expect(userB).toBeTruthy();
    expect(userA?.role).toBe('user');
    expect(userB?.role).toBe('user');

    for (const user of users) {
      expect(user.username).toBeTruthy();
      expect(user.role).toBeTruthy();
      expect(user.created_at).toBeTruthy();
      expect(Object.keys(user).some((key) => key.toLowerCase().includes('password'))).toBe(false);
      expect(Object.keys(user).some((key) => key.toLowerCase().includes('hash'))).toBe(false);
    }
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
