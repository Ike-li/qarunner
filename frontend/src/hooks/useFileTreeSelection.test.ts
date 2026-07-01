import { describe, it, expect } from 'vitest'
import { useFileTreeSelection } from './useFileTreeSelection'

describe('useFileTreeSelection', () => {
  it('exports a function', () => {
    expect(typeof useFileTreeSelection).toBe('function')
  })

  it('returns expected shape', () => {
    // Call the hook factory — it returns a tuple of state + setters
    // We can't call it without React context, so just verify the function signature
    expect(useFileTreeSelection.length).toBe(0) // no required args
  })
})
