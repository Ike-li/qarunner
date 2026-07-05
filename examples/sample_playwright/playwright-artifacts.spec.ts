import { expect, test } from '@playwright/test';

test.use({
  trace: 'on',
  screenshot: 'on',
  video: 'on',
});

test('emits trace screenshot and video artifacts @smoke @artifacts', async ({ page }) => {
  await page.setContent(`
    <main>
      <h1>qarunner Playwright artifact sample</h1>
      <button type="button">Ready</button>
    </main>
  `);

  await expect(page.getByRole('heading', { name: 'qarunner Playwright artifact sample' }))
    .toBeVisible();
  await expect(page.getByRole('button', { name: 'Ready' })).toBeVisible();
});
