import { ChevronDown, ChevronRight, Shuffle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { caseCells, type CaseTone } from '../runCaseHistory'
import { useDashboard } from '../hooks/DashboardContext'
import type { CaseHistory, TestCaseResult } from '../types'

const CASE_TONE_COLOR: Record<CaseTone, string> = {
  pass: '#10b981',
  fail: '#ef4444',
  error: '#b91c1c',
  skip: '#64748b',
}

/** One case row inside the Diff tab. Clicking it lazily pulls the case's
 *  cross-run outcome history (/cases/history) and paints it as a strip of
 *  coloured cells (oldest→newest); a server-computed flaky verdict shows as a
 *  badge that sticks once loaded. History is fetched at most once per mount on
 *  success; a failed fetch retries on the next expand. */
export function CaseRow({ caseResult }: { caseResult: TestCaseResult }) {
  const d = useDashboard()
  const [expanded, setExpanded] = useState(false)
  const [history, setHistory] = useState<CaseHistory | null>(null)
  const [loading, setLoading] = useState(false)
  const [historyError, setHistoryError] = useState(false)
  const c = caseResult
  const testsPath = d.runs.selectedRun?.tests_path
  const profileId = d.runs.selectedRun?.profile_id
  const label = c.suite ? `${c.suite}::${c.name}` : c.name

  // This fetch is triggered from a click handler, not a useEffect, so there's
  // no cleanup callback to flip a `cancelled` flag (the pattern this file's
  // other fetches use) — track mount state instead so a case collapsed/its
  // row unmounted before the response lands doesn't update stale state.
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const toggle = () => {
    const next = !expanded
    setExpanded(next)
    if (next && history === null && !loading && testsPath) {
      setLoading(true)
      setHistoryError(false)
      const qs = new URLSearchParams({ tests_path: testsPath, suite: c.suite, name: c.name })
      if (profileId) qs.set('profile_id', profileId)
      d.apiFetch(`/cases/history?${qs.toString()}`)
        .then((r) => {
          if (!r.ok) throw new Error(`Case history request failed: ${r.status}`)
          return r.json()
        })
        .then((data: CaseHistory | null) => {
          if (!mountedRef.current) return
          setHistory(data)
          setHistoryError(false)
        })
        .catch(() => {
          if (!mountedRef.current) return
          setHistory(null)
          setHistoryError(true)
        })
        .finally(() => {
          if (mountedRef.current) setLoading(false)
        })
    }
  }

  const cells = history ? caseCells(history.points) : []

  return (
    <div style={{ fontSize: '0.8rem' }}>
      <button
        type="button"
        onClick={toggle}
        data-testid="diff-case-toggle"
        aria-expanded={expanded}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.3rem', width: '100%',
          padding: 0, background: 'transparent', border: 'none', cursor: 'pointer',
          color: 'inherit', textAlign: 'left', font: 'inherit',
        }}
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <code>{label}</code>
        {history?.flaky && (
          <span
            data-testid="flaky-badge"
            title={d.t('flakyTooltip')}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '2px',
              padding: '0 0.35rem', borderRadius: 8, fontSize: '0.65rem', fontWeight: 600,
              color: '#f59e0b', border: '1px solid #f59e0b',
            }}
          >
            <Shuffle size={10} />{d.t('flakyBadge')}
          </span>
        )}
      </button>
      {c.message && (
        <div style={{ opacity: 0.6, fontSize: '0.75rem', whiteSpace: 'pre-wrap', paddingLeft: '1rem' }}>{c.message}</div>
      )}
      {expanded && (
        <div data-testid="case-history" style={{ marginTop: '0.35rem', paddingLeft: '1rem' }}>
          {loading ? (
            <span style={{ opacity: 0.6 }}>{d.t('caseHistoryLoading')}</span>
          ) : historyError ? (
            <span data-testid="case-history-error" style={{ opacity: 0.75, color: '#f59e0b' }}>
              {d.t('caseHistoryLoadError')}
            </span>
          ) : !history || cells.length === 0 ? (
            <span style={{ opacity: 0.6 }}>{d.t('caseHistoryEmpty')}</span>
          ) : (
            <>
              <div style={{ opacity: 0.6, fontSize: '0.7rem', marginBottom: '0.25rem' }}>{d.t('caseHistoryHint')}</div>
              <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap' }}>
                {cells.map((cell, i) => (
                  <span
                    key={i}
                    title={`${cell.status} · ${new Date(cell.at).toLocaleString()}`}
                    style={{ width: 12, height: 12, borderRadius: 2, background: CASE_TONE_COLOR[cell.tone], display: 'inline-block' }}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
