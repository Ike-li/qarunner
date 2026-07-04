import { defineConfig, devices } from '@playwright/test';

/**
 * See https://playwright.dev/docs/test-configuration.
 *
 * Projects:
 *   - chromium               : plain UI login (no storageState). Existing specs
 *                               live here, totally unchanged — globalSetup does
 *                               not affect them.
 *   - chromium-authed-admin   : storageState injected from global-setup, matches
 *                               only `*.authed-admin.spec.ts`.
 *   - chromium-authed-user    : storageState injected from global-setup, matches
 *                               only `*.authed-user.spec.ts`.
 *
 * The two authed projects only run specs opt-in via their filename suffix, so
 * the existing suite is untouched. globalSetup runs once before any project.
 */

export default defineConfig({
  testDir: './tests-e2e',
  globalSetup: './tests-e2e/global-setup.ts',

  /* Run tests in files in parallel */
  fullyParallel: false,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on local machines to avoid DB race conditions. */
  workers: 1,
  /* Reporter to use. See https://playwright.dev/docs/api/class-testreporters */
  reporter: 'html',
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: process.env.BASE_URL || 'http://localhost:5173',

    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      // Default project: existing specs with their own UI login. No storageState —
      // globalSetup's persisted cookies do NOT leak into these (no deps on auth files).
      name: 'chromium',
      testMatch: /.*\.spec\.ts/,
      testIgnore: /.*\.authed-(admin|user)\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome', // Use the host system's Google Chrome installation
      },
    },
    {
      name: 'chromium-authed-admin',
      testMatch: /.*\.authed-admin\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        storageState: './tests-e2e/.auth/admin.json',
      },
    },
    {
      name: 'chromium-authed-user',
      testMatch: /.*\.authed-user\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        storageState: './tests-e2e/.auth/user.json',
      },
    },
  ],
});
