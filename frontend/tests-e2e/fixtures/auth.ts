// Shared auth constants + programmatic login for the E2E suite.
//
// Single source of truth for credentials used by the authed-* Playwright projects.
// Project rule (CLAUDE.md §1.3 / §4): reuse, do not duplicate. The prior state had
// 21 specs eachInlining `login()` + a `ADMIN_PASSWORD` default that disagreed
// ('admin123' vs 'Demo-qarunner-2026!'); the backend SEC-2 validator
// (src/qarunner/config.py:_reject_placeholder_admin_password) rejects weak defaults
// like `admin`, so a CI run that fell back to `admin123` would already fail at login.
// We refuse silently — there is NO default password here: unset env = hard throw.

import { request } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname_estar = path.dirname(fileURLToPath(import.meta.url));

/** Admin username always present (backend seeds admin in sqlite_store.initialize). */
export const E2E_ADMIN = 'admin';

/** Non-admin user created + reused by globalSetup as a standing test account. */
export const E2E_USER = 'e2e_user';

/** Directory holding the generated storageState (HttpOnly cookie) files. */
export const AUTH_DIR = path.join(__dirname_estar, '..', '.auth');

export const adminAuthFile = path.join(AUTH_DIR, 'admin.json');
export const userAuthFile = path.join(AUTH_DIR, 'user.json');

/**
 * Admin password. NO default string — unset env throws. Callers must surface the
 * error (global-setup re-throws with a remediation hint).
 */
export const E2E_ADMIN_PASSWORD = requireAdminPassword();

/** User password. Has a default (the e2e_user account is created by us, not
 *  constrained by SEC-2 since that validator only guards the bootstrap admin).
 *  Override via env if your environment forbids the default. */
export const E2E_USER_PASSWORD = process.env.E2E_USER_PASSWORD || 'E2e-User-2026!';

function requireAdminPassword(): string {
  const pw = process.env.E2E_ADMIN_PASSWORD;
  if (!pw) {
    throw new Error(
      'E2E_ADMIN_PASSWORD is not set. Set it to the backend QARUNNER_ADMIN_PASSWORD ' +
        'value, e.g. E2E_ADMIN_PASSWORD=\'Demo-Qarunner-2026!\'. The backend SEC-2 ' +
        'validator rejects weak defaults like `admin`, so there is no fallback here.',
    );
  }
  return pw;
}

/** Base URL for programmatic API requests. Goes through the Vite dev proxy
 *  (http://localhost:5173) so auth cookies share the same origin as browser
 *  specs — avoiding cross-port cookie-domain pitfalls. */
const API_BASE_URL = process.env.BASE_URL || 'http://localhost:5173';

export interface LoginResult {
  /** Bearer token (also sent as the `token` HttpOnly cookie). */
  accessToken: string;
  /** Raw Set-Cookie header from /auth/login, for manual injection if needed. */
  setCookie: string | null;
}

/**
 * Programmatic login against POST /auth/login. Returns the access token + raw
 * Set-Cookie. Used by global-setup to persist storageState; does not touch a page.
 *
 * Throws on non-200 with the rendered error so failures point at the cause
 * (wrong password / locked-out / backend down).
 */
export async function loginProgrammatically(
  username: string,
  password: string,
): Promise<LoginResult> {
  const ctx = await request.newContext({ baseURL: API_BASE_URL });
  try {
    const resp = await ctx.post('/auth/login', {
      data: { username, password },
      maxRedirects: 0,
    });
    if (!resp.ok()) {
      const body = await resp.text().catch(() => '');
      throw new Error(
        `loginProgrammatically: /auth/login for '${username}' returned ${resp.status()}` +
          (body ? `: ${body}` : ''),
      );
    }
    const data = (await resp.json()) as { access_token: string; token_type: string };
    const setCookie = resp.headers()['set-cookie'] ?? null;
    return { accessToken: data.access_token, setCookie };
  } finally {
    await ctx.dispose();
  }
}

/** Ensure the .auth directory exists (storageState write requires it). */
export function ensureAuthDir(): void {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
}
