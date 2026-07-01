import { describe, it, expect } from 'vitest'
import { useSchedules } from './useSchedules'

describe('useSchedules', () => {
  it('exports a function', () => {
    expect(typeof useSchedules).toBe('function')
  })
})
