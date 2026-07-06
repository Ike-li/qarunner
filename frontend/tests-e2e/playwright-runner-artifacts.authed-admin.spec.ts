import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

import {
  createProfile,
  deleteProfile,
  deleteRun,
  loginAndGetContext,
  pollRunToTerminal,
} from './helpers/api';
import { E2E_USER } from './fixtures/auth';

const SUITE_NAME = 'sample_playwright';

interface RunResponse {
  id: string;
  runner: string;
  status: string;
  created_by: string;
  summary: { total: number; passed: number; failed: number; error: number } | null;
}

interface Artifact {
  path: string;
  size_bytes: number;
  content_type: string;
}

async function triggerPlaywrightRun(
  ctx: APIRequestContext,
  profileId: string,
): Promise<string> {
  const resp = await ctx.post(`/profiles/${encodeURIComponent(profileId)}/trigger`);
  expect(resp.status(), await resp.text()).toBe(202);
  const run = (await resp.json()) as RunResponse;
  expect(run.runner).toBe('playwright');
  return run.id;
}

async function expectDownloadableArtifacts(
  ctx: APIRequestContext,
  runId: string,
): Promise<Artifact[]> {
  const artifactsResp = await ctx.get(`/runs/${encodeURIComponent(runId)}/artifacts`);
  expect(artifactsResp.status(), await artifactsResp.text()).toBe(200);
  const payload = (await artifactsResp.json()) as { artifacts: Artifact[] };
  const artifacts = payload.artifacts;

  expect(artifacts.some((artifact) => artifact.path.endsWith('trace.zip'))).toBe(true);
  expect(artifacts.some((artifact) => artifact.content_type === 'image/png')).toBe(true);
  expect(artifacts.some((artifact) => artifact.content_type === 'video/webm')).toBe(true);

  const trace = artifacts.find((artifact) => artifact.path.endsWith('trace.zip'));
  expect(trace).toBeTruthy();
  const singleResp = await ctx.get(
    `/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(trace!.path)}`,
  );
  expect(singleResp.status(), await singleResp.text()).toBe(200);

  const zipResp = await ctx.get(`/runs/${encodeURIComponent(runId)}/artifacts.zip`);
  expect(zipResp.status(), await zipResp.text()).toBe(200);
  expect(zipResp.headers()['content-type']).toContain('application/zip');

  return artifacts;
}

async function openRunDrawer(page: Page, runId: string): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('execution-records-title')).toBeVisible({ timeout: 10_000 });
  await page.getByTestId(`run-row-${runId}`).click();
  await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 10_000 });
}

test.describe('Playwright runner artifacts', () => {
  test('real Playwright run exposes trace screenshot video downloads in the drawer', async ({
    page,
  }) => {
    test.setTimeout(180_000);

    const adminCtx = await loginAndGetContext('admin');
    let profileId: string | null = null;
    let runId: string | null = null;
    try {
      profileId = await createProfile(adminCtx, {
        name: `PWR artifacts ${Date.now()}`,
        tests_path: SUITE_NAME,
        runner: 'playwright',
      });

      runId = await triggerPlaywrightRun(adminCtx, profileId);
      const status = await pollRunToTerminal(adminCtx, runId, 120_000, 2_000);
      expect(status).toBe('completed');

      const detailResp = await adminCtx.get(`/runs/${encodeURIComponent(runId)}`);
      expect(detailResp.status(), await detailResp.text()).toBe(200);
      const detail = (await detailResp.json()) as RunResponse;
      expect(detail.runner).toBe('playwright');
      expect(detail.summary?.total).toBeGreaterThan(0);
      expect(detail.summary?.failed ?? 0).toBe(0);
      expect(detail.summary?.error ?? 0).toBe(0);

      await expectDownloadableArtifacts(adminCtx, runId);

      await openRunDrawer(page, runId);
      await page.getByTestId('drawer-tab-report').click();

      await expect(page.getByTestId('run-artifacts')).toBeVisible({ timeout: 10_000 });
      await expect(page.getByTestId('run-artifacts-download-all')).toHaveAttribute(
        'href',
        `/runs/${runId}/artifacts.zip`,
      );

      const traceGroup = page.getByTestId('run-artifact-group-trace');
      const screenshotGroup = page.getByTestId('run-artifact-group-screenshot');
      const videoGroup = page.getByTestId('run-artifact-group-video');
      await expect(traceGroup).toContainText('trace.zip');
      await expect(traceGroup).toContainText('npx playwright show-trace');
      await expect(screenshotGroup).toContainText('image/png');
      await expect(videoGroup).toContainText('video/webm');

      const traceDownload = traceGroup.getByRole('link').filter({ hasText: 'trace.zip' }).first();
      await expect(traceDownload).toHaveAttribute('href', /\/runs\/[^/]+\/artifacts\//);
    } finally {
      if (profileId) await deleteProfile(adminCtx, profileId);
      await adminCtx.dispose();
    }
  });

  test('admin drawer exposes artifacts for a user-owned Playwright run', async ({
    page,
  }) => {
    test.setTimeout(180_000);

    const adminCtx = await loginAndGetContext('admin');
    const userCtx = await loginAndGetContext('user');
    let profileId: string | null = null;
    let runId: string | null = null;
    try {
      profileId = await createProfile(userCtx, {
        name: `PWR user artifacts ${Date.now()}`,
        tests_path: SUITE_NAME,
        runner: 'playwright',
      });

      runId = await triggerPlaywrightRun(userCtx, profileId);
      const status = await pollRunToTerminal(userCtx, runId, 120_000, 2_000);
      expect(status).toBe('completed');

      const detailResp = await userCtx.get(`/runs/${encodeURIComponent(runId)}`);
      expect(detailResp.status(), await detailResp.text()).toBe(200);
      const detail = (await detailResp.json()) as RunResponse;
      expect(detail.runner).toBe('playwright');
      expect(detail.created_by).toBe(E2E_USER);

      const artifacts = await expectDownloadableArtifacts(adminCtx, runId);
      const trace = artifacts.find((artifact) => artifact.path.endsWith('trace.zip'));
      expect(trace).toBeTruthy();

      await openRunDrawer(page, runId);
      await page.getByTestId('drawer-tab-report').click();

      await expect(page.getByTestId('run-artifacts')).toBeVisible({ timeout: 10_000 });
      await expect(page.getByTestId('run-artifacts-download-all')).toHaveAttribute(
        'href',
        `/runs/${runId}/artifacts.zip`,
      );

      await expect(page.getByTestId('run-artifact-group-trace')).toContainText('trace.zip');
      await expect(page.getByTestId('run-artifact-group-screenshot')).toContainText('image/png');
      await expect(page.getByTestId('run-artifact-group-video')).toContainText('video/webm');
      await expect(page.getByTestId('run-artifacts-forbidden')).toHaveCount(0);
      await expect(page.getByTestId('run-artifacts-error')).toHaveCount(0);

      const traceDownload = page
        .getByTestId('run-artifact-group-trace')
        .getByRole('link')
        .filter({ hasText: 'trace.zip' })
        .first();
      await expect(traceDownload).toHaveAttribute(
        'href',
        `/runs/${runId}/artifacts/${encodeURIComponent(trace!.path)}`,
      );
    } finally {
      if (runId) {
        await deleteRun(userCtx, runId).catch(() => {});
      }
      if (profileId) {
        await deleteProfile(userCtx, profileId).catch(() => {});
      }
      await userCtx.dispose();
      await adminCtx.dispose();
    }
  });
});
