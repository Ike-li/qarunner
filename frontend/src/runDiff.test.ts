import { describe, expect, it } from 'vitest'

import { diffBuckets, diffIsEmpty, diffTotal } from './runDiff'
import type { RunDiff } from './types'

const mkCase = (name: string, status: 'passed' | 'failed' | 'skipped' | 'error') => ({
  suite: 's',
  name,
  status,
  duration_ms: 0,
  message: null,
})

const emptyDiff: RunDiff['diff'] = {
  new_failures: [],
  fixed: [],
  still_failing: [],
  new_cases: [],
  removed_cases: [],
}

describe('diffBuckets', () => {
  it('orders buckets with new_failures first', () => {
    const keys = diffBuckets(emptyDiff).map((b) => b.key)
    expect(keys).toEqual([
      'new_failures',
      'still_failing',
      'fixed',
      'new_cases',
      'removed_cases',
    ])
  })

  it('maps each bucket to its tone and cases', () => {
    const diff = { ...emptyDiff, new_failures: [mkCase('a', 'failed')] }
    expect(diffBuckets(diff)[0]).toEqual({
      key: 'new_failures',
      tone: 'danger',
      cases: [mkCase('a', 'failed')],
    })
  })
})

describe('diffTotal / diffIsEmpty', () => {
  it('counts cases across all buckets', () => {
    const diff = {
      ...emptyDiff,
      new_failures: [mkCase('a', 'failed')],
      fixed: [mkCase('b', 'passed'), mkCase('c', 'passed')],
    }
    expect(diffTotal(diff)).toBe(3)
    expect(diffIsEmpty(diff)).toBe(false)
  })

  it('treats an all-empty diff as empty', () => {
    expect(diffTotal(emptyDiff)).toBe(0)
    expect(diffIsEmpty(emptyDiff)).toBe(true)
  })
})
