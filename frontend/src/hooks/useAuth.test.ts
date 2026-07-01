import { describe, it, expect } from 'vitest'
import { useAuth } from './useAuth'

describe('useAuth', () => {
  it('exports a function', () => {
    expect(typeof useAuth).toBe('function')
  })
})
