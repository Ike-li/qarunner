// Journey 5 — Local suite onboard & first run (P1).
//
// Covers: link a local test directory as a suite via the UI → it appears in the
// sidebar → trigger its first run from the sidebar → the run completes.
//
// The suite is a throwaway fixture this spec writes itself, under a hidden
// directory of the backend's tests_root: hidden entries are never listed as
// suites, so only the link makes it one. It needs a unique basename — /tests/link
// names the suite after it and replaces whatever entry already has that name.
//
// Paths default to the dev compose layout: tests_root is ./external_tests, mounted
// at /app/external_tests in the backend, and the documented E2E command mounts the
// repo so this runner reaches it at ../external_tests. Override both with
// E2E_TESTS_ROOT_LOCAL / E2E_TESTS_ROOT_BACKEND for any other layout.

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import {
  deleteSuite,
  loginAndGetContext,
  pollRunToTerminal,
} from './helpers/api';

const here = path.dirname(fileURLToPath(import.meta.url));
const TESTS_ROOT_LOCAL =
  process.env.E2E_TESTS_ROOT_LOCAL ?? path.resolve(here, '..', '..', 'external_tests');
const TESTS_ROOT_BACKEND = process.env.E2E_TESTS_ROOT_BACKEND ?? '/app/external_tests';
const FIXTURES_DIR = '.e2e-fixtures';

test.describe('Journey 5 — suite onboard and first run', () => {
  const suiteName = `e2e_j5_${Date.now()}`;
  const fixtureDir = path.join(TESTS_ROOT_LOCAL, FIXTURES_DIR, suiteName);

  test.beforeAll(() => {
    fs.mkdirSync(fixtureDir, { recursive: true });
    fs.writeFileSync(
      path.join(fixtureDir, 'test_j5.py'),
      'def test_linked_suite_runs():\n    assert 1 + 1 == 2\n',
    );
  });

  test.afterAll(async () => {
    const adminCtx = await loginAndGetContext('admin');
    try {
      // Unlinks the suite's symlink and drops its record; the fixture goes next.
      await deleteSuite(adminCtx, suiteName);
    } finally {
      await adminCtx.dispose();
      fs.rmSync(fixtureDir, { recursive: true, force: true });
    }
  });

  test('J5.1 link a local suite via UI and run it from the sidebar', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('profile-username')).toHaveText('admin');

    // ── Step 1: link the fixture directory through the Add Suite modal ──
    await page.getByTestId('open-add-suite-button').click();
    await expect(page.getByTestId('link-path-input')).toBeVisible();
    await page
      .getByTestId('link-path-input')
      .fill(`${TESTS_ROOT_BACKEND}/${FIXTURES_DIR}/${suiteName}`);

    const [linkResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith('/tests/link') && r.request().method() === 'POST',
      ),
      page.getByTestId('link-submit').click(),
    ]);
    expect(linkResp.status(), await linkResp.text()).toBe(200);
    expect(await linkResp.json()).toMatchObject({
      success: true,
      suite_name: suiteName,
      is_accessible: true,
    });
    await page.getByTestId('add-suite-modal-close').click();

    // ── Step 2: the new suite shows up in the sidebar ──
    await expect(page.getByTestId(`suite-filter-${suiteName}`)).toBeVisible();

    // ── Step 3: trigger its first run via the sidebar quick-trigger ──
    await page.getByTestId(`suite-quick-trigger-${suiteName}`).click();
    await expect(page.getByTestId('trigger-modal')).toBeVisible();

    const [postResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith('/runs') && r.request().method() === 'POST',
      ),
      page.getByTestId('trigger-submit-button').click(),
    ]);
    expect(postResp.status(), await postResp.text()).toBe(202);
    const newRun = (await postResp.json()) as { id: string; tests_path: string };
    expect(newRun.tests_path).toBe(suiteName);
    await expect(page.getByTestId('trigger-modal')).not.toBeVisible();

    // ── Step 4: the run executes the linked tests and completes ──
    const adminCtx = await loginAndGetContext('admin');
    try {
      expect(await pollRunToTerminal(adminCtx, newRun.id, 120_000)).toBe('completed');
    } finally {
      await adminCtx.dispose();
    }

    // ── Step 5: the run row appears in the table ──
    await page.goto('/');
    await expect(page.getByTestId(`run-id-${newRun.id}`)).toBeVisible({ timeout: 15_000 });
  });
});
