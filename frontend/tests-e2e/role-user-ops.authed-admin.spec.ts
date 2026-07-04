// Role-matrix: role/password operations actually take effect.
//
// R-API-7.1 toggle-role 后下次登录 role 真变化
// R-API-7.2 改密码后旧密码失效、新密码可用
//
// Runs under `chromium-authed-admin` (storageState = admin cookies).

import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import {
  E2E_ADMIN,
  E2E_ADMIN_PASSWORD,
} from './fixtures/auth';

let adminToken: string;

const TEST_USER = `e2e_ops_${Date.now()}`;
const TEST_PW = 'OpsUser-2026!';
const TEST_PW_NEW = 'OpsUser-New-2026!';

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

// ── Setup & Teardown ─────────────────────────────────────────────────────

test.beforeAll(async ({ request }) => {
  adminToken = await getToken(request, E2E_ADMIN, E2E_ADMIN_PASSWORD);

  // Create test user
  const resp = await request.post('/users', {
    data: { username: TEST_USER, password: TEST_PW, role: 'user' },
    headers: { Authorization: `Bearer ${adminToken}` },
  });
  if (!resp.ok() && resp.status() !== 409) {
    // 409 = already exists (from a prior failed run)
    throw new Error(`Failed to create ${TEST_USER}: ${resp.status()}`);
  }
});

test.afterAll(async ({ request }) => {
  if (adminToken) {
    await request.delete(`/users/${TEST_USER}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    }).catch(() => {});
  }
});

// ── R-API-7.1  toggle-role 真生效 ────────────────────────────────────────

test.describe('R-API-7.1 toggle-role', () => {
  test('admin 改 role 后下次登录 /auth/me 反映新 role', async ({ page }) => {
    // 1) Verify initial role is 'user'
    const me1 = await authedGet(page, await getToken(page.request, TEST_USER, TEST_PW), '/auth/me');
    expect(me1.ok()).toBeTruthy();
    expect((await me1.json()).role).toBe('user');

    // 2) Admin promotes TEST_USER to admin
    const putResp = await authedPut(page, adminToken, `/users/${TEST_USER}`, { role: 'admin' });
    expect(putResp.ok()).toBeTruthy();

    // 3) Re-login and check role
    const newToken = await getToken(page.request, TEST_USER, TEST_PW);
    const me2 = await authedGet(page, newToken, '/auth/me');
    expect(me2.ok()).toBeTruthy();
    expect((await me2.json()).role).toBe('admin');

    // 4) Demote back to user (cleanup)
    const demoteResp = await authedPut(page, adminToken, `/users/${TEST_USER}`, { role: 'user' });
    expect(demoteResp.ok()).toBeTruthy();

    // 5) Verify demotion
    const me3 = await authedGet(page, await getToken(page.request, TEST_USER, TEST_PW), '/auth/me');
    expect((await me3.json()).role).toBe('user');
  });
});

// ── R-API-7.2  change password 真生效 ────────────────────────────────────

test.describe('R-API-7.2 change password', () => {
  test('改密码后旧密码失效、新密码可用', async ({ page }) => {
    // 1) Old password works
    const oldToken = await getToken(page.request, TEST_USER, TEST_PW);
    expect(oldToken).toBeTruthy();

    // 2) Admin changes password
    const putResp = await authedPut(page, adminToken, `/users/${TEST_USER}`, {
      password: TEST_PW_NEW,
    });
    expect(putResp.ok()).toBeTruthy();

    // 3) Old password should fail
    const oldLoginResp = await page.request.post('/auth/login', {
      data: { username: TEST_USER, password: TEST_PW },
    });
    expect(oldLoginResp.status()).toBe(401);

    // 4) New password should work
    const newToken = await getToken(page.request, TEST_USER, TEST_PW_NEW);
    expect(newToken).toBeTruthy();

    // 5) Restore original password (cleanup)
    await authedPut(page, adminToken, `/users/${TEST_USER}`, { password: TEST_PW });
  });
});
