import { describe, it, expect } from 'vitest'
import { useDialogA11y } from './useDialogA11y'

describe('useDialogA11y', () => {
  it('exports a function', () => {
    expect(typeof useDialogA11y).toBe('function')
  })
})
