// Shared API helpers for the E2E suite: data setup + teardown + run polling.
//
// Replaces the scattered inline `getAuthToken`/`createProfile`/`createSchedule`
// copies that used to live in schedule*.spec.ts before the authed project migration
// (project rule CLAUDE.md §1.3 — no parallel implementations).
//
// All requests go through the Vite dev proxy (BASE_URL, default 5173), sharing the
// same origin / cookie-domain as browser specs. loginAndGetContext persists the
// token cookie into the returned context so subsequent calls are authenticated.

import { request, type APIRequestContext } from '@playwright/test';
import { existsSync } from 'fs';
import {
  E2E_ADMIN,
  E2E_ADMIN_PASSWORD,
  E2E_USER,
  E2E_USER_PASSWORD,
  adminAuthFile,
  userAuthFile,
} from '../fixtures/auth';

const API_BASE_URL = process.env.BASE_URL || 'http://localhost:5173';

export type Role = 'admin' | 'user';

/**
 * Returns an authenticated APIRequestContext for the given role.
 * Credentials come from fixtures/auth (no inline passwords). For 'admin'/'user'
 * the context is rebuilt from the corresponding storageState file produced by
 * global-setup; if that file is missing we fall back to a live programmatic login
 * so ad-hoc `playwright test` runs without globalSetup still work.
 */
export async function loginAndGetContext(
  role: Role,
): Promise<APIRequestContext> {
  const storageState = role === 'admin' ? adminAuthFile : userAuthFile;
  const username = role === 'admin' ? E2E_ADMIN : E2E_USER;
  const password = role === 'admin' ? E2E_ADMIN_PASSWORD : E2E_USER_PASSWORD;
  const authFile = role === 'admin' ? adminAuthFile : userAuthFile;

  if (existsSync(authFile)) {
    return await request.newContext({ baseURL: API_BASE_URL, storageState: authFile });
  }
  const ctx = await request.newContext({ baseURL: API_BASE_URL });
  const resp = await ctx.post('/auth/login', { data: { username, password } });
  if (!resp.ok()) {
    throw new Error(`loginAndGetContext(${role}): /auth/login failed with ${resp.status()}`);
  }
  return ctx;
}

// --- Users -----------------------------------------------------------------

export interface CreateUserInput {
  username: string;
  password: string;
  role?: 'admin' | 'user';
}

export async function createUser(
  adminCtx: APIRequestContext,
  input: CreateUserInput,
): Promise<{ username: string; role: string }> {
  const resp = await adminCtx.post('/users', {
    data: { username: input.username, password: input.password, role: input.role ?? 'user' },
  });
  if (!resp.ok()) {
    throw new Error(`createUser(${input.username}) failed: ${resp.status()} ${await resp.text()}`);
  }
  return await resp.json();
}

export async function deleteUser(
  adminCtx: APIRequestContext,
  username: string,
): Promise<void> {
  const resp = await adminCtx.delete(`/users/${encodeURIComponent(username)}`);
  if (!resp.ok() && resp.status() !== 404) {
    throw new Error(`deleteUser(${username}) failed: ${resp.status()} ${await resp.text()}`);
  }
}

export async function listUsers(
  adminCtx: APIRequestContext,
): Promise<Array<{ username: string; role: string }>> {
  const resp = await adminCtx.get('/users');
  if (!resp.ok()) {
    throw new Error(`listUsers failed: ${resp.status()} ${await resp.text()}`);
  }
  const data = (await resp.json()) as { users: Array<{ username: string; role: string }> };
  return data.users ?? [];
}

// --- Profiles --------------------------------------------------------------

export interface CreateProfileInput {
  name: string;
  tests_path: string;
  runner?: 'pytest' | 'playwright';
}

export async function createProfile(
  ctx: APIRequestContext,
  input: CreateProfileInput,
): Promise<string> {
  const resp = await ctx.post('/profiles', {
    data: {
      name: input.name,
      tests_path: input.tests_path,
      runner: input.runner ?? 'pytest',
    },
  });
  if (!resp.ok()) {
    throw new Error(`createProfile(${input.name}) failed: ${resp.status()} ${await resp.text()}`);
  }
  const data = (await resp.json()) as { id: string };
  return data.id;
}

