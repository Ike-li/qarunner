import { describe, it, expect } from 'vitest'
import { useTriggerForm } from './useTriggerForm'

describe('useTriggerForm', () => {
  it('exports a function', () => {
    expect(typeof useTriggerForm).toBe('function')
  })
})
