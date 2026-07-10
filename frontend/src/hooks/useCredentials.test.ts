import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useCredentials } from './useCredentials'

describe('useCredentials', () => {
  it('exports a function', () => {
    expect(typeof useCredentials).toBe('function')
  })

  it('PERF: the returned object is referentially stable across a re-render with no state change', () => {
    const apiFetch = vi.fn()
    const { result, rerender } = renderHook(
      (props: { enabled: boolean }) => useCredentials({ apiFetch, ...props }),
      { initialProps: { enabled: false } },
    )
    const first = result.current

    // A re-render triggered by something unrelated (e.g. a sibling context
    // consumer updating) must not produce a new object — otherwise every
    // DashboardContext consumer downstream re-renders needlessly.
    rerender({ enabled: false })

    expect(result.current).toBe(first)
  })
})
