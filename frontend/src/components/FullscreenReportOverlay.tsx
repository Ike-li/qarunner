import { BarChart3, ExternalLink, RotateCw, X } from 'lucide-react'
import { useState } from 'react'
import styles from '../App.module.css'
import { useDialogA11y } from '../hooks/useDialogA11y'
import { useDashboard } from '../hooks/DashboardContext'

/** Fullscreen Allure report overlay — reads from DashboardContext. */
export function FullscreenReportOverlay() {
  const d = useDashboard()
  const [isIframeLoading, setIsIframeLoading] = useState(true)
  const dialogRef = useDialogA11y({ isOpen: true, onClose: () => d.terminal.setIsReportFullscreen(false) })

  if (!d.runs.selectedRun) return null

  const selectedRun = d.runs.selectedRun

  return (
    <div className={styles.fullscreenTerminalOverlay} onClick={() => d.terminal.setIsReportFullscreen(false)}>
      <div ref={dialogRef} className={styles.fullscreenTerminal} role="dialog" aria-modal="true"
        aria-labelledby="fullscreen-report-title" tabIndex={-1} onClick={(e) => e.stopPropagation()}
        data-testid="fullscreen-report-overlay">
        <div className={styles.fullscreenTerminalHeader}>
          <div className={styles.terminalTitleGroup}>
            <BarChart3 size={16} className={styles.terminalHeaderIcon} style={{ color: '#a855f7' }} />
            <h3 className={styles.fullscreenTerminalTitle} id="fullscreen-report-title">
              {d.lang === 'zh' ? 'Allure 交互式测试报告' : 'Allure Interactive Test Report'}
              <span className={styles.fullscreenTerminalSub}>#{selectedRun.id}</span>
            </h3>
          </div>
          <div className={styles.fullscreenTerminalControls}>
            <a href={`/runs/${selectedRun.id}/report`} target="_blank" rel="noreferrer"
              className={styles.terminalToolbarBtn}
              title={d.lang === 'zh' ? '在新窗口中打开' : 'Open in New Window'}>
              <ExternalLink size={12} />
              <span>{d.lang === 'zh' ? '新窗口打开' : 'New Window'}</span>
            </a>
            <button className={styles.fullscreenTerminalCloseBtn}
              onClick={() => d.terminal.setIsReportFullscreen(false)}
              title={d.lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
              aria-label={d.lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
              data-testid="fullscreen-report-close">
              <X size={16} />
            </button>
          </div>
        </div>
        <div className={styles.fullscreenReportBody}>
          {isIframeLoading && (
            <div className={styles.reportIframeLoading} style={{ top: 0 }}>
              <RotateCw size={24} className={styles.spinIcon} style={{ color: '#06b6d4' }} />
              <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.75rem' }}>
                {d.lang === 'zh' ? '正在载入测试报告资源...' : 'Loading Allure report resources...'}
              </span>
            </div>
          )}
          {/* S2: the report is generated from untrusted test code (attacker-influenced
              step/attachment names) and served same-origin with no CSP. `sandbox`
              blocks top-navigation hijack, popups, plugins and pointer-lock even
              though `allow-same-origin` (kept so Allure's own same-origin data
              fetches still work) doesn't fully isolate it from the parent session. */}
          <iframe src={`/runs/${selectedRun.id}/report`} className={styles.fullscreenReportIframe}
            onLoad={() => setIsIframeLoading(false)} title="Allure Fullscreen Report"
            sandbox="allow-scripts allow-same-origin"
            data-testid="fullscreen-report-iframe" />
        </div>
      </div>
    </div>
  )
}
