import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useSuites } from './useSuites'

describe('useSuites', () => {
  it('exports a function', () => {
    expect(typeof useSuites).toBe('function')
  })

  it('PERF: the returned object is referentially stable across a re-render with no state change', () => {
    const apiFetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: async () => [] } as unknown as Response),
    )
    const { result, rerender } = renderHook(
      (props: { enabled: boolean; lang: string }) => useSuites({ apiFetch, ...props }),
      { initialProps: { enabled: false, lang: 'en' } },
    )
    const first = result.current

    // A re-render triggered by something unrelated (e.g. a sibling context
    // consumer updating) must not produce a new object — otherwise every
    // DashboardContext consumer downstream re-renders needlessly.
    rerender({ enabled: false, lang: 'en' })

    expect(result.current).toBe(first)
  })
})
