import { useState, useEffect, useCallback, useMemo } from 'react'
import type { Credential } from '../types'
import { apiMutate } from './useApi'

interface UseCredentialsOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
  enabled: boolean
}

/**
 * Git credentials data layer (P0-1) — list / create / delete. Secrets are
 * write-only: created here, never read back (the API never returns them).
 */
export function useCredentials({ apiFetch, enabled }: UseCredentialsOpts) {
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [credentialsLoadError, setCredentialsLoadError] = useState(false)

  const fetchCredentials = useCallback(async () => {
    setCredentialsLoadError(false)
    try {
      const resp = await apiFetch('/credentials')
      if (!resp.ok) throw new Error(`Credentials request failed: ${resp.status}`)
      const data = await resp.json()
      setCredentials(data.credentials)
      setCredentialsLoadError(false)
    } catch (err) {
      console.error('Error fetching credentials:', err)
      setCredentials([])
      setCredentialsLoadError(true)
    }
  }, [apiFetch])

  useEffect(() => {
    if (enabled) fetchCredentials()
  }, [enabled, fetchCredentials])

  const createCredential = useCallback(
    async (name: string, secret: string): Promise<Credential | null> => {
      const resp = await apiMutate(
        apiFetch,
        '/credentials',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, type: 'https_token', secret }),
        },
        'Failed to create credential.',
      )
      if (!resp) return null
      const cred = (await resp.json()) as Credential
      await fetchCredentials()
      return cred
    },
    [apiFetch, fetchCredentials],
  )

  const deleteCredential = useCallback(
    async (id: string) => {
      const resp = await apiMutate(
        apiFetch,
        `/credentials/${id}`,
        { method: 'DELETE' },
        'Failed to delete credential.',
      )
      if (resp) await fetchCredentials()
    },
    [apiFetch, fetchCredentials],
  )

  const _reset = useCallback(() => {
    setCredentials([])
    setCredentialsLoadError(false)
  }, [])

  return useMemo(
    () =>
      ({
        credentials,
        credentialsLoadError,
        fetchCredentials,
        createCredential,
        deleteCredential,
        _reset,
      }) as const,
    [
      credentials, credentialsLoadError, fetchCredentials, createCredential,
      deleteCredential, _reset,
    ],
  )
}
