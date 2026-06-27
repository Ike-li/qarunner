import { describe, expect, it } from 'vitest'

import { buildProfilePayload } from './profilePayload'

describe('buildProfilePayload', () => {
  it('preserves the selected runner when saving a profile', () => {
    expect(
      buildProfilePayload({
        profileName: ' Daily Playwright ',
        profileDesc: '',
        testsPath: 'my-e2e-suite',
        selectedRunner: 'playwright',
        selectedFiles: ['tests/specs/smoke.spec.ts'],
        selectedMarkers: [],
        customArgs: '--headed',
        executorMode: 'docker',
        timeoutSeconds: 120,
        env: { APP_REPO_PATH: '~/code/my-app' },
      }),
    ).toEqual({
      name: 'Daily Playwright',
      description: null,
      tests_path: 'my-e2e-suite',
      runner: 'playwright',
      selected_files: ['tests/specs/smoke.spec.ts'],
      selected_markers: [],
      extra_args: '--headed',
      executor_mode: 'docker',
      timeout: 120,
      env: { APP_REPO_PATH: '~/code/my-app' },
    })
  })
})
