import { useState, useCallback } from 'react'
import type { UserProfile } from '../types'

interface UseAuthOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
}

/**
 * Auth state + login-form state + login action.
 *
 * The auth-cookie probe runs once on mount (driven from App via an effect)
 * so an already-logged-in user never sees the login screen flash.
 * ``isAuthenticated === null`` means "probe in flight" — App shows a spinner.
 */
export function useAuth({ apiFetch }: UseAuthOpts) {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null)

  // Login form
  const [loginUsername, setLoginUsername] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginLoading, setLoginLoading] = useState(false)

  /** Probe the HttpOnly auth cookie. Returns true when the session is valid. */
  const probeAuth = useCallback(async () => {
    try {
      const resp = await apiFetch('/auth/me')
      if (resp.ok) {
        const user: UserProfile = await resp.json()
        setCurrentUser(user)
        setIsAuthenticated(true)
      } else {
        setIsAuthenticated(false)
      }
    } catch {
      setIsAuthenticated(false)
    }
  }, [apiFetch])

  const handleLoginSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      if (!loginUsername.trim() || !loginPassword.trim()) {
        setLoginError('Please enter both username and password.')
        return
      }

      setLoginLoading(true)
      setLoginError(null)

      try {
        const resp = await apiFetch('/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: loginUsername.trim(),
            password: loginPassword,
          }),
        })

        if (resp.ok) {
          setLoginUsername('')
          setLoginPassword('')
          await probeAuth()
        } else {
          const err = await resp.json()
          setLoginError(err.detail || 'Invalid username or password.')
        }
      } catch {
        setLoginError('Network error. Failed to connect to authentication server.')
      } finally {
        setLoginLoading(false)
      }
    },
    [apiFetch, loginUsername, loginPassword, probeAuth],
  )

  return {
    // Auth gate
    isAuthenticated,
    currentUser,
    // Login form
    loginUsername,
    setLoginUsername,
    loginPassword,
    setLoginPassword,
    loginError,
    setLoginError,
    loginLoading,
    // Actions
    probeAuth,
    handleLoginSubmit,
    // State reset (called by clearSession)
    _reset: () => {
      setIsAuthenticated(false)
      setCurrentUser(null)
      setLoginUsername('')
      setLoginPassword('')
      setLoginError(null)
      setLoginLoading(false)
    },
  } as const
}
