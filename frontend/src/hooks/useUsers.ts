import { useState, useCallback } from 'react'
import type { UserProfile } from '../types'

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

  return {
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
    setRetentionDays,
    isCleaningStorage,
    setIsCleaningStorage,
    isAdmin,
    fetchUsers,
    handleCreateUserSubmit,
    _reset: () => {
      setUsersList([])
      setUsersLoading(false)
      setNewUsername('')
      setNewPassword('')
      setNewUserRole('user')
      setNewUserError(null)
      setNewUserLoading(false)
    },
  } as const
}
