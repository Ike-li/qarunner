import { describe, it, expect } from 'vitest'
import { translations, type TranslationKey } from './i18n'

describe('i18n translations', () => {
  it('has both en and zh locales', () => {
    expect(translations.en).toBeDefined()
    expect(translations.zh).toBeDefined()
  })

  it('has matching keys in en and zh', () => {
    const enKeys = Object.keys(translations.en).sort()
    const zhKeys = Object.keys(translations.zh).sort()
    expect(zhKeys).toEqual(enKeys)
  })

  it('has no empty translation values', () => {
    for (const [key, value] of Object.entries(translations.en)) {
      expect(value, `en.${key}`).not.toBe('')
    }
    for (const [key, value] of Object.entries(translations.zh)) {
      expect(value, `zh.${key}`).not.toBe('')
    }
  })

  it('has expected core keys', () => {
    const requiredKeys: TranslationKey[] = [
      'signIn', 'username', 'password',
      'executionRecords', 'triggerRun',
      'totalExecutions', 'successRate', 'failedRuns', 'activeQueue',
      'status_completed', 'status_failed', 'status_running',
    ]
    for (const key of requiredKeys) {
      expect(translations.en[key]).toBeDefined()
      expect(translations.zh[key]).toBeDefined()
    }
  })
})
