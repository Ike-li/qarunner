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

  // ── fetch ──────────────────────────────────────────────────────────────

  const fetchProfiles = useCallback(async () => {
    try {
      const resp = await apiFetch('/profiles')
      if (resp.ok) {
        const data = await resp.json()
        setProfiles(data)
      }
    } catch (err) {
      console.error('Error fetching profiles:', err)
    }
  }, [apiFetch])

  useEffect(() => {
    if (enabled) fetchProfiles()
  }, [enabled, fetchProfiles])

  // ── actions ────────────────────────────────────────────────────────────

  /** Direct one-click trigger from the sidebar. */
  const handleTriggerProfile = useCallback(
    async (profile: Profile, afterRun: (runId: string) => void) => {
      const payload = {
        tests_path: profile.tests_path,
        runner: profile.runner || 'pytest',
        args: [],
        allure: true,
        timeout: profile.timeout,
        selected_files: profile.selected_files,
        selected_markers: profile.selected_markers,
        extra_args: profile.extra_args,
        env: profile.env || {},
      }

      try {
        const resp = await apiFetch('/runs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
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
    fetchProfiles,
    handleTriggerProfile,
    handleDeleteProfile,
    _reset: () => {
      setProfiles([])
    },
  } as const
}
