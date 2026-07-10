import { useState, useCallback, useMemo } from 'react'
import type { UserProfile } from '../types'
import { apiMutate } from './useApi'

interface UseUsersOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
  /** Only fetch when the current user is admin. */
  currentUser: UserProfile | null
}

/**
 * Admin user management — list users, create user, storage cleanup.
 */
export function useUsers({ apiFetch, currentUser }: UseUsersOpts) {
  const [usersList, setUsersList] = useState<UserProfile[]>([])
  const [usersLoading, setUsersLoading] = useState(false)
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newUserRole, setNewUserRole] = useState<'admin' | 'user'>('user')
  const [newUserError, setNewUserError] = useState<string | null>(null)
  const [newUserLoading, setNewUserLoading] = useState(false)
  const [retentionDays, setRetentionDays] = useState(30)
  const [isCleaningStorage, setIsCleaningStorage] = useState(false)

  const isAdmin = currentUser?.role === 'admin'

  /**
   * BUG: a bare `Number(value)` accepted NaN (non-numeric input) and
   * negative/zero values into retentionDays, which feeds the destructive
   * storage-cleanup call — reject anything that isn't a positive integer
   * and keep the last valid value instead.
   */
  const handleRetentionDaysChange = useCallback((value: string) => {
    const n = Number(value)
    if (Number.isFinite(n) && n >= 1) {
      setRetentionDays(Math.floor(n))
    }
  }, [])

  const fetchUsers = useCallback(async () => {
    if (!isAdmin) return
    setUsersLoading(true)
    try {
      const resp = await apiFetch('/users')
      if (resp.ok) {
        const data = await resp.json()
        setUsersList(data.users)
      }
    } catch (err) {
      console.error('Error fetching users:', err)
    } finally {
      setUsersLoading(false)
    }
  }, [isAdmin, apiFetch])

  const handleCreateUserSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      if (!newUsername.trim() || !newPassword.trim()) {
        setNewUserError('Please fill in both username and password.')
        return
      }

      setNewUserLoading(true)
      setNewUserError(null)

      try {
        const resp = await apiFetch('/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: newUsername.trim(),
            password: newPassword,
            role: newUserRole,
          }),
        })
        if (resp.ok) {
          setNewUsername('')
          setNewPassword('')
          setNewUserRole('user')
          await fetchUsers()
        } else {
          const err = await resp.json()
          setNewUserError(err.detail || 'Failed to create user.')
        }
      } catch {
        setNewUserError('Network error. Failed to connect to server.')
      } finally {
        setNewUserLoading(false)
      }
    },
    [apiFetch, newUsername, newPassword, newUserRole, fetchUsers],
  )

  const handleDeleteUser = useCallback(
    async (username: string) => {
      const resp = await apiMutate(
        apiFetch,
        `/users/${encodeURIComponent(username)}`,
        { method: 'DELETE' },
        'Failed to delete user.',
      )
      if (resp) await fetchUsers()
    },
    [apiFetch, fetchUsers],
  )

  const handleUpdateUserRole = useCallback(
    async (username: string, role: 'admin' | 'user') => {
      const resp = await apiMutate(
        apiFetch,
        `/users/${encodeURIComponent(username)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role }),
        },
        'Failed to update role.',
      )
      if (resp) await fetchUsers()
    },
    [apiFetch, fetchUsers],
  )

  const handleUpdateUserPassword = useCallback(
    async (username: string, password: string): Promise<boolean> => {
      const resp = await apiMutate(
        apiFetch,
        `/users/${encodeURIComponent(username)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password }),
        },
        'Failed to update password.',
      )
      return resp !== null
    },
    [apiFetch],
  )

  const _reset = useCallback(() => {
    setUsersList([])
    setUsersLoading(false)
    setNewUsername('')
    setNewPassword('')
    setNewUserRole('user')
    setNewUserError(null)
    setNewUserLoading(false)
  }, [])

  const currentUsername = currentUser?.username ?? null

  return useMemo(
    () =>
      ({
        usersList,
        usersLoading,
        newUsername,
        setNewUsername,
        newPassword,
        setNewPassword,
        newUserRole,
        setNewUserRole,
        newUserError,
        setNewUserError,
        newUserLoading,
        retentionDays,
        handleRetentionDaysChange,
        isCleaningStorage,
        setIsCleaningStorage,
        isAdmin,
        currentUsername,
        fetchUsers,
        handleCreateUserSubmit,
        handleDeleteUser,
        handleUpdateUserRole,
        handleUpdateUserPassword,
        _reset,
      }) as const,
    [
      usersList, usersLoading, newUsername, newPassword, newUserRole, newUserError,
      newUserLoading, retentionDays, handleRetentionDaysChange, isCleaningStorage,
      isAdmin, currentUsername, fetchUsers, handleCreateUserSubmit, handleDeleteUser,
      handleUpdateUserRole, handleUpdateUserPassword, _reset,
    ],
  )
}
