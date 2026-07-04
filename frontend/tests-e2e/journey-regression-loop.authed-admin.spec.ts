// Journey 2 — Regression core loop (P0).
//
// The product's route-A soul (see docs/CROSS_RUN_PLAN.md) is cross-run
// comparison. This journey is the ONLY spec that drives the full chain
// end-to-end against the real backend:
//   trigger a run  ->  poll to terminal  ->  open drawer  ->  re-run
//   ->  open diff tab and verify cross-run comparison renders.
//
// Runs under `chromium-authed-admin`: storageState injected, no UI login.
// No mocking — /runs /runs/{id} /runs/{id}/diff all hit the real backend.
// `sample_tests` (examples/sample_tests) has a deliberately-failing case, so a
// real run completes in ~3s with exit_code=1 / passed=False — fast enough for
// E2E and produces real diff data when re-run.

import { test, expect } from '@playwright/test';
import { createProfile, deleteProfile, loginAndGetContext, pollRunToTerminal } from './helpers/api';

const SUITE = 'sample_tests';

/** Trigger a run against `SUITE` through the UI trigger modal, returning the
 *  new run id (read from the POST /runs response the modal issues). The modal's
 *  testsPath state starts empty, so we must select the suite in the dropdown
 *  before submit — submitting with an empty testsPath is a real UX guard that
 *  keeps the modal open with a form error. */
async function triggerRunViaUi(page: import('@playwright/test').Page): Promise<string> {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
  await page.getByTestId('open-trigger-button').click();
  await expect(page.getByTestId('trigger-modal')).toBeVisible();

  // Select SUITE in the tests-path dropdown via keyboard (reliable Semi Select).
  const testsSelect = page.locator('#trigger-tests-path');
  await testsSelect.click();
  await testsSelect.press('ArrowDown');
  await testsSelect.press('Enter');

  // Wait for fetchSuiteMetadata to finish its /tests/{suite}/tree + /markers calls
  // (proves React state updated → submit button enabled, not disabled by empty
  // testsPath guard).  We listen for either API response; both fire after the
  // selection onChange triggers the useEffect in useSuites.
  await page.waitForResponse(
    (r) => r.url().includes(`/tests/${encodeURIComponent(SUITE)}`),
    { timeout: 10000 },
  ).catch(() => {});  // best-effort: proceed even if response already passed

  // Capture the POST /runs response (carries the new run id).
  // The submit button must be enabled now that testsPath is set.
  const [postResp] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/runs') && r.request().method() === 'POST', { timeout: 15000 }),
    page.getByTestId('trigger-submit-button').click(),
  ]);
  expect(postResp.ok(), 'POST /runs should succeed').toBe(true);
  const newRun = (await postResp.json()) as { id: string };

  // Modal closes on a successful trigger.
  await expect(page.getByTestId('trigger-modal')).not.toBeVisible({ timeout: 10000 });
  return newRun.id;
}

test.describe('Journey 2 — regression core loop', () => {
  // Profile is reused across both run triggers so the second run shares the
  // same (tests_path, runner, args) scope and becomes a valid baseline for the
  // first run's diff.
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

  test('J2.1 trigger a run and watch it flow to a terminal status', async ({ page }) => {
    // This test waits for a real run to complete (sample_tests ≈3-10s) plus
    // multiple page navigations.  Default 30s test timeout is too short.
    test.setTimeout(120_000); // 2 min: run execution + polling + drawer nav
    const adminCtx = await loginAndGetContext('admin');
    try {
      profileId = await createProfile(adminCtx, {
        name: 'J2 regression-loop',
        tests_path: SUITE,
        runner: 'pytest',
      });
    } finally {
      await adminCtx.dispose();
    }

    const runId = await triggerRunViaUi(page);

    // Poll the run to a terminal status via the API (faster than waiting on
    // the table, which only refreshes on a DB poll interval).
    const adminCtx2 = await loginAndGetContext('admin');
    try {
      const status = await pollRunToTerminal(adminCtx2, runId, 60_000);
      // sample_tests has a failing case → terminal status is `failed`, not
      // `completed`. Both are valid terminal states; assert terminal-ness only.
      expect(['completed', 'failed', 'timeout']).toContain(status);
    } finally {
      await adminCtx2.dispose();
    }

    // Open the run's drawer from the table. The table truncates run ids to 8
    // chars in a <code> cell; locate the row by that prefix and click it.
    const rowPrefix = runId.slice(0, 8);
    await page.goto('/');
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await page.locator('code', { hasText: rowPrefix }).first().click();
    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('drawer-tab-diff')).toBeVisible();
  });

  test('J2.2 re-run produces a baseline and the diff tab shows comparison', async ({ page }) => {
    // Reuse the run from J2.1: re-trigger via the API rerun endpoint to get a
    // second run sharing the same scope, then open the diff tab in the UI.
    const adminCtx = await loginAndGetContext('admin');
    let runBId: string | null = null;
    try {
      const resp = await adminCtx.get('/runs');
      const data = (await resp.json()) as { runs: Array<{ id: string; tests_path: string }> };
      const first = data.runs.find((r) => r.tests_path === SUITE);
      expect(first, 'J2.1 should have left a sample_tests run').toBeTruthy();

      // Rerun uses the original run's params → same scope → valid baseline.
      const rerunResp = await adminCtx.post(`/runs/${first!.id}/rerun`, { maxRedirects: 0 });
      expect(rerunResp.status()).toBe(202);
      const rerunData = (await rerunResp.json()) as { id: string };
      runBId = rerunData.id;

      const status = await pollRunToTerminal(adminCtx, runBId, 60_000);
      expect(['completed', 'failed', 'timeout']).toContain(status);
    } finally {
      await adminCtx.dispose();
    }

    // UI: open run B's drawer and the diff tab.
    const rowPrefix = runBId!.slice(0, 8);
    await page.goto('/');
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
    await page.locator('code', { hasText: rowPrefix }).first().click();
    await expect(page.getByTestId('drawer-tab-diff')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('drawer-tab-diff').click();

    // With a baseline present, the diff should render either buckets OR, if
    // both runs had identical outcomes, an empty-but-resolved state. Assert
    // that the "no baseline" placeholder is NOT shown — that would mean the
    // earlier run wasn't picked as baseline (the regression-loop core promise).
    await expect(page.getByTestId('diff-empty-baseline')).not.toBeVisible({ timeout: 10000 });
  });
});
