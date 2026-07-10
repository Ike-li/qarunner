import { useCallback } from 'react'

export type ApiFetch = (path: string, opts?: RequestInit) => Promise<Response>

/**
 * Shared mutation wrapper: centralises the error handling every domain handler
 * repeated (try / non-ok → alert(detail||fallback) / catch → console.error).
 * Returns the ok Response (so each caller writes only its success path) or null
 * on any failure.
 */
export async function apiMutate(
  apiFetch: ApiFetch,
  path: string,
  opts: RequestInit,
  fallbackMsg: string,
): Promise<Response | null> {
  try {
    const resp = await apiFetch(path, opts)
    if (resp.ok) return resp
    const err = await resp.json()
    alert(err.detail || fallbackMsg)
    return null
  } catch (err) {
    console.error(`Mutation failed: ${opts.method ?? 'GET'} ${path}`, err)
    return null
  }
}

/**
 * Foundational API hook — the single fetch wrapper every domain hook and
 * component uses.  Centralises cookie-based auth (SEC-6) and the 401 →
 * session-clear path so no caller duplicates it.
 *
 * Accepts an `onSessionClear` callback because clearing client state requires
 * access to every state setter in App; the hook itself stays pure.
 */
export function useApi(onSessionClear: () => void) {
  const apiFetch = useCallback(
    async (path: string, opts: RequestInit = {}): Promise<Response> => {
      const resp = await globalThis.fetch(path, {
        ...opts,
        credentials: 'include',
      })
      if (resp.status === 401) {
        onSessionClear()
      }
      return resp
    },
    [onSessionClear],
  )

  /** Best-effort server-side cookie clear + client reset. */
  const handleLogout = useCallback(async () => {
    try {
      await apiFetch('/auth/logout', { method: 'POST' })
    } catch {
      // best-effort; clear client state regardless
    }
    onSessionClear()
  }, [apiFetch, onSessionClear])

  return { apiFetch, handleLogout } as const
}
