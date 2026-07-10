import { describe, it, expect, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useSchedules } from './useSchedules'

describe('useSchedules', () => {
  it('exports a function', () => {
    expect(typeof useSchedules).toBe('function')
  })

  it('B: isTriggeringSchedule is true while a manual trigger is in flight', async () => {
    let resolveFetch!: (v: Response) => void
    const apiFetch = vi.fn(
      () => new Promise<Response>((resolve) => { resolveFetch = resolve }),
    )
    const { result } = renderHook(() => useSchedules({ apiFetch, enabled: false, lang: 'en' }))

    expect(result.current.isTriggeringSchedule).toBe(false)

    let triggerDone!: Promise<boolean>
    act(() => {
      triggerDone = result.current.handleTriggerSchedule('sched-1')
    })
    await waitFor(() => {
      expect(result.current.isTriggeringSchedule).toBe(true)
    })

    await act(async () => {
      resolveFetch({ ok: true, json: async () => ({}) } as unknown as Response)
      await triggerDone
    })
    expect(result.current.isTriggeringSchedule).toBe(false)
  })

  it('PERF: the returned object is referentially stable across a re-render with no state change', () => {
    const apiFetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: async () => ([]) } as unknown as Response),
    )
    const { result, rerender } = renderHook(
      (props: { enabled: boolean; lang: string }) => useSchedules({ apiFetch, ...props }),
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
