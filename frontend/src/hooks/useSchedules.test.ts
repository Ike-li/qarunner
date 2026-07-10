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
})
