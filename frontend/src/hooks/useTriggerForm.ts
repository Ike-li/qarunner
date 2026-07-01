import { useState, useCallback } from 'react'
import type { Profile } from '../types'
import { buildProfilePayload } from '../profilePayload'

interface UseTriggerFormOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
}

/**
 * Trigger-run modal form state + actions (create run, save/update profile,
 * open edit-profile mode).
 *
 * Lifted out of App so the trigger form state (~20 fields) doesn't bloat the
 * layout component.
 */
export function useTriggerForm({ apiFetch }: UseTriggerFormOpts) {
  // ── form fields ──────────────────────────────────────────────────────
  const [testsPath, setTestsPath] = useState('')
  const [selectedRunner, setSelectedRunner] = useState('pytest')
  const [customArgs, setCustomArgs] = useState('')
  const [allureEnabled, setAllureEnabled] = useState(true)
  const [timeoutSeconds, setTimeoutSeconds] = useState<number | ''>('')
  const [envVars, setEnvVars] = useState<{ key: string; value: string }[]>([])

  // ── profile-editing state ────────────────────────────────────────────
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null)
  const [selectedProfileId, setSelectedProfileId] = useState('')
  const [profileName, setProfileName] = useState('')
  const [profileDesc, setProfileDesc] = useState('')
  const [isSavingProfile, setIsSavingProfile] = useState(false)

  // ── submission state ─────────────────────────────────────────────────
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  // ── helpers ──────────────────────────────────────────────────────────

  const makeEnvPayload = useCallback(
    () =>
      envVars.reduce(
        (acc, curr) => {
          const k = curr.key.trim()
          if (k) acc[k] = curr.value
          return acc
        },
        {} as Record<string, string>,
      ),
    [envVars],
  )

  const resetForm = useCallback(() => {
    setTestsPath('')
    setSelectedRunner('pytest')
    setCustomArgs('')
    setAllureEnabled(true)
    setTimeoutSeconds('')
    setEnvVars([])
    setEditingProfileId(null)
    setSelectedProfileId('')
    setProfileName('')
    setProfileDesc('')
    setIsSavingProfile(false)
    setIsSubmitting(false)
    setFormError(null)
  }, [])

  // ── open edit-profile mode ───────────────────────────────────────────

  const openEditProfile = useCallback((profile: Profile) => {
    setEditingProfileId(profile.id)
    setTestsPath(profile.tests_path)
    setCustomArgs(profile.extra_args || '')
    setAllureEnabled(true)
    setTimeoutSeconds(profile.timeout === null ? '' : profile.timeout)
    setProfileName(profile.name || '')
    setProfileDesc(profile.description || '')
    setEnvVars(
      profile.env
        ? Object.entries(profile.env).map(([key, value]) => ({
            key,
            value: String(value),
          }))
        : [],
    )
    setIsSavingProfile(true)
    setFormError(null)
  }, [])

  // ── actions ──────────────────────────────────────────────────────────

  const handleTriggerRun = useCallback(
    async (e: React.FormEvent, afterRun: (runId: string) => void) => {
      e.preventDefault()
      if (!testsPath) {
        setFormError('Please select a test directory.')
        return
      }
      setIsSubmitting(true)
      setFormError(null)

      try {
        const resp = await apiFetch('/runs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            tests_path: testsPath,
            runner: selectedRunner,
            args: [],
            allure: allureEnabled,
            timeout: timeoutSeconds === '' ? null : Number(timeoutSeconds),
            selected_files: [],
            selected_markers: [],
            extra_args: customArgs,
            env: makeEnvPayload(),
          }),
        })
        if (resp.ok) {
          const newRun = await resp.json()
          resetForm()
          afterRun(newRun.id)
        } else {
          const err = await resp.json()
          setFormError(err.detail || 'Failed to trigger run.')
        }
      } catch {
        setFormError('Network error. Failed to connect to server.')
      } finally {
        setIsSubmitting(false)
      }
    },
    [apiFetch, testsPath, selectedRunner, allureEnabled, timeoutSeconds, customArgs, makeEnvPayload, resetForm],
  )

  const handleSaveProfile = useCallback(
    async (
      e: React.FormEvent,
      selectedFiles: string[],
      selectedMarkers: string[],
      afterSave: () => void,
    ) => {
      e.preventDefault()
      if (!profileName.trim()) {
        setFormError('Please enter a profile name.')
        return
      }
      try {
        const resp = await apiFetch('/profiles', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildProfilePayload({
            profileName,
            profileDesc,
            testsPath,
            selectedRunner,
            selectedFiles,
            selectedMarkers,
            customArgs,
            timeoutSeconds,
            env: makeEnvPayload(),
          })),
        })
        if (resp.ok) {
          setProfileName('')
          setProfileDesc('')
          setIsSavingProfile(false)
          afterSave()
        } else {
          const err = await resp.json()
          setFormError(err.detail || 'Failed to save execution profile.')
        }
      } catch {
        setFormError('Network error. Failed to save execution profile.')
      }
    },
    [apiFetch, profileName, profileDesc, testsPath, selectedRunner, customArgs, timeoutSeconds, makeEnvPayload],
  )

  const handleUpdateProfile = useCallback(
    async (
      e: React.FormEvent,
      selectedFiles: string[],
      selectedMarkers: string[],
      afterUpdate: () => void,
    ) => {
      e.preventDefault()
      if (!editingProfileId) return
      if (!profileName.trim()) {
        setFormError('Please enter a profile name.')
        return
      }
      setIsSubmitting(true)
      try {
        const resp = await apiFetch(`/profiles/${editingProfileId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildProfilePayload({
            profileName,
            profileDesc,
            testsPath,
            selectedRunner,
            selectedFiles,
            selectedMarkers,
            customArgs,
            timeoutSeconds,
            env: makeEnvPayload(),
          })),
        })
        if (resp.ok) {
          resetForm()
          afterUpdate()
        } else {
          const err = await resp.json()
          setFormError(err.detail || 'Failed to update profile.')
        }
      } catch {
        setFormError('Network error. Failed to update profile.')
      } finally {
        setIsSubmitting(false)
      }
    },
    [apiFetch, editingProfileId, profileName, profileDesc, testsPath, selectedRunner, customArgs, timeoutSeconds, makeEnvPayload, resetForm],
  )

  return {
    // state
    testsPath, setTestsPath,
    selectedRunner, setSelectedRunner,
    customArgs, setCustomArgs,
    allureEnabled, setAllureEnabled,
    timeoutSeconds, setTimeoutSeconds,
    envVars, setEnvVars,
    editingProfileId, setEditingProfileId,
    selectedProfileId, setSelectedProfileId,
    profileName, setProfileName,
    profileDesc, setProfileDesc,
    isSavingProfile, setIsSavingProfile,
    isSubmitting,
    formError,
    // helpers
    makeEnvPayload,
    resetForm,
    // actions
    openEditProfile,
    handleTriggerRun,
    handleSaveProfile,
    handleUpdateProfile,
  } as const
}
