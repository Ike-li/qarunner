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
