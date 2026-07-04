// Infrastructure smoke test — validates the shared auth + API helper stack.
//
// Opt-in to the `chromium-authed-admin` project: storageState (admin cookie) is
// injected by Playwright, so this spec does NOT call login() — it goes straight
// to '/' and asserts the dashboard rendered as admin. It then exercises the
// helpers/api.ts CRUD against the real dev backend to prove the full chain:
//   globalSetup persisted admin cookie  ->  browser accepts it  ->  API context
//   built from the same storageState  ->  CRUD round-trips.
//
// Rename caveat: any spec matching `*.authed-admin.spec.ts` runs under the
// authed-admin project. Legacy specs should opt in only after replacing inline
// UI login with storageState-compatible setup.

import { test, expect } from '@playwright/test';
import {
  createUser,
  deleteUser,
  listUsers,
  loginAndGetContext,
} from './helpers/api';
import { E2E_USER } from './fixtures/auth';

test.describe('E2E infrastructure — shared auth + API helpers', () => {
  test('admin storageState logs in without UI and helpers round-trip', async ({ page }) => {
    // F1 — storageState (admin HttpOnly cookie) is accepted by the browser.
    await page.goto('/');
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('profile-role')).toHaveText('admin');

    // F2 — admin gate renders the users button (proves the cookie carried the
    // admin role through /auth/me, not just an anonymous-but-authenticated state).
    await expect(page.getByTestId('open-users-button')).toBeVisible();

    // F3 — admin API context built from the same storageState lists users,
    // including the standing e2e_user that globalSetup ensures.
    const adminCtx = await loginAndGetContext('admin');
    try {
      const users = await listUsers(adminCtx);
      const names = users.map((u) => u.username);
      expect(names).toContain('admin');
      expect(names).toContain(E2E_USER);
    } finally {
      await adminCtx.dispose();
    }

    // F4 — createUser / deleteUser round-trip (exercise the teardown path the
    // journey + role-matrix specs will rely on).
    const tmpName = `smoke_infra_${Date.now()}`;
    const adminCtx2 = await loginAndGetContext('admin');
    try {
      await createUser(adminCtx2, { username: tmpName, password: 'Smoke-Pass-2026!', role: 'user' });
      const after = await listUsers(adminCtx2);
      expect(after.map((u) => u.username)).toContain(tmpName);
      await deleteUser(adminCtx2, tmpName);
      const final = await listUsers(adminCtx2);
      expect(final.map((u) => u.username)).not.toContain(tmpName);
    } finally {
      await adminCtx2.dispose();
    }

    // F5 — non-admin (e2e_user) API context can read an owner-scoped endpoint
    // (/profiles is auth-required but accessible to any logged-in user). Proves
    // loginAndGetContext('user') resolves to the e2e_user cookie and that a
    // non-admin context is NOT silently rejected (it gets 200, the empty list).
    const userCtx = await loginAndGetContext('user');
    try {
      const resp = await userCtx.get('/profiles');
      expect(resp.status()).toBe(200);
      // /profiles returns an array (owner-scoped, possibly empty for e2e_user).
      const data = await resp.json();
      expect(Array.isArray(data)).toBe(true);
    } finally {
      await userCtx.dispose();
    }
  });
});
