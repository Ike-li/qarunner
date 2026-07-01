import { describe, it, expect } from 'vitest'
import { useSuites } from './useSuites'

describe('useSuites', () => {
  it('exports a function', () => {
    expect(typeof useSuites).toBe('function')
  })
})
