import { useState, useEffect, useCallback, useRef } from 'react'
import { summarizeRuns } from '../runStats'
import type { Run } from '../types'
import { apiMutate } from './useApi'

interface UseRunsOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
  /** Only fetch / poll when true. */
  enabled: boolean
}

/**
 * Runs data layer — list, detail, SSE streaming, polling, lock toggle, and
 * derived aggregates.  Everything the dashboard needs about runs lives here.
 */
export function useRuns({ apiFetch, enabled }: UseRunsOpts) {
  const [runs, setRuns] = useState<Run[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedRunDetails, setSelectedRunDetails] = useState<Run | null>(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState(false)
  const [streamedStdout, setStreamedStdout] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)

  // ── fetch helpers ──────────────────────────────────────────────────────

  const fetchRuns = useCallback(async () => {
    try {
      const resp = await apiFetch('/runs')
      if (resp.ok) {
        const data = await resp.json()
        setRuns(data.runs)
      }
    } catch (err) {
      console.error('Error fetching runs:', err)
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  const fetchSelectedRunDetails = useCallback(
    async (runId: string) => {
      setDetailsLoading(true)
      setDetailsError(false)
      try {
        const resp = await apiFetch(`/runs/${runId}`)
        if (!resp.ok) throw new Error(`Run details request failed: ${resp.status}`)
        const data = await resp.json()
        setSelectedRunDetails(data)
        setDetailsError(false)
      } catch (err) {
        console.error('Error fetching run details:', err)
        setSelectedRunDetails((prev) => (prev?.id === runId ? null : prev))
        setDetailsError(true)
      } finally {
        setDetailsLoading(false)
      }
    },
    [apiFetch],
  )

  // ── lock toggle ────────────────────────────────────────────────────────

  const handleToggleLock = useCallback(
    async (runId: string, e: React.MouseEvent) => {
      e.stopPropagation()
      // BUG-9: read from ref to avoid closing over the entire runs array,
      // which changes every 1.5s poll cycle and causes unnecessary re-renders.
      const run = runsRef.current.find((r) => r.id === runId)
      if (!run) return
      const newLocked = !run.locked

      try {
        const resp = await apiFetch(`/runs/${runId}/lock`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ locked: newLocked }),
        })
        if (resp.ok) {
          setRuns((prev) =>
            prev.map((r) => (r.id === runId ? { ...r, locked: newLocked } : r)),
          )
          if (selectedRunDetailsRef.current?.id === runId) {
            setSelectedRunDetails((prev) =>
              prev ? { ...prev, locked: newLocked } : null,
            )
          }
        } else {
          const err = await resp.json()
          alert(err.detail || 'Failed to toggle lock status.')
        }
      } catch (err) {
        console.error('Error toggling lock:', err)
      }
    },
    [apiFetch],
  )

  // ── cancel a queued / running run ──────────────────────────────────────

  const handleCancelRun = useCallback(
    async (runId: string) => {
      const resp = await apiMutate(
        apiFetch,
        `/runs/${runId}/cancel`,
        { method: 'POST' },
        'Failed to cancel run.',
      )
      if (!resp) return
      const updated = await resp.json()
      setRuns((prev) =>
        prev.map((r) =>
          r.id === runId
            ? { ...r, status: updated.status, finished_at: updated.finished_at }
            : r,
        ),
      )
      setSelectedRunDetails((prev) =>
        prev && prev.id === runId
          ? { ...prev, status: updated.status, finished_at: updated.finished_at }
          : prev,
      )
    },
    [apiFetch],
  )

  // ── delete a run (metadata + artifacts) ────────────────────────────────

  const handleDeleteRun = useCallback(
    async (runId: string) => {
      const resp = await apiMutate(
        apiFetch,
        `/runs/${runId}`,
        { method: 'DELETE' },
        'Failed to delete run.',
      )
      if (!resp) return
      setRuns((prev) => prev.filter((r) => r.id !== runId))
      setSelectedRunId((prev) => (prev === runId ? null : prev))
      setSelectedRunDetails((prev) => (prev && prev.id === runId ? null : prev))
    },
    [apiFetch],
  )

  // ── re-run with the same parameters ────────────────────────────────────

  const handleRerunRun = useCallback(
    async (runId: string) => {
      const resp = await apiMutate(
        apiFetch,
        `/runs/${runId}/rerun`,
        { method: 'POST' },
        'Failed to re-run.',
      )
      if (resp) await fetchRuns()
    },
    [apiFetch, fetchRuns],
  )

  // ── SSE streaming ──────────────────────────────────────────────────────

  // Snapshot refs so the SSE effect doesn't depend on frequently-changing state.
  const runsRef = useRef(runs)
  const selectedRunIdRef = useRef(selectedRunId)
  const selectedRunDetailsRef = useRef(selectedRunDetails)

  useEffect(() => { runsRef.current = runs }, [runs])
  useEffect(() => { selectedRunIdRef.current = selectedRunId }, [selectedRunId])
  useEffect(() => {
    selectedRunDetailsRef.current = selectedRunDetails
  }, [selectedRunDetails])

  useEffect(() => {
    if (!selectedRunId) return
    let cancelled = false

    setSelectedRunDetails(null)
    setDetailsLoading(true)
    setDetailsError(false)
    apiFetch(`/runs/${selectedRunId}`)
      .then((resp) => {
        if (!resp.ok) throw new Error(`Run details request failed: ${resp.status}`)
        return resp.json()
      })
      .then((data) => {
        if (!cancelled) {
          setSelectedRunDetails(data)
          setDetailsError(false)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          console.error('Error fetching run details:', err)
          setSelectedRunDetails(null)
          setDetailsError(true)
        }
      })
      .finally(() => {
        if (!cancelled) setDetailsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedRunId, apiFetch])

  useEffect(() => {
    if (!selectedRunId) {
      setStreamedStdout('')
      setIsStreaming(false)
      return
    }

    const findRun = (): Run | null | undefined =>
      runsRef.current.find((r) => r.id === selectedRunId) ||
      (selectedRunDetailsRef.current?.id === selectedRunId
        ? selectedRunDetailsRef.current
        : null)
    const isActive = (r: Run | null | undefined): boolean =>
      r?.status === 'running' || r?.status === 'queued'

    if (!isActive(findRun())) {
      setIsStreaming(false)
      return
    }

    setStreamedStdout('')
    setIsStreaming(true)

    let eventSource: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectAttempts = 0
    let disposed = false

    const connect = () => {
      if (disposed) return
      setStreamedStdout('')  // BUG-24: clear old content on reconnect to avoid duplicate lines
      eventSource = new EventSource(`/runs/${selectedRunId}/stream`)

      eventSource.onopen = () => {
        reconnectAttempts = 0
      }

      eventSource.onmessage = (event) => {
        setStreamedStdout((prev) => prev + event.data + '\n')
      }

      eventSource.onerror = () => {
        eventSource?.close()
        eventSource = null
        if (disposed) return

        if (!isActive(findRun())) {
          setIsStreaming(false)
          fetchSelectedRunDetails(selectedRunId)
          return
        }

        const delay = Math.min(1000 * 2 ** reconnectAttempts, 15000)
        reconnectAttempts += 1
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      disposed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      eventSource?.close()
      setIsStreaming(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRunId, fetchSelectedRunDetails])

  // ── transition fetch (active → inactive) ───────────────────────────────

  const lastStatusRef = useRef<string | null>(null)
  useEffect(() => {
    if (!selectedRunId) {
      lastStatusRef.current = null
      return
    }
    const shallowRun = runs.find((r) => r.id === selectedRunId)
    const selectedDetails =
      selectedRunDetails?.id === selectedRunId ? selectedRunDetails : null
    const currentStatus =
      shallowRun?.status || selectedDetails?.status || null
    const wasActive =
      lastStatusRef.current === 'running' || lastStatusRef.current === 'queued'
    const isInactive =
      currentStatus && currentStatus !== 'running' && currentStatus !== 'queued'

    if (wasActive && isInactive) {
      fetchSelectedRunDetails(selectedRunId)
      fetchRuns()
    }
    lastStatusRef.current = currentStatus
  }, [selectedRunId, runs, selectedRunDetails?.status, fetchSelectedRunDetails, fetchRuns])

  // ── polling ────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!enabled) return

    const interval = setInterval(() => {
      const currentRuns = runsRef.current
      const currentSelectedId = selectedRunIdRef.current
      const currentDetails = selectedRunDetailsRef.current

      const hasActiveRuns = currentRuns.some(
        (r) => r.status === 'queued' || r.status === 'running',
      )
      const activeSelectedRun =
        currentRuns.find((r) => r.id === currentSelectedId) ||
        (currentDetails?.id === currentSelectedId ? currentDetails : null)
      const isSelectedActive =
        activeSelectedRun &&
        (activeSelectedRun.status === 'queued' ||
          activeSelectedRun.status === 'running')

      if (hasActiveRuns || isSelectedActive) {
        fetchRuns()
        if (currentSelectedId && isSelectedActive) {
          fetchSelectedRunDetails(currentSelectedId)
        }
      }
    }, 1500)

    return () => clearInterval(interval)
  }, [enabled, fetchRuns, fetchSelectedRunDetails])

  // ── initial load ───────────────────────────────────────────────────────

  useEffect(() => {
    if (enabled) {
      fetchRuns()
    }
  }, [enabled, fetchRuns])

  // ── derived ────────────────────────────────────────────────────────────

  const selectedRun =
    (selectedRunDetails?.id === selectedRunId ? selectedRunDetails : null) ||
    runs.find((r) => r.id === selectedRunId) ||
    null

  const completedRuns = runs.filter((r) => r.status === 'completed')
  const runStats = summarizeRuns(runs)

  // ── close drawer ───────────────────────────────────────────────────────

  const closeDrawer = useCallback(() => {
    setSelectedRunId(null)
    setSelectedRunDetails(null)
    setDetailsError(false)
    setStreamedStdout('')
    setIsStreaming(false)
  }, [])

  return {
    // state
    runs,
    loading,
    selectedRunId,
    setSelectedRunId,
    selectedRunDetails,
    closeDrawer,
    detailsLoading,
    detailsError,
    streamedStdout,
    isStreaming,
    selectedRun,
    // actions
    fetchRuns,
    fetchSelectedRunDetails,
    handleToggleLock,
    handleCancelRun,
    handleDeleteRun,
    handleRerunRun,
    // derived
    totalRuns: runStats.totalRuns,
    completedRuns,
    overallSuccessRate: runStats.overallSuccessRate,
    activeRunsCount: runStats.activeRunsCount,
    failedRunsCount: runStats.failedRunsCount,
    manualRunsCount: runStats.manualRunsCount,
    scheduledRunsCount: runStats.scheduledRunsCount,
    passedTestCases: runStats.passedTestCases,
    failedTestCases: runStats.failedTestCases,
    totalTestCases: runStats.totalTestCases,
    // reset
    _reset: () => {
      setRuns([])
      setLoading(true)
      setSelectedRunId(null)
      setSelectedRunDetails(null)
      setDetailsLoading(false)
      setDetailsError(false)
      setStreamedStdout('')
      setIsStreaming(false)
    },
  } as const
}
