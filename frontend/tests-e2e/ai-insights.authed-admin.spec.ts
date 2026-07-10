import { test, expect, type Page } from '@playwright/test';

// AI Insights drawer-tab E2E tests — validates the fourth "AI Analysis" tab of
// the run-details SideSheet drawer: the disabled/degraded state (no API key),
// the empty state with its on-demand Generate button, the structured diagnosis
// card (root-cause badge, confidence, evidence, next steps, regression badge),
// Regenerate, and the transport-error state.
//
// The AI diagnosis endpoint (`/runs/{id}/ai-analysis`) is fully mocked so the
// suite is deterministic and needs no real LLM key — the dev backend degrades
// to `enabled:false` without a key, which would otherwise only ever exercise the
// disabled branch. Runs under `chromium-authed-admin` (storageState, no UI login).

// ── Mock data ──────────────────────────────────────────────────────────────

const RUN_FAILED = {
  id: 'run-ai-0001',
  status: 'completed',
  runner: 'pytest',
  created_by: 'admin',
  tests_path: 'suite_integration/',
  args: ['--tb=short'],
  executor_mode: 'docker',
  summary: { total: 10, passed: 9, failed: 1, skipped: 0, error: 0, duration_ms: 5000, pass_rate: 0.9 },
  report: null,
  exit_code: 1,
  error: 'FAILED suite_integration/test_auth.py::test_login - AssertionError: expected 200 got 401',
  passed: false,
  created_at: '2026-07-03T10:00:00Z',
  started_at: '2026-07-03T10:00:01Z',
  finished_at: '2026-07-03T10:00:06Z',
  stdout: 'suite_integration/test_auth.py::test_login FAILED',
  stderr: null,
  cases: [
    {
      suite: 'suite_integration/test_auth.py',
      name: 'test_login',
      status: 'failed',
      duration_ms: 1200,
      message: 'AssertionError: expected 200 got 401',
    },
  ],
  locked: false,
};

const DIAGNOSIS_NEW_FAILURE = {
  category: 'new_failure',
  confidence: 'HIGH',
  summary: 'The login assertion regressed: the endpoint now returns 401 where 200 was expected.',
  evidence: [
    'test_login asserted status 200 but received 401',
    'This case passed in the previous baseline run',
  ],
  is_likely_regression: true,
  next_steps: [
    {
      kind: 'inspect',
      action: 'Compare the auth handler against the last green commit',
      reference: 'suite_integration/test_auth.py::test_login',
    },
    { kind: 'rerun', action: 'Re-run the case in isolation to rule out flakiness', reference: null },
  ],
};

const DIAGNOSIS_TIMEOUT = {
  category: 'timeout',
  confidence: 'MED',
  summary: 'The suite exceeded the step timeout while waiting on a slow fixture.',
  evidence: ['Execution stopped at the 5s timeout boundary'],
  is_likely_regression: false,
  next_steps: [
    { kind: 'config', action: 'Raise the per-test timeout or speed up the fixture', reference: null },
  ],
};

// ── Helpers ────────────────────────────────────────────────────────────────

function shallowRunForList<T extends object>(run: T) {
  return { ...run, stdout: null, stderr: null, cases: [] };
}

async function mockRunsRoute(page: Page, runsArray: unknown[]) {
  await page.route('**/runs', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: runsArray }) });
  });
  await page.route('**/suites', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
  await page.route('**/tests', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(['suite_integration/']) });
  });
}

async function mockRunDetailRoute(page: Page, runId: string, runData: unknown) {
  await page.route(`**/runs/${runId}`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runData) });
    } else {
      await route.fallback();
    }
  });
}

type AiResponse = { status?: number; body?: unknown };

/** Mock the AI analysis endpoint. GET serves the cached diagnosis (drawer load),
 *  POST serves a freshly generated one (Generate / Regenerate). Only the methods
 *  provided are handled; anything else falls through so unrelated calls are not
 *  swallowed. */
async function mockAiRoute(page: Page, runId: string, opts: { get?: AiResponse; post?: AiResponse }) {
  await page.route(`**/runs/${runId}/ai-analysis`, async (route) => {
    const method = route.request().method();
    if (method === 'GET' && opts.get) {
      await route.fulfill({
        status: opts.get.status ?? 200,
        contentType: 'application/json',
        body: JSON.stringify(opts.get.body ?? {}),
      });
    } else if (method === 'POST' && opts.post) {
      await route.fulfill({
        status: opts.post.status ?? 200,
        contentType: 'application/json',
        body: JSON.stringify(opts.post.body ?? {}),
      });
    } else {
      await route.fallback();
    }
  });
}

async function openDashboard(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('profile-username')).toHaveText('admin');
}

async function openDrawer(page: Page) {
  await expect(page.getByTestId('execution-records-title')).toBeVisible({ timeout: 8000 });
  const firstRow = page.locator('[data-testid^="run-row-"]').first();
  await expect(firstRow).toBeVisible({ timeout: 5000 });
  await firstRow.click();
  await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 5000 });
}

