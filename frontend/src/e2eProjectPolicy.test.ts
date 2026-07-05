import { describe, expect, it } from 'vitest'

import playwrightConfig from '../playwright.config'

type ProjectConfig = {
  name?: string
  testMatch?: RegExp | RegExp[]
  testIgnore?: RegExp | RegExp[]
  use?: { storageState?: string }
}

function projectByName(name: string): ProjectConfig {
  const projects = (playwrightConfig.projects ?? []) as ProjectConfig[]
  const project = projects.find((candidate) => candidate.name === name)
  if (!project) throw new Error(`Missing Playwright project: ${name}`)
  return project
}

function regexSources(value: RegExp | RegExp[] | undefined): string[] {
  if (!value) return []
  return Array.isArray(value) ? value.map((item) => item.source) : [value.source]
}

describe('Playwright E2E project policy', () => {
  it('limits the unauthenticated chromium project to explicit login and anonymous specs', () => {
    const chromium = projectByName('chromium')

    expect(chromium.use?.storageState).toBeUndefined()
    expect(chromium.testIgnore).toBeUndefined()
    expect(regexSources(chromium.testMatch)).toEqual([
      '.*\\/(login-auth|login-smoke|role-anonymous)\\.spec\\.ts',
    ])
  })

  it('keeps authenticated projects opt-in by filename suffix and storageState', () => {
    const admin = projectByName('chromium-authed-admin')
    const user = projectByName('chromium-authed-user')

    expect(regexSources(admin.testMatch)).toEqual(['.*\\.authed-admin\\.spec\\.ts'])
    expect(admin.use?.storageState).toBe('./tests-e2e/.auth/admin.json')
    expect(regexSources(user.testMatch)).toEqual(['.*\\.authed-user\\.spec\\.ts'])
    expect(user.use?.storageState).toBe('./tests-e2e/.auth/user.json')
  })
})
