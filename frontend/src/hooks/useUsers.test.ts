import { describe, it, expect } from 'vitest'
import { useUsers } from './useUsers'

describe('useUsers', () => {
  it('exports a function', () => {
    expect(typeof useUsers).toBe('function')
  })
})
