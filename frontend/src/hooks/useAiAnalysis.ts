import { useCallback, useState } from 'react'
import type { ApiFetch } from './useApi'

export interface NextStep {
  kind: string
  action: string
  reference: string | null
}

export interface FailureDiagnosis {
  category: string
  confidence: string
  summary: string
  evidence: string[]
  is_likely_regression: boolean
  next_steps: NextStep[]
}

export interface AiAnalysisState {
  diagnosis: FailureDiagnosis | null
  enabled: boolean
  detail: string | null
  loading: boolean
  error: boolean
}

const INITIAL: AiAnalysisState = {
  diagnosis: null,
  enabled: true,
  detail: null,
  loading: false,
  error: false,
}

/**
 * Manages the AI failure-diagnosis panel for one run: fetch the cached
 * diagnosis (GET), generate a fresh one on demand (POST). Non-streaming — the
 * backend returns a structured diagnosis, rendered as a card. Any transport or
 * non-ok response degrades to an error flag rather than throwing.
 */
export function useAiAnalysis(apiFetch: ApiFetch) {
  const [state, setState] = useState<AiAnalysisState>(INITIAL)

  const _apply = useCallback((body: Record<string, unknown>) => {
    setState({
      diagnosis: (body.diagnosis as FailureDiagnosis | null) ?? null,
      enabled: Boolean(body.enabled),
      detail: (body.detail as string | null) ?? null,
      loading: false,
      error: false,
    })
  }, [])

  const _run = useCallback(
    async (runId: string, opts: RequestInit | undefined) => {
      try {
        const resp = await apiFetch(`/runs/${runId}/ai-analysis`, opts)
        if (!resp.ok) {
          setState(s => ({ ...s, loading: false, error: true }))
          return
        }
        _apply(await resp.json())
      } catch {
        setState(s => ({ ...s, loading: false, error: true }))
      }
    },
    [apiFetch, _apply],
  )

  const load = useCallback(
    async (runId: string) => {
      setState({ ...INITIAL, loading: true })
      await _run(runId, undefined)
    },
    [_run],
  )

  const generate = useCallback(
    async (runId: string) => {
      setState(s => ({ ...s, loading: true, error: false }))
      await _run(runId, { method: 'POST' })
    },
    [_run],
  )

  const reset = useCallback(() => setState(INITIAL), [])

  return { ...state, load, generate, reset }
}
