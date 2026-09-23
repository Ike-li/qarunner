// Route mocks for specs that need runs on the dashboard without depending on
// whatever the backend database happens to hold (CI starts from an empty one).

import type { Page } from '@playwright/test';

/** Mock GET /runs with *runsArray*, plus /suites and /tests so the sidebar and
 *  trend calls need no backend data either. Non-GET /runs falls through. */
export async function mockRunsRoute(page: Page, runsArray: unknown[]) {
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

/** The list endpoint omits logs and cases; the detail endpoint carries them. */
export function shallowRunForList<T extends object>(run: T) {
  return { ...run, stdout: null, stderr: null, cases: [] };
}

/** Mock GET /runs/{runId} with *runData*. Other methods fall through. */
export async function mockRunDetailRoute(page: Page, runId: string, runData: unknown) {
  await page.route(`**/runs/${runId}`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runData) });
    } else {
      await route.fallback();
    }
  });
}

/** A completed pytest run carrying console output, cases and an HTML report — enough
 *  for the drawer's logs/report tabs and both fullscreen overlays. */
export const RUN_WITH_LOGS_AND_REPORT = {
  id: 'run-full-0001',
  status: 'completed',
  runner: 'pytest',
  created_by: 'admin',
  tests_path: 'suite_integration/',
  args: ['--verbose', '--tb=short'],
  executor_mode: 'docker',
  summary: { total: 10, passed: 8, failed: 1, skipped: 1, error: 0, duration_ms: 5000, pass_rate: 0.8 },
  report: {
    allure_results_dir: '/artifacts/run-full-0001/results',
    allure_report_file: '/artifacts/run-full-0001/results/allure-report/index.html',
    html_generated: true,
  },
  exit_code: 1,
  error: 'FAILED suite_integration/test_auth.py::test_login - AssertionError: expected 200 got 401',
  passed: false,
  created_at: '2026-07-03T10:00:00Z',
  started_at: '2026-07-03T10:00:01Z',
  finished_at: '2026-07-03T10:00:06Z',
  stdout: `============================= test session starts ==============================
platform linux -- Python 3.12.8, pytest-8.3.4, pluggy-1.5.0
rootdir: /workspace/suite_integration
collecting ... collected 10 items

suite_integration/test_auth.py::test_login FAILED
suite_integration/test_auth.py::test_logout PASSED
suite_integration/test_api.py::test_create PASSED
suite_integration/test_api.py::test_update PASSED
suite_integration/test_api.py::test_delete PASSED
suite_integration/test_db.py::test_query PASSED
suite_integration/test_db.py::test_migration PASSED
suite_integration/test_db.py::test_rollback PASSED
suite_integration/test_ui.py::test_render PASSED
suite_integration/test_ui.py::test_interaction FAILED

----------- generated xml report: /workspace/reports/results.xml ------------
/workspace/suite_integration/conftest.py:42: UserWarning: Some fixture is deprecated
========================= 2 failed, 8 passed in 5.00s =========================`,
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

/** Put RUN_WITH_LOGS_AND_REPORT on the dashboard (list + detail), so a spec can open
 *  its drawer and overlays whatever runs the database holds — CI starts from none. */
export async function mockRunWithLogsAndReport(page: Page) {
  await mockRunsRoute(page, [shallowRunForList(RUN_WITH_LOGS_AND_REPORT)]);
  await mockRunDetailRoute(page, RUN_WITH_LOGS_AND_REPORT.id, RUN_WITH_LOGS_AND_REPORT);
}
