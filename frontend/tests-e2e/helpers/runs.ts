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
