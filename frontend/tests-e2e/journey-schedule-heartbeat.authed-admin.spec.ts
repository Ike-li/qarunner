// Journey 4 — Schedule-driven regression heartbeat (P1).
//
// Validates the schedule lifecycle that underpins automated regression:
//   create profile (API) → create schedule (API) → trigger schedule (API)
//   → poll run to terminal → verify run appears in UI table.
//
// The UI portion focuses on verifying the triggered run lands correctly;
// schedule CRUD via API is stable and doesn't depend on sidebar button
// visibility (which requires a profile-linked suite).

import { test, expect } from '@playwright/test';
import {
  createProfile,
  createSchedule,
  deleteProfile,
  deleteSchedule,
  loginAndGetContext,
  pollRunToTerminal,
} from './helpers/api';

const SUITE = 'sample_tests';

test.describe('Journey 4 — schedule-driven heartbeat', () => {
  let profileId: string | null = null;

  test.afterAll(async () => {
    if (!profileId) return;
    const adminCtx = await loginAndGetContext('admin');
    try {
      await deleteProfile(adminCtx, profileId);
    } finally {
      await adminCtx.dispose();
    }
  });

  test('J4.1 create+trigger schedule via API, verify run reaches terminal and shows in UI', async ({
    page,
  }) => {
    // ── Step 1: Create a profile for the schedule to reference ──
    const adminCtx = await loginAndGetContext('admin');
    try {
      profileId = await createProfile(adminCtx, {
        name: 'J4 schedule heartbeat',
        tests_path: SUITE,
        runner: 'pytest',
      });
    } finally {
      await adminCtx.dispose();
    }

    // ── Step 2: Create a schedule via API (stable, no UI gate dependencies) ──
    const ctx = await loginAndGetContext('admin');
    try {
      const scheduleId = await createSchedule(ctx, {
        name: `J4_heartbeat_${Date.now()}`,
        profile_id: profileId!,
        cron_expression: '0 2 * * *', // daily at 02:00 UTC
        enabled: true,
      });
      expect(scheduleId).toBeTruthy();

      // ── Step 3: Trigger the schedule ──
      const trigResp = await ctx.post(`/schedules/${scheduleId}/trigger`, {
        maxRedirects: 0,
      });
      expect(trigResp.status()).toBe(202);
      const { id: runId } = (await trigResp.json()) as { id: string };

      // ── Step 4: Poll run to terminal ──
      const status = await pollRunToTerminal(ctx, runId, 60_000);
      expect(['completed', 'failed', 'timeout']).toContain(status);

      // ── Step 5: Verify the run appears in the UI RunsTable ──
      await page.goto('/');
      await expect(page.getByTestId('execution-records-title')).toBeVisible({
        timeout: 10000,
      });
      const rowPrefix = runId.slice(0, 8);
      await expect(page.locator('code', { hasText: rowPrefix })).toBeVisible({
        timeout: 15000,
      });

      // ── Cleanup: delete the schedule ──
      await deleteSchedule(ctx, scheduleId);
    } finally {
      await ctx.dispose();
    }
  });
});
