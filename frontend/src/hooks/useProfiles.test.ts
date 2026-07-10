import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useProfiles } from './useProfiles'

describe('useProfiles', () => {
  it('exports a function', () => {
    expect(typeof useProfiles).toBe('function')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('PERF: the returned object is referentially stable across a re-render with no state change', () => {
    const apiFetch = vi.fn()
    const { result, rerender } = renderHook(
      (props: { enabled: boolean; lang: string }) => useProfiles({ apiFetch, ...props }),
      { initialProps: { enabled: false, lang: 'en' } },
    )
    const first = result.current

    // A re-render triggered by something unrelated (e.g. a sibling context
    // consumer updating) must not produce a new object — otherwise every
    // DashboardContext consumer downstream re-renders needlessly.
    rerender({ enabled: false, lang: 'en' })

    expect(result.current).toBe(first)
  })

  describe('B3: handleDeleteProfile return value', () => {
    it('resolves false when the user cancels the confirmation, without calling the API', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(false)
      const apiFetch = vi.fn()
      const { result } = renderHook(() =>
        useProfiles({ apiFetch, enabled: false, lang: 'en' }),
      )

      let deleted: boolean | undefined
      await act(async () => {
        deleted = await result.current.handleDeleteProfile('p1')
      })

      expect(deleted).toBe(false)
      expect(apiFetch).not.toHaveBeenCalled()
    })

    it('resolves true after a confirmed, successful delete', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const apiFetch = vi.fn(async () => ({ ok: true, json: async () => [] }) as unknown as Response)
      const { result } = renderHook(() =>
        useProfiles({ apiFetch, enabled: false, lang: 'en' }),
      )

      let deleted: boolean | undefined
      await act(async () => {
        deleted = await result.current.handleDeleteProfile('p1')
      })

      expect(deleted).toBe(true)
      expect(apiFetch).toHaveBeenCalledWith('/profiles/p1', { method: 'DELETE' })
    })

    it('resolves false when the confirmed delete fails server-side', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      vi.spyOn(window, 'alert').mockImplementation(() => {})
      const apiFetch = vi.fn(
        async () => ({ ok: false, json: async () => ({ detail: 'nope' }) }) as unknown as Response,
      )
      const { result } = renderHook(() =>
        useProfiles({ apiFetch, enabled: false, lang: 'en' }),
      )

      let deleted: boolean | undefined
      await act(async () => {
        deleted = await result.current.handleDeleteProfile('p1')
      })

      expect(deleted).toBe(false)
    })

    it('resolves false when the confirmed delete throws a network error', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const apiFetch = vi.fn(async () => {
        throw new Error('network')
      })
      const { result } = renderHook(() =>
        useProfiles({ apiFetch, enabled: false, lang: 'en' }),
      )

      let deleted: boolean | undefined
      await act(async () => {
        deleted = await result.current.handleDeleteProfile('p1')
      })

      expect(deleted).toBe(false)
    })
  })
})
