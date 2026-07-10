import { describe, it, expect } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useTerminalView } from './useTerminalView'

describe('useTerminalView', () => {
  it('PERF: the returned object is referentially stable across a re-render with no state change', () => {
    const { result, rerender } = renderHook(() => useTerminalView())
    const first = result.current

    // A re-render triggered by something unrelated (e.g. a sibling context
    // consumer updating) must not produce a new object — otherwise every
    // DashboardContext consumer downstream re-renders needlessly.
    rerender()

    expect(result.current).toBe(first)
  })
})
