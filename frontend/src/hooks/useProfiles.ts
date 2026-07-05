import { useState, useEffect, useCallback } from 'react'
import type { Profile } from '../types'

interface UseProfilesOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
  enabled: boolean
  lang: string
  /** Called after a profile is deleted so schedules can refresh too. */
  onProfileChanged?: () => void
}

/**
 * Profiles data layer — list, create, update, delete, and direct trigger.
 */
export function useProfiles({ apiFetch, enabled, lang, onProfileChanged }: UseProfilesOpts) {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profileLoadError, setProfileLoadError] = useState(false)

  // ── fetch ──────────────────────────────────────────────────────────────

  const fetchProfiles = useCallback(async () => {
    setProfileLoadError(false)
    try {
      const resp = await apiFetch('/profiles')
      if (!resp.ok) throw new Error(`Profiles request failed: ${resp.status}`)
      const data = await resp.json()
      setProfiles(data)
      setProfileLoadError(false)
    } catch (err) {
      console.error('Error fetching profiles:', err)
      setProfiles([])
      setProfileLoadError(true)
    }
  }, [apiFetch])

  useEffect(() => {
    if (enabled) fetchProfiles()
  }, [enabled, fetchProfiles])

  // ── actions ────────────────────────────────────────────────────────────

  /** Direct one-click trigger from the sidebar. */
  const handleTriggerProfile = useCallback(
    async (profile: Profile, afterRun: (runId: string) => void) => {
      try {
        const resp = await apiFetch(`/profiles/${encodeURIComponent(profile.id)}/trigger`, {
          method: 'POST',
        })
        if (resp.ok) {
          const newRun = await resp.json()
          afterRun(newRun.id)
        } else {
          const err = await resp.json()
          alert(err.detail || 'Failed to trigger run for profile.')
        }
      } catch (err) {
        console.error('Network error. Failed to trigger run for profile.', err)
      }
    },
    [apiFetch],
  )

  const handleDeleteProfile = useCallback(
    async (profileId: string, e?: React.MouseEvent) => {
      if (e) e.stopPropagation()
      const msg =
        lang === 'zh'
          ? '确定要删除此执行方案吗？'
          : 'Are you sure you want to delete this profile?'
      if (!window.confirm(msg)) return

      try {
        const resp = await apiFetch(`/profiles/${profileId}`, {
          method: 'DELETE',
        })
        if (resp.ok) {
          await fetchProfiles()
          onProfileChanged?.()
        } else {
          const err = await resp.json()
          alert(err.detail || 'Failed to delete profile.')
        }
      } catch (err) {
        console.error('Error deleting profile:', err)
      }
    },
    [apiFetch, lang, fetchProfiles, onProfileChanged],
  )

  return {
    profiles,
    profileLoadError,
    fetchProfiles,
    handleTriggerProfile,
    handleDeleteProfile,
    _reset: () => {
      setProfiles([])
      setProfileLoadError(false)
    },
  } as const
}
