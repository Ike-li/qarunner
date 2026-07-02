import {
  Activity, AlertTriangle, Ban, BarChart3, Box, Check, CheckCircle2, ChevronDown, ChevronRight,
  Clock, Copy, Download, ExternalLink, GitCompare, Maximize2, Minus, Plus, RotateCw, Shuffle,
  Terminal, Trash2, XCircle,
} from 'lucide-react'
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { SideSheet, Tabs } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import { formatDuration } from '../logUtils'
import { diffBuckets, diffIsEmpty, type DiffTone } from '../runDiff'
import { caseCells, type CaseTone } from '../runCaseHistory'
import { useDashboard } from '../hooks/DashboardContext'
import type { CaseHistory, RunDiff, TestCaseResult } from '../types'

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
  const c = caseResult
  const testsPath = d.runs.selectedRun?.tests_path
  const label = c.suite ? `${c.suite}::${c.name}` : c.name

  const toggle = () => {
    const next = !expanded
    setExpanded(next)
    if (next && history === null && !loading && testsPath) {
      setLoading(true)
      const qs = new URLSearchParams({ tests_path: testsPath, suite: c.suite, name: c.name })
      d.apiFetch(`/cases/history?${qs.toString()}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data: CaseHistory | null) => setHistory(data))
        .catch(() => setHistory(null))
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
  const [runDiff, setRunDiff] = useState<RunDiff | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)

  // Fetch the baseline diff lazily — only when the Diff tab is active (and
  // re-fetch when the selected run changes while it stays active). The cancel
  // flag drops a stale response if the user switches run/tab mid-flight.
  const selectedRunId = d.runs.selectedRun?.id
  useEffect(() => {
    if (d.terminal.drawerTab !== 'diff' || !selectedRunId) return
    let cancelled = false
    setDiffLoading(true)
    setRunDiff(null)
    d.apiFetch(`/runs/${selectedRunId}/diff`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (!cancelled) setRunDiff(data) })
      .catch(() => { if (!cancelled) setRunDiff(null) })
      .finally(() => { if (!cancelled) setDiffLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.terminal.drawerTab, selectedRunId])

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
        d.runs.setSelectedRunId(null)
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
      bodyStyle={{ padding: '16px' }}
    >
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
            </Tabs>

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
                        onClick={() => d.terminal.setIsTerminalFullscreen(true)}
                        title={d.lang === 'zh' ? "全屏终端" : "Fullscreen Terminal"}
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
                    {d.runs.isStreaming ? (
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
          </div>
        )}
    </SideSheet>
  )
}
