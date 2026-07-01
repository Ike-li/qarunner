import { describe, it, expect } from 'vitest'
import { useRuns } from './useRuns'

describe('useRuns', () => {
  it('exports a function', () => {
    expect(typeof useRuns).toBe('function')
  })
})