/** Open the drawer and switch to the AI Analysis tab. */
async function openAiTab(page: Page) {
  await openDrawer(page);
  await expect(page.getByTestId('drawer-tab-ai')).toBeVisible();
  await page.getByTestId('drawer-tab-ai').click();
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('AI Insights Drawer Tab', () => {
  test.beforeEach(async ({ page }) => {
    await mockRunsRoute(page, [shallowRunForList(RUN_FAILED)]);
    await mockRunDetailRoute(page, RUN_FAILED.id, RUN_FAILED);
    await openDashboard(page);
  });

  // 1. The AI Analysis tab is present alongside the other drawer tabs.
  test('AI Analysis tab is visible in the run-details drawer', async ({ page }) => {
    await mockAiRoute(page, RUN_FAILED.id, { get: { body: { enabled: false, diagnosis: null, detail: null } } });
    await openDrawer(page);

    await expect(page.getByTestId('drawer-tab-logs')).toBeVisible();
    await expect(page.getByTestId('drawer-tab-report')).toBeVisible();
    await expect(page.getByTestId('drawer-tab-diff')).toBeVisible();
    await expect(page.getByTestId('drawer-tab-ai')).toBeVisible();
  });

  // 2. Without an API key the backend degrades — the tab shows a disabled state,
  //    never a broken card or a generate affordance.
  test('Disabled state is shown when AI analysis is not configured', async ({ page }) => {
    await mockAiRoute(page, RUN_FAILED.id, { get: { body: { enabled: false, diagnosis: null, detail: null } } });
    await openAiTab(page);

    await expect(page.getByTestId('ai-disabled')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('ai-disabled')).toContainText(/not configured|未配置/);
    await expect(page.getByTestId('ai-generate')).toHaveCount(0);
    await expect(page.getByTestId('ai-diagnosis')).toHaveCount(0);
  });

  // 3. Enabled but not-yet-analyzed: the empty state offers Generate; clicking it
  //    posts and renders the returned diagnosis card.
  test('Empty state offers Generate which produces a diagnosis card', async ({ page }) => {
    await mockAiRoute(page, RUN_FAILED.id, {
      get: { body: { enabled: true, diagnosis: null, detail: null } },
      post: { body: { enabled: true, diagnosis: DIAGNOSIS_NEW_FAILURE, detail: null } },
    });
    await openAiTab(page);

    await expect(page.getByTestId('ai-empty')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('ai-generate')).toBeVisible();

    await page.getByTestId('ai-generate').click();

    const card = page.getByTestId('ai-diagnosis');
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(card).toContainText(DIAGNOSIS_NEW_FAILURE.summary);
    await expect(page.getByTestId('ai-empty')).toHaveCount(0);
  });

  // 4. A cached diagnosis renders every structured field on load.
  test('Diagnosis card renders category, confidence, evidence, next steps and regression badge', async ({ page }) => {
    await mockAiRoute(page, RUN_FAILED.id, {
      get: { body: { enabled: true, diagnosis: DIAGNOSIS_NEW_FAILURE, detail: null } },
    });
    await openAiTab(page);

    const card = page.getByTestId('ai-diagnosis');
    await expect(card).toBeVisible({ timeout: 5000 });

    // Root-cause category badge (i18n-mapped) + raw confidence value.
    await expect(card).toContainText(/New failure|新增失败/);
    await expect(card).toContainText(/Confidence|置信度/);
    await expect(card).toContainText('HIGH');

    // Likely-regression badge only shows when is_likely_regression is true.
    await expect(card).toContainText(/Likely regression|疑似回归/);

    // Summary, evidence and next-step actions are the model's own text.
    await expect(card).toContainText(DIAGNOSIS_NEW_FAILURE.summary);
    await expect(card).toContainText(DIAGNOSIS_NEW_FAILURE.evidence[0]);
    await expect(card).toContainText(DIAGNOSIS_NEW_FAILURE.evidence[1]);
    await expect(card).toContainText(DIAGNOSIS_NEW_FAILURE.next_steps[0].action);
    // A next step's reference is rendered in parentheses beside the action.
    await expect(card).toContainText('suite_integration/test_auth.py::test_login');

    await expect(page.getByTestId('ai-regenerate')).toBeVisible();
  });

  // 5. An empty state carrying a server detail message (e.g. "no failures to
  //    analyze") explains itself instead of offering a pointless Generate button.
  test('Empty state with a detail message hides the Generate button', async ({ page }) => {
    const detail = 'This run has no failing cases to analyze.';
    await mockAiRoute(page, RUN_FAILED.id, {
      get: { body: { enabled: true, diagnosis: null, detail } },
    });
    await openAiTab(page);

    await expect(page.getByTestId('ai-empty')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('ai-empty')).toContainText(detail);
    await expect(page.getByTestId('ai-generate')).toHaveCount(0);
  });

  // 6. A transport / non-ok response degrades to an error state, not a blank card.
  test('Error state is shown when the analysis endpoint fails', async ({ page }) => {
    await mockAiRoute(page, RUN_FAILED.id, {
      get: { status: 500, body: { detail: 'analysis failed' } },
    });
    await openAiTab(page);

    await expect(page.getByTestId('ai-error')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('ai-error')).toContainText(/Could not load|无法加载/);
    await expect(page.getByTestId('ai-diagnosis')).toHaveCount(0);
  });

  // 7. Regenerate posts again and replaces the visible card with the new result.
  test('Regenerate replaces the diagnosis card with a fresh result', async ({ page }) => {
    await mockAiRoute(page, RUN_FAILED.id, {
      get: { body: { enabled: true, diagnosis: DIAGNOSIS_NEW_FAILURE, detail: null } },
      post: { body: { enabled: true, diagnosis: DIAGNOSIS_TIMEOUT, detail: null } },
    });
    await openAiTab(page);

    const card = page.getByTestId('ai-diagnosis');
    await expect(card).toBeVisible({ timeout: 5000 });
    await expect(card).toContainText(/New failure|新增失败/);
    await expect(card).toContainText(/Likely regression|疑似回归/);

    await page.getByTestId('ai-regenerate').click();

    await expect(card).toContainText(/Timeout|超时/, { timeout: 5000 });
    await expect(card).toContainText(DIAGNOSIS_TIMEOUT.summary);
    // The timeout diagnosis is not a regression — that badge must disappear.
    await expect(card).not.toContainText(/Likely regression|疑似回归/);
  });
});
