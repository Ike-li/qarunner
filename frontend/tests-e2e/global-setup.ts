// Playwright globalSetup: persists authenticated storageState for the
// `chromium-authed-admin` and `chromium-authed-user` projects.
//
// Side effects on the dev backend (intentional, user-approved):
//   - Ensures the standing `e2e_user` account exists (created if absent, never
//     deleted — reused across runs).
//   - Writes two HttpOnly-cookie storageState files under tests-e2e/.auth/
//     (gitignored — never committed).
//
// Invoked once before any test runs. Existing 21 specs run under the plain
// `chromium` project with NO storageState, so this changes nothing for them.

import { request } from '@playwright/test';
import {
  E2E_ADMIN,
  E2E_ADMIN_PASSWORD,
  E2E_USER,
  E2E_USER_PASSWORD,
  ensureAuthDir,
  adminAuthFile,
  userAuthFile,
} from './fixtures/auth';
import { createUser, listUsers, loginAndGetContext } from './helpers/api';

export default async function globalSetup() {
  // Fail fast with a readable cause (matches fixtures/auth behaviour).
  if (!E2E_ADMIN_PASSWORD) {
    throw new Error(
      'globalSetup: E2E_ADMIN_PASSWORD is unset — set it to the backend ' +
        'QARUNNER_ADMIN_PASSWORD value before running the authed projects.',
    );
  }
  ensureAuthDir();

  // 1) Admin login -> persist cookies + token for chromium-authed-admin.
  await persistAuthedSession(E2E_ADMIN, E2E_ADMIN_PASSWORD, adminAuthFile);

  // 2) Ensure the standing e2e_user account exists (idempotent), then log in
  //    as it and persist for chromium-authed-user.
  await ensureE2EUser();
  await persistAuthedSession(E2E_USER, E2E_USER_PASSWORD, userAuthFile);
}

/** Login via /auth/login and persist the resulting storageState to disk. */
async function persistAuthedSession(username: string, password: string, authFile: string) {
  const ctx = await request.newContext({
    baseURL: process.env.BASE_URL || 'http://localhost:5173',
  });
  try {
    const resp = await ctx.post('/auth/login', { data: { username, password } });
    if (!resp.ok()) {
      const body = await resp.text().catch(() => '');
      throw new Error(
        `globalSetup: admin/user login for '${username}' failed (${resp.status()})` +
          (body ? `: ${body}` : '') +
          ' — verify the dev backend is up on :8000/:8001 and the password matches.',
      );
    }
    await ctx.storageState({ path: authFile });
  } finally {
    await ctx.dispose();
  }
}

/** Create e2e_user if absent (idempotent). Uses an admin-scoped context. */
async function ensureE2EUser() {
  const adminCtx = await loginAndGetContext('admin');
  try {
    const users = await listUsers(adminCtx);
    if (users.some((u) => u.username === E2E_USER)) return;
    await createUser(adminCtx, {
      username: E2E_USER,
      password: E2E_USER_PASSWORD,
      role: 'user',
    });
  } finally {
    await adminCtx.dispose();
  }
}
