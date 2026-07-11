import { useState, useEffect, useCallback, useMemo } from 'react'
import type { Profile, Schedule } from '../types'
import { apiMutate } from './useApi'

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
  const [isSavingSchedule, setIsSavingSchedule] = useState(false)
  const [isTriggeringSchedule, setIsTriggeringSchedule] = useState(false)
  const [schedName, setSchedName] = useState('')
  const [schedExpression, setSchedExpression] = useState('')
  const [schedTimezone, setSchedTimezone] = useState('UTC')
  const [schedEnabled, setSchedEnabled] = useState(true)
  const [previewNextRuns, setPreviewNextRuns] = useState<string[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [scheduleLoadError, setScheduleLoadError] = useState(false)

  // ── fetch ──────────────────────────────────────────────────────────────

  const fetchSchedules = useCallback(async () => {
    setScheduleLoadError(false)
    try {
      const resp = await apiFetch('/schedules')
      if (!resp.ok) throw new Error(`Schedules request failed: ${resp.status}`)
      const data = await resp.json()
      setSchedules(data)
      setScheduleLoadError(false)
    } catch (err) {
      console.error('Error fetching schedules:', err)
      setSchedules([])
      setScheduleLoadError(true)
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
    if (!scheduleProfile || isSavingSchedule) return
    setIsSavingSchedule(true)
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
    } finally {
      setIsSavingSchedule(false)
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
    isSavingSchedule,
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
      setIsTriggeringSchedule(true)
      try {
        const resp = await apiMutate(
          apiFetch,
          `/schedules/${scheduleId}/trigger`,
          { method: 'POST' },
          'Failed to trigger schedule.',
        )
        return resp !== null
      } finally {
        setIsTriggeringSchedule(false)
      }
    },
    [apiFetch],
  )

  const _reset = useCallback(() => {
    setSchedules([])
    setIsScheduleModalOpen(false)
    setScheduleProfile(null)
    setSchedName('')
    setSchedExpression('')
    setSchedTimezone('UTC')
    setSchedEnabled(true)
    setPreviewNextRuns([])
    setPreviewError(null)
    setScheduleLoadError(false)
    setIsSavingSchedule(false)
    setIsTriggeringSchedule(false)
  }, [])

  return useMemo(
    () =>
      ({
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
        scheduleLoadError,
        isSavingSchedule,
        isTriggeringSchedule,
        fetchSchedules,
        handleOpenScheduleModal,
        handleSaveSchedule,
        handleDeleteSchedule,
        handleTriggerSchedule,
        _reset,
      }) as const,
    [
      schedules,
      isScheduleModalOpen,
      scheduleProfile,
      schedName,
      schedExpression,
      schedTimezone,
      schedEnabled,
      previewNextRuns,
      previewError,
      scheduleLoadError,
      isSavingSchedule,
      isTriggeringSchedule,
      fetchSchedules,
      handleOpenScheduleModal,
      handleSaveSchedule,
      handleDeleteSchedule,
      handleTriggerSchedule,
      _reset,
    ],
  )
}
