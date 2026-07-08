import {
  Activity, AlertTriangle, Ban, BarChart3, Box, Check, CheckCircle2, ChevronDown, ChevronRight,
  Clock, Copy, Download, ExternalLink, GitCompare, Maximize2, Minus, Plus, RotateCw, Shuffle,
  Sparkles, Terminal, Trash2, X, XCircle,
} from 'lucide-react'
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { SideSheet, Tabs } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import { formatDuration } from '../logUtils'
import { diffBuckets, diffIsEmpty, type DiffTone } from '../runDiff'
import { caseCells, type CaseTone } from '../runCaseHistory'
import { useDashboard } from '../hooks/DashboardContext'
import { useDialogA11y } from '../hooks/useDialogA11y'
import type { TranslationKey } from '../i18n'
import type { CaseHistory, RunArtifact, RunDiff, TestCaseResult } from '../types'
import { AiInsightsTab } from './AiInsightsTab'

const DIFF_TONE_COLOR: Record<DiffTone, string> = {
  danger: '#ef4444',
  warning: '#f59e0b',
  success: '#10b981',
  info: '#3b82f6',
  muted: '#64748b',
}

const DIFF_TONE_ICON: Record<DiffTone, ReactNode> = {
  danger: <XCircle size={14} />,
  warning: <AlertTriangle size={14} />,
  success: <CheckCircle2 size={14} />,
  info: <Plus size={14} />,
  muted: <Minus size={14} />,
}

const DIFF_BUCKET_LABEL = {
  new_failures: 'diffNewFailures',
  still_failing: 'diffStillFailing',
  fixed: 'diffFixed',
  new_cases: 'diffNewCases',
  removed_cases: 'diffRemovedCases',
} as const

const CASE_TONE_COLOR: Record<CaseTone, string> = {
  pass: '#10b981',
  fail: '#ef4444',
  error: '#b91c1c',
  skip: '#64748b',
}

const CASE_STATUS_COLOR: Record<TestCaseResult['status'], string> = {
  passed: '#10b981',
  failed: '#ef4444',
  error: '#b91c1c',
  skipped: '#64748b',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes % 1024 === 0 ? 0 : 1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

type ArtifactGroupKey = 'trace' | 'screenshot' | 'video' | 'other'

const ARTIFACT_GROUPS: Array<{ key: ArtifactGroupKey; labelKey: TranslationKey }> = [
  { key: 'trace', labelKey: 'artifactGroupTrace' },
  { key: 'screenshot', labelKey: 'artifactGroupScreenshot' },
  { key: 'video', labelKey: 'artifactGroupVideo' },
  { key: 'other', labelKey: 'artifactGroupOther' },
]

const ARTIFACT_GROUP_ORDER: Record<ArtifactGroupKey, number> = {
  trace: 0,
  screenshot: 1,
  video: 2,
  other: 3,
}

function artifactGroupKey(artifact: RunArtifact): ArtifactGroupKey {
  const path = artifact.path.toLowerCase()
  const contentType = artifact.content_type.toLowerCase()
  if (path.endsWith('trace.zip') || path.includes('/trace.')) return 'trace'
  if (
    contentType.startsWith('image/') ||
    /\.(png|jpe?g|webp)$/.test(path)
  ) return 'screenshot'
  if (contentType.startsWith('video/') || /\.(webm|mp4)$/.test(path)) return 'video'
  return 'other'
}

function groupRunArtifacts(artifacts: RunArtifact[]) {
  const sorted = artifacts
    .map((artifact) => ({ artifact, group: artifactGroupKey(artifact) }))
    .sort((a, b) => {
      const groupDelta = ARTIFACT_GROUP_ORDER[a.group] - ARTIFACT_GROUP_ORDER[b.group]
      if (groupDelta !== 0) return groupDelta
      return a.artifact.path.localeCompare(b.artifact.path)
    })

  return ARTIFACT_GROUPS.map((group) => ({
    ...group,
    items: sorted
      .map((item, index) => ({ ...item, index }))
      .filter((item) => item.group === group.key),
  })).filter((group) => group.items.length > 0)
}

function traceViewerCommand(artifact: RunArtifact): string {
  return `npx playwright show-trace ${artifact.path}`
}

/** One case row inside the Diff tab. Clicking it lazily pulls the case's
 *  cross-run outcome history (/cases/history) and paints it as a strip of
 *  coloured cells (oldest→newest); a server-computed flaky verdict shows as a
 *  badge that sticks once loaded. History is fetched at most once per mount on
 *  success; a failed fetch retries on the next expand. */
function CaseRow({ caseResult }: { caseResult: TestCaseResult }) {
  const d = useDashboard()
  const [expanded, setExpanded] = useState(false)
  const [history, setHistory] = useState<CaseHistory | null>(null)
  const [loading, setLoading] = useState(false)
  const [historyError, setHistoryError] = useState(false)
  const c = caseResult
  const testsPath = d.runs.selectedRun?.tests_path
  const profileId = d.runs.selectedRun?.profile_id
  const label = c.suite ? `${c.suite}::${c.name}` : c.name

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
          setHistory(data)
          setHistoryError(false)
        })
        .catch(() => {
          setHistory(null)
          setHistoryError(true)
        })
        .finally(() => setLoading(false))
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

