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
})
