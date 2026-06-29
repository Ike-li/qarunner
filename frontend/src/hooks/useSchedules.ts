import { useState, useEffect, useCallback } from 'react'
import type { Profile, Schedule } from '../types'

interface UseSchedulesOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
  enabled: boolean
  /** i18n helper — for confirm dialogs. */
  lang: string
}

/**
 * Schedules data layer — list, create/update, delete, cron preview.
 */
export function useSchedules({ apiFetch, enabled, lang }: UseSchedulesOpts) {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [isScheduleModalOpen, setIsScheduleModalOpen] = useState(false)
  const [scheduleProfile, setScheduleProfile] = useState<Profile | null>(null)
  const [schedName, setSchedName] = useState('')
  const [schedExpression, setSchedExpression] = useState('')
  const [schedTimezone, setSchedTimezone] = useState('UTC')
  const [schedEnabled, setSchedEnabled] = useState(true)
  const [previewNextRuns, setPreviewNextRuns] = useState<string[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)

  // ── fetch ──────────────────────────────────────────────────────────────

  const fetchSchedules = useCallback(async () => {
    try {
      const resp = await apiFetch('/schedules')
      if (resp.ok) {
        const data = await resp.json()
        setSchedules(data)
      }
    } catch (err) {
      console.error('Error fetching schedules:', err)
    }
  }, [apiFetch])

  useEffect(() => {
    if (enabled) fetchSchedules()
  }, [enabled, fetchSchedules])

  // ── preview ────────────────────────────────────────────────────────────

  const fetchSchedulePreview = useCallback(
    async (expression: string, timezone: string) => {
      if (!expression) {
        setPreviewNextRuns([])
        setPreviewError(null)
        return
      }
      try {
        const resp = await apiFetch(
          `/schedules/preview?expression=${encodeURIComponent(expression)}&timezone=${encodeURIComponent(timezone)}`,
        )
        if (resp.ok) {
          const data = await resp.json()
          setPreviewNextRuns(data.next_runs)
          setPreviewError(null)
        } else {
          const err = await resp.json()
          setPreviewError(err.detail || 'Invalid cron expression')
          setPreviewNextRuns([])
        }
      } catch {
        setPreviewError('Failed to fetch schedule preview')
        setPreviewNextRuns([])
      }
    },
    [apiFetch],
  )

  // Debounced preview
  useEffect(() => {
    if (schedExpression) {
      const t = setTimeout(() => {
        fetchSchedulePreview(schedExpression, schedTimezone)
      }, 500)
      return () => clearTimeout(t)
    }
  }, [schedExpression, schedTimezone, fetchSchedulePreview])

  // ── modal open ─────────────────────────────────────────────────────────

  const handleOpenScheduleModal = useCallback(
    (profile: Profile) => {
      setScheduleProfile(profile)
      const existing = schedules.find((s) => s.profile_id === profile.id)
      if (existing) {
        setSchedName(existing.name)
        setSchedExpression(existing.cron_expression)
        setSchedTimezone(existing.timezone || 'UTC')
        setSchedEnabled(existing.enabled)
        fetchSchedulePreview(existing.cron_expression, existing.timezone || 'UTC')
      } else {
        setSchedName(`${profile.name} Schedule`)
        setSchedExpression('0 2 * * *')
        setSchedTimezone('UTC')
        setSchedEnabled(true)
        fetchSchedulePreview('0 2 * * *', 'UTC')
      }
      setPreviewError(null)
      setIsScheduleModalOpen(true)
    },
    [schedules, fetchSchedulePreview],
  )

  // ── save ───────────────────────────────────────────────────────────────

  const handleSaveSchedule = useCallback(async () => {
    if (!scheduleProfile) return
    const existing = schedules.find((s) => s.profile_id === scheduleProfile.id)
    const payload = {
      name: schedName,
      profile_id: scheduleProfile.id,
      cron_expression: schedExpression,
      enabled: schedEnabled,
      timezone: schedTimezone,
    }

    try {
      const url = existing ? `/schedules/${existing.id}` : '/schedules'
      const method = existing ? 'PUT' : 'POST'
      const resp = await apiFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (resp.ok) {
        await fetchSchedules()
        setIsScheduleModalOpen(false)
        setPreviewError(null)
      } else {
        const err = await resp.json()
        setPreviewError(err.detail || 'Failed to save schedule')
      }
    } catch {
      setPreviewError('Network error. Failed to save schedule.')
    }
  }, [
    scheduleProfile,
    schedules,
    schedName,
    schedExpression,
    schedEnabled,
    schedTimezone,
    apiFetch,
    fetchSchedules,
  ])

  // ── delete ─────────────────────────────────────────────────────────────

  const handleDeleteSchedule = useCallback(
    async (scheduleId: string) => {
      const msg =
        lang === 'zh'
          ? '确定要删除此定时调度吗？'
          : 'Are you sure you want to delete this schedule?'
      if (!window.confirm(msg)) return
      try {
        const resp = await apiFetch(`/schedules/${scheduleId}`, {
          method: 'DELETE',
        })
        if (resp.ok) {
          await fetchSchedules()
          if (scheduleProfile) {
            const existing = schedules.find(
              (s) => s.profile_id === scheduleProfile.id,
            )
            if (existing && existing.id === scheduleId) {
              setSchedName('')
              setSchedExpression('')
              setPreviewNextRuns([])
            }
          }
        }
      } catch (err) {
        console.error('Error deleting schedule:', err)
      }
    },
    [lang, scheduleProfile, schedules, apiFetch, fetchSchedules],
  )

  // ── manual trigger ───────────────────────────────────────────────────────

  const handleTriggerSchedule = useCallback(
    async (scheduleId: string): Promise<boolean> => {
      try {
        const resp = await apiFetch(`/schedules/${scheduleId}/trigger`, {
          method: 'POST',
        })
        if (resp.ok) return true
        const err = await resp.json()
        alert(err.detail || 'Failed to trigger schedule.')
        return false
      } catch (err) {
        console.error('Error triggering schedule:', err)
        return false
      }
    },
    [apiFetch],
  )

  return {
    schedules,
    isScheduleModalOpen,
    setIsScheduleModalOpen,
    scheduleProfile,
    schedName,
    setSchedName,
    schedExpression,
    setSchedExpression,
    schedTimezone,
    setSchedTimezone,
    schedEnabled,
    setSchedEnabled,
    previewNextRuns,
    previewError,
    fetchSchedules,
    handleOpenScheduleModal,
    handleSaveSchedule,
    handleDeleteSchedule,
    handleTriggerSchedule,
    _reset: () => {
      setSchedules([])
      setIsScheduleModalOpen(false)
      setScheduleProfile(null)
      setSchedName('')
      setSchedExpression('')
      setSchedTimezone('UTC')
      setSchedEnabled(true)
      setPreviewNextRuns([])
      setPreviewError(null)
    },
  } as const
}
