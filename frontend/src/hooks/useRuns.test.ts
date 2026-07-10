import { describe, it, expect, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useRuns } from './useRuns'

describe('useRuns', () => {
  it('exports a function', () => {
    expect(typeof useRuns).toBe('function')
  })

  it('B2: fetchSelectedRunDetails discards a response for a run that is no longer selected', async () => {
    // run-a's request is slow; run-b's resolves immediately. Mirrors a poll
    // cycle that queued a fetch for run-a right before the user switched
    // the selection to run-b.
    let resolveA!: (v: unknown) => void
    const aPromise = new Promise((resolve) => {
      resolveA = resolve
    })
    const apiFetch = vi.fn((path: string) => {
      if (path === '/runs') {
        return Promise.resolve({ ok: true, json: async () => ({ runs: [] }) } as unknown as Response)
      }
      if (path === '/runs/run-a') return aPromise as Promise<Response>
      if (path === '/runs/run-b') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ id: 'run-b', status: 'completed' }),
        } as unknown as Response)
      }
      throw new Error(`unexpected path: ${path}`)
    })

    const { result } = renderHook(() => useRuns({ apiFetch, enabled: false }))

    act(() => {
      result.current.setSelectedRunId('run-b')
    })
    await waitFor(() => {
      expect(result.current.selectedRunDetails?.id).toBe('run-b')
    })

    let fetchADone!: Promise<void>
    act(() => {
      fetchADone = result.current.fetchSelectedRunDetails('run-a')
    })
    await act(async () => {
      resolveA({ ok: true, json: async () => ({ id: 'run-a', status: 'completed' }) })
      await fetchADone
    })

    // The stale run-a response must not have overwritten run-b's details.
    expect(result.current.selectedRunDetails?.id).toBe('run-b')
  })
})
