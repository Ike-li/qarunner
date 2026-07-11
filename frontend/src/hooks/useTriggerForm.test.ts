import { describe, it, expect, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useTriggerForm } from './useTriggerForm'

describe('useTriggerForm', () => {
  it('exports a function', () => {
    expect(typeof useTriggerForm).toBe('function')
  })

  describe('handleTimeoutSecondsChange', () => {
    function setup() {
      const apiFetch = vi.fn()
      return renderHook(() => useTriggerForm({ apiFetch }))
    }

    it('accepts a valid positive integer', () => {
      const { result } = setup()
      act(() => {
        result.current.handleTimeoutSecondsChange('120')
      })
      expect(result.current.timeoutSeconds).toBe(120)
    })

    it('clears back to "" (use the default timeout)', () => {
      const { result } = setup()
      act(() => {
        result.current.handleTimeoutSecondsChange('120')
      })
      act(() => {
        result.current.handleTimeoutSecondsChange('')
      })
      expect(result.current.timeoutSeconds).toBe('')
    })

    it('ignores non-numeric input, keeping the last valid value (BUG: no NaN/negative guard)', () => {
      const { result } = setup()
      act(() => {
        result.current.handleTimeoutSecondsChange('120')
      })
      act(() => {
        result.current.handleTimeoutSecondsChange('abc')
      })
      expect(result.current.timeoutSeconds).toBe(120)
    })

    it('ignores zero/negative input', () => {
      const { result } = setup()
      act(() => {
        result.current.handleTimeoutSecondsChange('120')
      })
      act(() => {
        result.current.handleTimeoutSecondsChange('-30')
      })
      expect(result.current.timeoutSeconds).toBe(120)
      act(() => {
        result.current.handleTimeoutSecondsChange('0')
      })
      expect(result.current.timeoutSeconds).toBe(120)
    })
  })

  it('PERF: the returned object is referentially stable across a re-render with no state change', () => {
    const apiFetch = vi.fn()
    const { result, rerender } = renderHook(() => useTriggerForm({ apiFetch }))
    const first = result.current
    rerender()
    expect(result.current).toBe(first)
  })
})