export async function deleteProfile(ctx: APIRequestContext, profileId: string): Promise<void> {
  const resp = await ctx.delete(`/profiles/${encodeURIComponent(profileId)}`);
  if (!resp.ok() && resp.status() !== 404) {
    throw new Error(`deleteProfile(${profileId}) failed: ${resp.status()} ${await resp.text()}`);
  }
}

// --- Schedules -------------------------------------------------------------

export interface CreateScheduleInput {
  name: string;
  profile_id: string;
  cron_expression?: string;
  timezone?: string;
  enabled?: boolean;
}

export async function createSchedule(
  ctx: APIRequestContext,
  input: CreateScheduleInput,
): Promise<string> {
  const resp = await ctx.post('/schedules', {
    data: {
      name: input.name,
      profile_id: input.profile_id,
      cron_expression: input.cron_expression ?? '0 2 * * *',
      timezone: input.timezone ?? 'UTC',
      enabled: input.enabled ?? true,
    },
  });
  if (!resp.ok()) {
    throw new Error(`createSchedule(${input.name}) failed: ${resp.status()} ${await resp.text()}`);
  }
  const data = (await resp.json()) as { id: string };
  return data.id;
}

export async function deleteSchedule(ctx: APIRequestContext, scheduleId: string): Promise<void> {
  const resp = await ctx.delete(`/schedules/${encodeURIComponent(scheduleId)}`);
  if (!resp.ok() && resp.status() !== 404) {
    throw new Error(`deleteSchedule(${scheduleId}) failed: ${resp.status()} ${await resp.text()}`);
  }
}

// --- Suites ----------------------------------------------------------------

export async function linkSuite(ctx: APIRequestContext, suitePath: string): Promise<void> {
  const resp = await ctx.post('/tests/link', { data: { path: suitePath } });
  if (!resp.ok()) {
    throw new Error(`linkSuite(${suitePath}) failed: ${resp.status()} ${await resp.text()}`);
  }
}

export async function deleteSuite(ctx: APIRequestContext, suite: string): Promise<void> {
  const resp = await ctx.delete(`/tests/${encodeURIComponent(suite)}`);
  if (!resp.ok() && resp.status() !== 404) {
    throw new Error(`deleteSuite(${suite}) failed: ${resp.status()} ${await resp.text()}`);
  }
}

// --- Runs ------------------------------------------------------------------

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'timeout' | 'interrupted';

const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set([
  'completed',
  'failed',
  'timeout',
  'interrupted',
]);

/**
 * Poll GET /runs/{id} until the run reaches a terminal status or the timeout
 * elapses. Returns the observed status. Default 60s, 1s interval — keep tests
 * snappy; raise the timeout for slow-runner suites.
 */
export async function pollRunToTerminal(
  ctx: APIRequestContext,
  runId: string,
  timeoutMs = 60_000,
  intervalMs = 1_000,
): Promise<RunStatus> {
  const deadline = Date.now() + timeoutMs;
  let status: RunStatus = 'queued';
  while (Date.now() < deadline) {
    const resp = await ctx.get(`/runs/${encodeURIComponent(runId)}`);
    if (!resp.ok()) {
      throw new Error(`pollRunToTerminal: GET /runs/${runId} -> ${resp.status()}`);
    }
    const data = (await resp.json()) as { status: RunStatus };
    status = data.status;
    if (TERMINAL_STATUSES.has(status)) return status;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return status;
}

export async function deleteRun(ctx: APIRequestContext, runId: string): Promise<void> {
  const resp = await ctx.delete(`/runs/${encodeURIComponent(runId)}`);
  if (!resp.ok() && resp.status() !== 404 && resp.status() !== 409) {
    // 409 = locked or active; caller should unlock/cancel first — surface it.
    throw new Error(`deleteRun(${runId}) failed: ${resp.status()} ${await resp.text()}`);
  }
}
