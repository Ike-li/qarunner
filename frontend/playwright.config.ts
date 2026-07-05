import { defineConfig, devices } from '@playwright/test';

declare const process: {
  env: Record<string, string | undefined>;
};

const useBundledChromium = process.env.PLAYWRIGHT_USE_BUNDLED_CHROMIUM === 'true';
const desktopChrome = {
  ...devices['Desktop Chrome'],
  ...(useBundledChromium ? {} : { channel: 'chrome' as const }),
};
const unauthenticatedChromiumSpecs =
  /.*\/(login-auth|login-smoke|role-anonymous)\.spec\.ts/;

/**
 * See https://playwright.dev/docs/test-configuration.
 *
 * Projects:
 *   - chromium               : explicitly allowlisted anonymous + login-flow specs
 *                               (no storageState). globalSetup does not affect
 *                               them.
 *   - chromium-authed-admin   : storageState injected from global-setup, matches
 *                               only `*.authed-admin.spec.ts`.
 *   - chromium-authed-user    : storageState injected from global-setup, matches
 *                               only `*.authed-user.spec.ts`.
 *
 * The two authed projects only run specs opt-in via their filename suffix.
 * globalSetup runs once before any project.
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
      // Default project: anonymous specs and login-flow specs that must run without storageState.
      // No storageState — globalSetup's persisted cookies do NOT leak into these.
      name: 'chromium',
      testMatch: unauthenticatedChromiumSpecs,
      use: desktopChrome,
    },
    {
      name: 'chromium-authed-admin',
      testMatch: /.*\.authed-admin\.spec\.ts/,
      use: {
        ...desktopChrome,
        storageState: './tests-e2e/.auth/admin.json',
      },
    },
    {
      name: 'chromium-authed-user',
      testMatch: /.*\.authed-user\.spec\.ts/,
      use: {
        ...desktopChrome,
        storageState: './tests-e2e/.auth/user.json',
      },
    },
  ],
});