/** Shared outline style for the drawer's header action buttons (cancel / re-run
 *  / delete) — they differ only by colour. */
const actionBtnStyle = (color: string): CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.4rem',
  padding: '0.35rem 0.75rem',
  cursor: 'pointer',
  background: 'transparent',
  color,
  border: `1px solid ${color}`,
  borderRadius: '4px',
  fontSize: '0.85rem',
})

/** Slide-in run-details drawer: status badge, info matrix, and a logs/report tab
 *  switch. Logs tab = console terminal (search/level/font/height/fullscreen
 *  controls + line-numbered log body) plus pytest args and error stacktrace;
 *  report tab = outcome breakdown chart and the inline Allure report iframe.
 *  All state lives in App (terminal prefs via useTerminalView); this displays. */
export function RunDetailsDrawer() {
  const d = useDashboard()
  const onClose = () => d.runs.closeDrawer()
  const dialogRef = useDialogA11y({ isOpen: !!d.runs.selectedRun, onClose })
  const [runDiff, setRunDiff] = useState<RunDiff | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [diffError, setDiffError] = useState(false)
  const [runArtifacts, setRunArtifacts] = useState<RunArtifact[]>([])
  const [artifactsLoading, setArtifactsLoading] = useState(false)
  const [artifactsError, setArtifactsError] = useState(false)
  const [artifactsForbidden, setArtifactsForbidden] = useState(false)
  const [artifactsLoaded, setArtifactsLoaded] = useState(false)

  // Fetch Playwright runner artifacts lazily — only when the Report tab is
  // active for a Playwright run. The cancel flag drops a stale response if the
  // user switches run/tab mid-flight.
  const selectedRunId = d.runs.selectedRun?.id
  const selectedRunner = d.runs.selectedRun?.runner
  const closeDrawer = d.runs.closeDrawer
  useEffect(() => {
    if (d.terminal.drawerTab !== 'report' || !selectedRunId || selectedRunner !== 'playwright') {
      setRunArtifacts([])
      setArtifactsLoading(false)
      setArtifactsError(false)
      setArtifactsForbidden(false)
      setArtifactsLoaded(false)
      return
    }

    let cancelled = false
    setArtifactsLoading(true)
    setArtifactsError(false)
    setArtifactsForbidden(false)
    setArtifactsLoaded(false)
    setRunArtifacts([])
    d.apiFetch(`/runs/${selectedRunId}/artifacts`)
      .then(async (r) => {
        if (r.status === 403) {
          return { kind: 'forbidden' as const, data: null }
        }
        if (!r.ok) throw new Error(`Artifacts request failed: ${r.status}`)
        return {
          kind: 'ok' as const,
          data: await r.json() as { artifacts?: RunArtifact[] } | null,
        }
      })
      .then((result) => {
        if (!cancelled) {
          if (result.kind === 'forbidden') {
            setRunArtifacts([])
            setArtifactsForbidden(true)
            setArtifactsLoaded(false)
          } else {
            setRunArtifacts(result.data?.artifacts ?? [])
            setArtifactsLoaded(true)
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRunArtifacts([])
          setArtifactsError(true)
          setArtifactsForbidden(false)
          setArtifactsLoaded(false)
        }
      })
      .finally(() => {
        if (!cancelled) setArtifactsLoading(false)
      })
    return () => { cancelled = true }
  }, [d.apiFetch, d.terminal.drawerTab, selectedRunId, selectedRunner])

  useEffect(() => {
    if (d.terminal.drawerTab !== 'diff' || !selectedRunId) return
    let cancelled = false
    setDiffLoading(true)
    setDiffError(false)
    setRunDiff(null)
    d.apiFetch(`/runs/${selectedRunId}/diff`)
      .then((r) => {
        if (!r.ok) throw new Error(`Diff request failed: ${r.status}`)
        return r.json()
      })
      .then((data) => { if (!cancelled) setRunDiff(data) })
      .catch(() => {
        if (!cancelled) {
          setRunDiff(null)
          setDiffError(true)
        }
      })
      .finally(() => { if (!cancelled) setDiffLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.terminal.drawerTab, selectedRunId])

  useEffect(() => {
    if (!selectedRunId) return
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (d.terminal.isTerminalFullscreen || d.terminal.isReportFullscreen) return
      event.preventDefault()
      closeDrawer()
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [
    closeDrawer,
    selectedRunId,
    d.terminal.isReportFullscreen,
    d.terminal.isTerminalFullscreen,
  ])

  // ── Derived filtered logs ──────────────────────────────────────────────
  const filteredStdout = d.runs.selectedRun?.stdout
    ? d.terminal.getFilteredLogs(d.runs.selectedRun.stdout)
    : ''
  const filteredStderr = d.runs.selectedRun?.stderr
    ? d.terminal.getFilteredLogs(d.runs.selectedRun.stderr)
    : ''
  const filteredStreamed = d.runs.streamedStdout
    ? d.terminal.getFilteredLogs(d.runs.streamedStdout)
    : ''

  // ── Inline helpers ─────────────────────────────────────────────────────
  const formatDate = (isoStr: string | null) => {
    if (!isoStr) return '-'
    return new Date(isoStr).toLocaleString()
  }
  const caseResults = d.runs.selectedRun?.cases ?? []
  const artifactGroups = groupRunArtifacts(runArtifacts)
  const showArtifactsSection =
    selectedRunner === 'playwright' &&
    (artifactsLoading || artifactsError || artifactsForbidden || artifactsLoaded ||
      runArtifacts.length > 0)

  const downloadLogs = (runId: string) => {
    const logText = d.runs.isStreaming
      ? d.runs.streamedStdout
      : (d.runs.selectedRun?.stdout || '') + '\n' + (d.runs.selectedRun?.stderr || '')
    const blob = new Blob([logText], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `run_${runId}_execution.log`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  return (
    <SideSheet
      visible={!!d.runs.selectedRun}
      onCancel={() => {
        d.runs.closeDrawer()
      }}
      width="680px"
      title={
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: '1rem' }}>
          <div className={styles.drawerTitleGroup}>
            <h3 id="run-details-title" style={{ margin: 0 }}>{d.t('executionDetails')}</h3>
            <code>{d.t('id')}: {d.runs.selectedRun?.id}</code>
          </div>
          {(d.runs.selectedRun?.status === 'running' ||
            d.runs.selectedRun?.status === 'queued') && (
            <button
              type="button"
              onClick={() => {
                if (
                  window.confirm(
                    d.lang === 'zh' ? '确定要取消这个运行吗？' : 'Cancel this run?',
                  )
                ) {
                  d.runs.handleCancelRun(d.runs.selectedRun!.id)
                }
              }}
              style={{ ...actionBtnStyle('var(--semi-color-danger)'), marginRight: '2.5rem' }}
            >
              <Ban size={15} />
              {d.lang === 'zh' ? '终止运行' : 'Cancel Run'}
            </button>
          )}
          {d.runs.selectedRun &&
            d.runs.selectedRun.status !== 'running' &&
            d.runs.selectedRun.status !== 'queued' && (
            <div style={{ display: 'flex', gap: '0.5rem', marginRight: '2.5rem' }}>
              <button
                type="button"
                onClick={() => d.runs.handleRerunRun(d.runs.selectedRun!.id)}
                style={actionBtnStyle('var(--semi-color-primary)')}
              >
                <RotateCw size={15} />
                {d.lang === 'zh' ? '重新运行' : 'Re-run'}
              </button>
              {!d.runs.selectedRun.locked && (
                <button
                  type="button"
                  onClick={() => {
                    if (
                      window.confirm(
                        d.lang === 'zh'
                          ? '确定删除此运行及其产物吗？此操作不可撤销。'
                          : 'Delete this run and its artifacts? This cannot be undone.',
                      )
                    ) {
                      d.runs.handleDeleteRun(d.runs.selectedRun!.id)
                    }
                  }}
                  style={actionBtnStyle('var(--semi-color-danger)')}
                >
                  <Trash2 size={15} />
                  {d.lang === 'zh' ? '删除运行' : 'Delete Run'}
                </button>
              )}
            </div>
          )}
        </div>
      }
      closable={true}
      closeIcon={(
        <span data-testid="run-details-close" style={{ display: 'inline-flex' }} aria-hidden="true">
          <X size={16} />
        </span>
      )}
      bodyStyle={{ padding: '16px' }}
    >
      <div ref={dialogRef} tabIndex={-1}>
      {d.runs.selectedRun && (
        <div className={styles.drawerContent} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            {/* Top Big Status Badge */}
            <div className={styles.drawerStatusSection}>
              <span className={`${styles.drawerBadge} ${
                d.runs.selectedRun.status === 'completed'
                  ? (d.runs.selectedRun.passed ? styles.badge_completed : styles.badge_completed_failed)
                  : styles[`badge_${d.runs.selectedRun.status}`]
              }`}>
                {d.runs.selectedRun.status === 'queued' && <RotateCw size={16} className={styles.spinIcon} />}
                {d.runs.selectedRun.status === 'running' && <Activity size={16} className={styles.pulseIcon} />}
                {d.runs.selectedRun.status === 'completed' && d.runs.selectedRun.passed && <CheckCircle2 size={16} />}
                {d.runs.selectedRun.status === 'completed' && !d.runs.selectedRun.passed && <XCircle size={16} />}
                {d.runs.selectedRun.status === 'failed' && <XCircle size={16} />}
                {d.runs.selectedRun.status === 'timeout' && <Clock size={16} />}
                <span>{d.t(`status_${d.runs.selectedRun.status}`).toUpperCase()}</span>
              </span>
              {d.runs.selectedRun.status === 'completed' && (
                <span className={d.runs.selectedRun.passed ? styles.textSuccessGlow : styles.textDangerGlow}>
                  {d.runs.selectedRun.passed ? d.t('allTestsPassed') : d.t('suiteFailed')}
                </span>
              )}
            </div>

            {/* Info Matrix grid */}
            <div className={styles.infoGrid}>
              <div className={styles.infoItem}>
                <span className={styles.infoLabel}>{d.t('runner')}</span>
                <span className={styles.infoValue}>{d.runs.selectedRun.runner}</span>
              </div>
              <div className={styles.infoItem}>
                <span className={styles.infoLabel}>{d.t('exitCode')}</span>
                <span className={styles.infoValue}>
                  {d.runs.selectedRun.exit_code !== null ? d.runs.selectedRun.exit_code : '-'}
                </span>
              </div>
              <div className={styles.infoItem}>
                <span className={styles.infoLabel}>{d.t('triggeredBy')}</span>
                <span className={`${styles.ownerBadge} ${
                  d.runs.selectedRun.created_by === 'system' ? styles.ownerBadge_system :
                  d.runs.selectedRun.created_by === 'admin' ? styles.ownerBadge_admin : styles.ownerBadge_user
                }`} style={{ marginTop: '0.15rem', alignSelf: 'flex-start' }}>
                  {d.runs.selectedRun.created_by}
                </span>
              </div>
              <div className={styles.infoItem}>
                <span className={styles.infoLabel}>{d.t('environment')}</span>
                <span className={`${styles.engineBadge} ${styles.engineBadge_docker}`}
                  style={{ marginTop: '0.15rem', alignSelf: 'flex-start' }}>
                  <Box size={12} className={styles.inlineIcon} />
                  <span>{d.t('engine_docker')}</span>
                </span>
              </div>
              <div className={styles.infoItem}>
                <span className={styles.infoLabel}>{d.t('created')}</span>
                <span className={styles.infoValue}>{formatDate(d.runs.selectedRun.created_at)}</span>
              </div>
              <div className={styles.infoItem}>
                <span className={styles.infoLabel}>{d.t('finished')}</span>
                <span className={styles.infoValue}>{formatDate(d.runs.selectedRun.finished_at)}</span>
              </div>
            </div>

            {/* Tab Selector Segmented Control */}
            <Tabs activeKey={d.terminal.drawerTab} onChange={key => d.terminal.setDrawerTab(key as any)} style={{ marginBottom: '1rem' }}>
              <Tabs.TabPane
                itemKey="logs"
                tab={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }} data-testid="drawer-tab-logs">
                    <Terminal size={14} />
                    <span>{d.t('consoleLogs')}</span>
                  </span>
                }
              />
              <Tabs.TabPane
                itemKey="report"
                tab={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }} data-testid="drawer-tab-report">
                    <BarChart3 size={14} />
                    <span>{d.t('testReport')}</span>
                  </span>
                }
              />
              <Tabs.TabPane
                itemKey="diff"
                tab={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }} data-testid="drawer-tab-diff">
                    <GitCompare size={14} />
                    <span>{d.t('diffTab')}</span>
                  </span>
                }
              />
              <Tabs.TabPane
                itemKey="ai-insights"
                tab={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }} data-testid="drawer-tab-ai">
                    <Sparkles size={14} />
                    <span>{d.t('aiTab')}</span>
                  </span>
                }
              />
            </Tabs>

            {d.runs.detailsError && (
              <div className={styles.summaryPlaceholder} data-testid="run-details-error" role="alert">
                <AlertTriangle size={28} />
                <p>{d.t('runDetailsLoadError')}</p>
                <span>{d.t('runDetailsLoadErrorDesc')}</span>
              </div>
            )}

            {d.terminal.drawerTab === 'logs' && (
              <>
                {/* Terminal Console Logs */}
                <div className={styles.terminalSection}>
                  <div className={styles.terminalHeader}>
                    <div className={styles.terminalTitleGroup}>
                      <Terminal size={14} className={styles.terminalHeaderIcon} />
                      <h4>{d.t('consoleLogs')}</h4>
                    </div>
                      
                    <div className={styles.terminalHeaderControls}>
                      {/* Live Streaming indicator */}
                      {(d.runs.selectedRun.status === 'running' || d.runs.selectedRun.status === 'queued') && (
                        <span className={d.runs.isStreaming ? styles.livePulse : styles.streamingIndicator}>
                          {!d.runs.isStreaming && <span className={styles.streamingDot}></span>}
                          {d.runs.isStreaming ? (d.lang === 'zh' ? '实时' : 'LIVE') : d.t('streaming')}
                        </span>
                      )}

                      {/* Copy Button (Icon Only) */}
                      <button 
                        className={styles.terminalIconOnlyButton}
                        onClick={() => {
                          const logsText = d.runs.isStreaming 
                            ? d.runs.streamedStdout 
                            : (d.runs.selectedRun?.stdout || '') + '\n' + (d.runs.selectedRun?.stderr || '');
                          d.terminal.copyToClipboard(logsText);
                        }}
                        title={d.terminal.copySuccess ? d.t('copied') : d.t('copy')}
                        aria-label={d.t('copy')}
                      >
                        {d.terminal.copySuccess ? <Check size={14} style={{ color: '#10b981' }} /> : <Copy size={14} />}
                      </button>

                      {/* Download Button (Icon Only) */}
                      <button 
                        className={styles.terminalIconOnlyButton}
                        onClick={() => downloadLogs(d.runs.selectedRun!.id)}
                        title={d.lang === 'zh' ? '下载完整日志' : 'Download raw log file'}
                        aria-label={d.t('download')}
                      >
                        <Download size={14} />
                      </button>

                      {/* Fullscreen toggle button */}
                      <button 
                        className={styles.terminalFullscreenButton}
                        onClick={(event) => {
                          event.currentTarget.focus()
                          d.terminal.setIsTerminalFullscreen(true)
                        }}
                        title={d.lang === 'zh' ? "全屏终端" : "Fullscreen Terminal"}
                        data-testid="terminal-fullscreen-button"
                      >
                        <Maximize2 size={13} />
                        <span>{d.lang === 'zh' ? "全屏终端" : "Fullscreen"}</span>
                      </button>
                    </div>
                  </div>
                  <div 
                    className={`${styles.terminalBlock} ${d.terminal.isTerminalHeightExpanded ? styles.terminalBlockExpanded : ''}`} 
                    ref={d.terminalRef}
                    style={{ fontSize: `${d.terminal.terminalFontSize}px` }}
                  >
                    {d.runs.detailsError ? (
                      <span className={styles.terminalPlaceholder}>
                        {d.t('runDetailsLoadError')}
                      </span>
                    ) : d.runs.isStreaming ? (
                      filteredStreamed ? (
                        <pre className={styles.stdoutPre}>{d.terminal.renderFormattedLogs(filteredStreamed)}</pre>
                      ) : d.runs.streamedStdout ? (
                        <span className={styles.terminalPlaceholder}>
                          {d.lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                        </span>
                      ) : (
                        <span className={styles.terminalPlaceholder}>
                          <span className={styles.waitingLogs}>
                            <span className={styles.pulsingText}>{d.t('waitingLogs')}</span>
                          </span>
                        </span>
                      )
                    ) : (d.runs.selectedRun.stdout || d.runs.selectedRun.stderr || d.runs.streamedStdout) ? (
                      (filteredStdout || filteredStderr || filteredStreamed) ? (
                        <>
                          {(filteredStdout || (d.runs.selectedRunDetails ? null : filteredStreamed)) && (
                            <pre className={styles.stdoutPre}>
                              {d.terminal.renderFormattedLogs(filteredStdout || filteredStreamed)}
                            </pre>
                          )}
                          {filteredStderr && <pre className={styles.stderrPre}>{d.terminal.renderFormattedLogs(filteredStderr)}</pre>}
                        </>
                      ) : (
                        <span className={styles.terminalPlaceholder}>
                          {d.lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                        </span>
                      )
                    ) : d.runs.detailsLoading ? (
                      <span className={styles.terminalPlaceholder}>
                        <span className={styles.waitingLogs}>
                          <span className={styles.pulsingText}>{d.lang === 'zh' ? '正在加载控制台日志...' : 'Loading console logs...'}</span>
                        </span>
                      </span>
                    ) : (
                      <span className={styles.terminalPlaceholder}>
                        {d.t('noLogsAvailable')}
                      </span>
                    )}
                  </div>
                </div>

                {/* Arguments box */}
                {d.runs.selectedRun.args && d.runs.selectedRun.args.length > 0 && (
                  <div className={styles.argsBlock}>
                    <h4>{d.t('pytestArguments')}</h4>
                    <div className={styles.argsWrapper}>
                      {d.runs.selectedRun.args.map((arg: string, idx: number) => (
                        <code key={idx} className={styles.argBadge}>{arg}</code>
                      ))}
                    </div>
                  </div>
                )}

                {/* Error logs container */}
                {d.runs.selectedRun.error && (
                  <div className={styles.errorLogSection}>
                    <div className={styles.errorLogHeader}>
                      <h4>{d.t('executionStacktrace')}</h4>
                      <button 
                        className={styles.copyButton}
                        onClick={() => d.terminal.copyToClipboard(d.runs.selectedRun?.error || '')}
                      >
                        {d.terminal.copySuccess ? <Check size={14} style={{ color: '#10b981' }} /> : <Copy size={14} />}
                        <span>{d.terminal.copySuccess ? d.t('copied') : d.t('copy')}</span>
                      </button>
                    </div>
                    <pre className={styles.errorText}>
                      <code>{d.runs.selectedRun.error}</code>
                    </pre>
                  </div>
                )}
              </>
            )}

            {d.terminal.drawerTab === 'report' && (
              <>
                {/* Dynamic summary chart block */}
                <section data-testid="report-section-summary" aria-label={d.t('testOutcomes')}>
                  <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.9rem' }}>{d.t('testOutcomes')}</h4>
                  {d.runs.selectedRun.summary ? (
                    <div className={styles.compactSummaryBar}>
                      <div className={styles.compactSummaryMetrics}>
                        <span className={`${styles.compactMetric} ${styles.compactMetricPassed}`}>
                          <CheckCircle2 size={12} />
                          <span>{d.t('passed')}:</span>
                          <strong>{d.runs.selectedRun.summary.passed}</strong>
                        </span>
                        <span className={`${styles.compactMetric} ${styles.compactMetricFailed}`}>
                          <XCircle size={12} />
                          <span>{d.t('failed')}:</span>
                          <strong>{d.runs.selectedRun.summary.failed}</strong>
                        </span>
                        <span className={`${styles.compactMetric} ${styles.compactMetricError}`}>
                          <AlertTriangle size={12} />
                          <span>{d.t('error')}:</span>
                          <strong>{d.runs.selectedRun.summary.error}</strong>
                        </span>
                        <span className={`${styles.compactMetric} ${styles.compactMetricSkipped}`}>
                          <Clock size={12} />
                          <span>{d.t('skipped')}:</span>
                          <strong>{d.runs.selectedRun.summary.skipped}</strong>
                        </span>
                        <span className={styles.compactMetricDuration}>
                          <Clock size={12} />
                          <span>{d.t('durationLabel')}: {formatDuration(d.runs.selectedRun.summary.duration_ms)}</span>
                        </span>
                      </div>

                      {/* Multi-segmented single bar chart */}
                      <div className={styles.compactSegmentBar}>
                        {d.runs.selectedRun.summary.passed > 0 && (
                          <div
                            className={styles.segmentPassed}
                            style={{ width: `${(d.runs.selectedRun.summary.passed / d.runs.selectedRun.summary.total) * 100}%` }}
                            title={`${d.t('passed')}: ${d.runs.selectedRun.summary.passed}`}
                          ></div>
                        )}
                        {d.runs.selectedRun.summary.failed > 0 && (
                          <div
                            className={styles.segmentFailed}
                            style={{ width: `${(d.runs.selectedRun.summary.failed / d.runs.selectedRun.summary.total) * 100}%` }}
                            title={`${d.t('failed')}: ${d.runs.selectedRun.summary.failed}`}
                          ></div>
                        )}
                        {d.runs.selectedRun.summary.error > 0 && (
                          <div
                            className={styles.segmentError}
                            style={{ width: `${(d.runs.selectedRun.summary.error / d.runs.selectedRun.summary.total) * 100}%` }}
                            title={`${d.t('error')}: ${d.runs.selectedRun.summary.error}`}
                          ></div>
                        )}
                        {d.runs.selectedRun.summary.skipped > 0 && (
                          <div
                            className={styles.segmentSkipped}
                            style={{ width: `${(d.runs.selectedRun.summary.skipped / d.runs.selectedRun.summary.total) * 100}%` }}
                            title={`${d.t('skipped')}: ${d.runs.selectedRun.summary.skipped}`}
                          ></div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className={styles.summaryPlaceholder}>
                      <Activity size={24} className={styles.pulseIcon} style={{ color: '#06b6d4', marginBottom: '0.75rem' }} />
                      <p>{d.t('execInProgress')}</p>
                      <span>{d.t('execInProgressDesc')}</span>
                    </div>
                  )}
                </section>

                {caseResults.length > 0 && (
                  <section data-testid="report-section-cases" aria-label={d.t('caseResults')}>
                    <div
                      data-testid="run-case-results"
                      style={{
                        border: '1px solid var(--semi-color-border)',
                        borderRadius: 4,
                        padding: '0.65rem 0.75rem',
                        marginBottom: '0.75rem',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '0.5rem',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem' }}>
                        <h4 style={{ margin: 0, fontSize: '0.9rem' }}>{d.t('caseResults')}</h4>
                        <span style={{ fontSize: '0.75rem', opacity: 0.65 }}>{caseResults.length}</span>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                        {caseResults.map((caseResult, index) => {
                          const label = caseResult.suite
                            ? `${caseResult.suite}::${caseResult.name}`
                            : caseResult.name
                          return (
                            <div
                              key={`${caseResult.suite}:${caseResult.name}:${index}`}
                              data-testid={`run-case-result-${index}`}
                              style={{
                                borderTop: index === 0 ? 'none' : '1px solid var(--semi-color-border)',
                                paddingTop: index === 0 ? 0 : '0.4rem',
                                display: 'flex',
                                flexDirection: 'column',
                                gap: '0.25rem',
                                fontSize: '0.8rem',
                              }}
                            >
                              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                                <code>{label}</code>
                                <span style={{ color: CASE_STATUS_COLOR[caseResult.status], fontWeight: 600 }}>
                                  {caseResult.status}
                                </span>
                                <span style={{ opacity: 0.7 }}>{formatDuration(caseResult.duration_ms)}</span>
                              </div>
                              {caseResult.message && (
                                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', opacity: 0.7 }}>
                                  {caseResult.message}
                                </pre>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  </section>
                )}

                {showArtifactsSection && (
                  <section data-testid="report-section-artifacts" aria-label={d.t('runnerArtifacts')}>
                    {artifactsLoading && (
                      <span className={styles.terminalPlaceholder} data-testid="run-artifacts-loading">
                        {d.t('artifactsLoading')}
                      </span>
                    )}

                    {artifactsError && (
                      <div className={styles.summaryPlaceholder} data-testid="run-artifacts-error">
                        <AlertTriangle size={24} style={{ color: '#f59e0b', marginBottom: '0.75rem' }} />
                        <p>{d.t('artifactsLoadError')}</p>
                      </div>
                    )}

                    {artifactsForbidden && (
                      <div
                        className={styles.summaryPlaceholder}
                        data-testid="run-artifacts-forbidden"
                        role="alert"
                      >
                        <Ban size={24} style={{ color: '#f59e0b', marginBottom: '0.75rem' }} />
                        <p>{d.t('artifactsForbidden')}</p>
                        <span>{d.t('artifactsForbiddenDesc')}</span>
                      </div>
                    )}

                    {artifactsLoaded && !artifactsLoading && !artifactsError && runArtifacts.length === 0 && (
                      <div className={styles.summaryPlaceholder} data-testid="run-artifacts-empty">
                        <Download size={24} style={{ color: '#64748b', marginBottom: '0.75rem' }} />
                        <p>{d.t('noRunnerArtifacts')}</p>
                        <span>{d.t('noRunnerArtifactsDesc')}</span>
                      </div>
                    )}

                    {runArtifacts.length > 0 && (
                      <div
                        data-testid="run-artifacts"
                        style={{
                          border: '1px solid var(--semi-color-border)',
                          borderRadius: 4,
                          padding: '0.65rem 0.75rem',
                          marginBottom: '0.75rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.5rem',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', alignItems: 'flex-start' }}>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                            <h4 style={{ margin: 0, fontSize: '0.9rem' }}>{d.t('runnerArtifacts')}</h4>
                            <span style={{ fontSize: '0.75rem', opacity: 0.65 }}>
                              {d.t('runnerArtifactsDesc')}
                            </span>
                          </div>
                          <a
                            data-testid="run-artifacts-download-all"
                            href={`/runs/${d.runs.selectedRun!.id}/artifacts.zip`}
                            download
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '0.35rem',
                              color: 'var(--semi-color-primary)',
                              fontSize: '0.78rem',
                              fontWeight: 700,
                              textDecoration: 'none',
                              flexShrink: 0,
                            }}
                          >
                            <Download size={13} />
                            <span>{d.t('downloadAllArtifacts')}</span>
                          </a>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                          {artifactGroups.map((group) => (
                            <div
                              key={group.key}
                              data-testid={`run-artifact-group-${group.key}`}
                              style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}
                            >
                              <div style={{ fontSize: '0.72rem', fontWeight: 700, opacity: 0.72, textTransform: 'uppercase' }}>
                                {d.t(group.labelKey)}
                              </div>
                              {group.items.map(({ artifact, index }) => {
                                const traceCommand = traceViewerCommand(artifact)
                                return (
                                  <div
                                    key={artifact.path}
                                    style={{
                                      display: 'flex',
                                      flexDirection: 'column',
                                      gap: '0.25rem',
                                    }}
                                  >
                                    <a
                                      data-testid={`run-artifact-link-${index}`}
                                      href={`/runs/${d.runs.selectedRun!.id}/artifacts/${encodeURIComponent(artifact.path)}`}
                                      download
                                      style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'space-between',
                                        gap: '0.75rem',
                                        color: 'var(--semi-color-primary)',
                                        textDecoration: 'none',
                                        fontSize: '0.8rem',
                                      }}
                                    >
                                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', minWidth: 0 }}>
                                        <Download size={13} />
                                        <code style={{ overflowWrap: 'anywhere' }}>{artifact.path}</code>
                                      </span>
                                      <span
                                        style={{
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '0.5rem',
                                          opacity: 0.65,
                                          flexShrink: 0,
                                        }}
                                      >
                                        <code>{artifact.content_type}</code>
                                        <span>{formatBytes(artifact.size_bytes)}</span>
                                      </span>
                                    </a>
                                    {group.key === 'trace' && (
                                      <div
                                        data-testid={`run-artifact-trace-command-${index}`}
                                        style={{
                                          display: 'flex',
                                          alignItems: 'center',
                                          justifyContent: 'space-between',
                                          gap: '0.75rem',
                                          border: '1px dashed var(--semi-color-border)',
                                          borderRadius: 4,
                                          padding: '0.35rem 0.45rem',
                                          fontSize: '0.76rem',
                                          color: 'var(--semi-color-text-1)',
                                          background: 'var(--semi-color-fill-0)',
                                        }}
                                      >
                                        <span style={{ display: 'inline-flex', flexDirection: 'column', gap: '0.15rem', minWidth: 0 }}>
                                          <span style={{ opacity: 0.65 }}>{d.t('traceViewerCommand')}</span>
                                          <code style={{ overflowWrap: 'anywhere' }}>{traceCommand}</code>
                                        </span>
                                        <button
                                          type="button"
                                          data-testid={`run-artifact-trace-copy-${index}`}
                                          onClick={() => d.terminal.copyToClipboard(traceCommand)}
                                          style={{
                                            display: 'inline-flex',
                                            alignItems: 'center',
                                            gap: '0.25rem',
                                            color: 'var(--semi-color-primary)',
                                            border: 'none',
                                            background: 'transparent',
                                            cursor: 'pointer',
                                            fontSize: '0.74rem',
                                            fontWeight: 700,
                                            padding: 0,
                                            flexShrink: 0,
                                          }}
                                        >
                                          <Copy size={12} />
                                          <span>{d.t('copyTraceCommand')}</span>
                                        </button>
                                      </div>
                                    )}
                                  </div>
                                )
                              })}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </section>
                )}

                {/* Premium Allure Report Portal Actions */}
                {d.runs.selectedRun.report?.html_generated && d.runs.selectedRun.report?.allure_report_file ? (
                  <div className={styles.reportPortalCardCompact}>
                    <div className={styles.portalActions} style={{ marginTop: 0 }}>
                      <button
                        type="button"
                        className={styles.portalBtnPrimary}
                        onClick={() => d.terminal.setIsReportFullscreen(true)}
                        title={d.lang === 'zh' ? '全屏查看测试报告' : 'View Test Report in Fullscreen'}
                        data-testid="report-fullscreen-button"
                      >
                        <Maximize2 size={14} />
                        <span>{d.lang === 'zh' ? '全屏查看报告' : 'Fullscreen Report'}</span>
                      </button>

                      <a
                        href={`/runs/${d.runs.selectedRun.id}/report`}
                        target="_blank"
                        rel="noreferrer"
                        className={styles.portalBtnSecondary}
                        title={d.lang === 'zh' ? '在新窗口中打开' : 'Open in New Window'}
                        data-testid="report-new-window-link"
                      >
                        <ExternalLink size={14} />
                        <span>{d.lang === 'zh' ? '在新窗口打开' : 'Open in New Window'}</span>
                      </a>
                    </div>
                  </div>
                ) : (
                  <div className={styles.reportPlaceholder}>
                    {d.runs.selectedRun.status === 'running' || d.runs.selectedRun.status === 'queued' ? (
                      <>
                        <RotateCw size={24} className={styles.spinIcon} style={{ color: '#a855f7', marginBottom: '0.75rem' }} />
                        <p>{d.t('generatingAllureReport')}</p>
                        <span>{d.t('generatingAllureReportDesc')}</span>
                      </>
                    ) : (
                      <>
                        <AlertTriangle size={24} style={{ color: '#f59e0b', marginBottom: '0.75rem' }} />
                        <p>{d.t('noReportGenerated')}</p>
                        <span>{d.t('noReportGeneratedDesc')}</span>
                      </>
                    )}
                  </div>
                )}
              </>
            )}

            {d.terminal.drawerTab === 'diff' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {diffLoading ? (
                  <span className={styles.terminalPlaceholder}>{d.t('diffLoading')}</span>
                ) : diffError ? (
                  <div className={styles.summaryPlaceholder} data-testid="diff-error">
                    <AlertTriangle size={24} style={{ color: '#f59e0b', marginBottom: '0.75rem' }} />
                    <p>{d.t('diffLoadError')}</p>
                    <span>{d.t('diffLoadErrorDesc')}</span>
                  </div>
                ) : !runDiff || runDiff.baseline === null ? (
                  <div className={styles.summaryPlaceholder} data-testid="diff-empty-baseline">
                    <GitCompare size={24} style={{ color: '#64748b', marginBottom: '0.75rem' }} />
                    <p>{d.t('diffNoBaseline')}</p>
                  </div>
                ) : (
                  <>
                    <div style={{ fontSize: '0.85rem', opacity: 0.8 }}>
                      {d.t('diffBaseline')}: <code>{runDiff.baseline.id.slice(0, 8)}</code>
                      {' · '}{formatDate(runDiff.baseline.created_at)}
                    </div>
                    {diffIsEmpty(runDiff.diff) ? (
                      <span className={styles.terminalPlaceholder}>{d.t('diffNoChanges')}</span>
                    ) : (
                      diffBuckets(runDiff.diff)
                        .filter((b) => b.cases.length > 0)
                        .map((b) => (
                          <div
                            key={b.key}
                            data-testid={`diff-bucket-${b.key}`}
                            style={{ border: `1px solid ${DIFF_TONE_COLOR[b.tone]}`, borderRadius: 4, padding: '0.5rem 0.75rem' }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: DIFF_TONE_COLOR[b.tone], fontWeight: 600, marginBottom: '0.4rem' }}>
                              {DIFF_TONE_ICON[b.tone]}
                              <span>{d.t(DIFF_BUCKET_LABEL[b.key])}</span>
                              <span style={{ opacity: 0.7 }}>({b.cases.length})</span>
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                              {b.cases.map((c, i) => (
                                <CaseRow key={i} caseResult={c} />
                              ))}
                            </div>
                          </div>
                        ))
                    )}
                  </>
                )}
              </div>
            )}

            {d.terminal.drawerTab === 'ai-insights' && d.runs.selectedRunId && (
              <AiInsightsTab runId={d.runs.selectedRunId} apiFetch={d.apiFetch} t={d.t} />
            )}
          </div>
        )}
      </div>
    </SideSheet>
  )
}
