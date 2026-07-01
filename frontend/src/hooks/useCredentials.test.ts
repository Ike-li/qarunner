import { describe, it, expect } from 'vitest'
import { useCredentials } from './useCredentials'

describe('useCredentials', () => {
  it('exports a function', () => {
    expect(typeof useCredentials).toBe('function')
  })
})
