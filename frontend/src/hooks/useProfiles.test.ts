import { describe, it, expect } from 'vitest'
import { useProfiles } from './useProfiles'

describe('useProfiles', () => {
  it('exports a function', () => {
    expect(typeof useProfiles).toBe('function')
  })
})
