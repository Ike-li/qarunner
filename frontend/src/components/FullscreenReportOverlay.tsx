import { BarChart3, ExternalLink, RotateCw, X } from 'lucide-react'
import { useState } from 'react'
import styles from '../App.module.css'
import { useDialogA11y } from '../hooks/useDialogA11y'
import type { Lang } from '../i18n'
import type { Run } from '../types'

interface FullscreenReportOverlayProps {
  lang: Lang
  selectedRun: Run
  setIsReportFullscreen: (value: boolean) => void
}

/** Fullscreen read-only Allure report overlay opened from the drawer report tab.
 *  Displays the Allure report iframe in 100% viewport dimensions with a premium header. */
export function FullscreenReportOverlay({
  lang,
  selectedRun,
  setIsReportFullscreen,
}: FullscreenReportOverlayProps) {
  const [isIframeLoading, setIsIframeLoading] = useState(true)
  const dialogRef = useDialogA11y({ isOpen: true, onClose: () => setIsReportFullscreen(false) })

  return (
    <div
      className={styles.fullscreenTerminalOverlay}
      onClick={() => setIsReportFullscreen(false)}
    >
      <div
        ref={dialogRef}
        className={styles.fullscreenTerminal}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fullscreen-report-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header Controls */}
        <div className={styles.fullscreenTerminalHeader}>
          <div className={styles.terminalTitleGroup}>
            <BarChart3 size={16} className={styles.terminalHeaderIcon} style={{ color: '#a855f7' }} />
            <h3 className={styles.fullscreenTerminalTitle} id="fullscreen-report-title">
              {lang === 'zh' ? 'Allure 交互式测试报告' : 'Allure Interactive Test Report'}
              <span className={styles.fullscreenTerminalSub}>
                #{selectedRun.id}
              </span>
            </h3>
          </div>
              
          <div className={styles.fullscreenTerminalControls}>
            {/* Open In New Window Button */}
            <a 
              href={`/runs/${selectedRun.id}/report`}
              target="_blank"
              rel="noreferrer"
              className={styles.terminalToolbarBtn}
              title={lang === 'zh' ? '在新窗口中打开' : 'Open in New Window'}
            >
              <ExternalLink size={12} />
              <span>{lang === 'zh' ? '新窗口打开' : 'New Window'}</span>
            </a>

            {/* Close Button */}
            <button
              className={styles.fullscreenTerminalCloseBtn}
              onClick={() => setIsReportFullscreen(false)}
              title={lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
              aria-label={lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Fullscreen Report Iframe Body */}
        <div className={styles.fullscreenReportBody}>
          {isIframeLoading && (
            <div className={styles.reportIframeLoading} style={{ top: 0 }}>
              <RotateCw size={24} className={styles.spinIcon} style={{ color: '#06b6d4' }} />
              <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.75rem' }}>
                {lang === 'zh' ? '正在载入测试报告资源...' : 'Loading Allure report resources...'}
              </span>
            </div>
          )}

          <iframe
            src={`/runs/${selectedRun.id}/report`}
            className={styles.fullscreenReportIframe}
            onLoad={() => setIsIframeLoading(false)}
            title="Allure Fullscreen Report"
          />
        </div>
      </div>
    </div>
  )
}
