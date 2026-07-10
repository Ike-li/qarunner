import { act, renderHook } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import type { UserProfile } from '../types'
import { useUsers } from './useUsers'

describe('useUsers', () => {
  it('exports a function', () => {
    expect(typeof useUsers).toBe('function')
  })

  it('does not request admin-only users for a non-admin role', async () => {
    const apiFetch = vi.fn(async () => new Response(JSON.stringify({ users: [] })))
    const currentUser: UserProfile = {
      username: 'regular-user',
      role: 'user',
      created_at: '2026-07-06T00:00:00Z',
    }

    const { result } = renderHook(() => useUsers({ apiFetch, currentUser }))

    await act(async () => {
      await result.current.fetchUsers()
    })

    expect(apiFetch).not.toHaveBeenCalled()
    expect(result.current.usersList).toEqual([])
  })

  it('fetches and stores users for an admin role', async () => {
    const users: UserProfile[] = [
      {
        username: 'admin-user',
        role: 'admin',
        created_at: '2026-07-06T00:00:00Z',
      },
      {
        username: 'regular-user',
        role: 'user',
        created_at: '2026-07-06T00:00:00Z',
      },
    ]
    const apiFetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ users }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    const currentUser: UserProfile = {
      username: 'admin-user',
      role: 'admin',
      created_at: '2026-07-06T00:00:00Z',
    }

    const { result } = renderHook(() => useUsers({ apiFetch, currentUser }))

    await act(async () => {
      await result.current.fetchUsers()
    })

    expect(apiFetch).toHaveBeenCalledWith('/users')
    expect(result.current.usersList).toEqual(users)
  })

  describe('handleRetentionDaysChange', () => {
    const currentUser: UserProfile = {
      username: 'admin-user',
      role: 'admin',
      created_at: '2026-07-06T00:00:00Z',
    }

    it('accepts a valid positive integer', () => {
      const apiFetch = vi.fn()
      const { result } = renderHook(() => useUsers({ apiFetch, currentUser }))
      act(() => {
        result.current.handleRetentionDaysChange('14')
      })
      expect(result.current.retentionDays).toBe(14)
    })

    it('ignores non-numeric input, keeping the last valid value (BUG: no NaN/negative guard)', () => {
      const apiFetch = vi.fn()
      const { result } = renderHook(() => useUsers({ apiFetch, currentUser }))
      act(() => {
        result.current.handleRetentionDaysChange('abc')
      })
      expect(result.current.retentionDays).toBe(30)
      expect(Number.isNaN(result.current.retentionDays)).toBe(false)
    })

    it('ignores zero/negative input — retention_days feeds a destructive cleanup call', () => {
      const apiFetch = vi.fn()
      const { result } = renderHook(() => useUsers({ apiFetch, currentUser }))
      act(() => {
        result.current.handleRetentionDaysChange('-5')
      })
      expect(result.current.retentionDays).toBe(30)
      act(() => {
        result.current.handleRetentionDaysChange('0')
      })
      expect(result.current.retentionDays).toBe(30)
    })
  })
})
