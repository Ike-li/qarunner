import { Check, Copy, Download, Search, Terminal, X, ZoomIn, ZoomOut } from 'lucide-react'
import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'
import { useDialogA11y } from '../hooks/useDialogA11y'
import { LogsBody } from './LogsBody'

/** Fullscreen read-only terminal overlay opened from the drawer logs tab. Mirrors
 *  the inline console (search/level/font + word-wrap + auto-scroll toggles) over
 *  the full viewport. Rendered only while isTerminalFullscreen (guard in App). */
export function FullscreenTerminalOverlay() {
  const d = useDashboard()
  const selectedRun = d.runs.selectedRun!

  // Compute filtered logs locally
  const filteredStdout = d.terminal.getFilteredLogs(selectedRun.stdout || '')
  const filteredStderr = d.terminal.getFilteredLogs(selectedRun.stderr || '')
  const filteredStreamed = d.terminal.getFilteredLogs(d.runs.streamedStdout)

  // Inline download logic
  const handleDownloadLogs = () => {
    const run = d.runs.selectedRun
    if (!run) return
    const logText = d.runs.isStreaming
      ? d.runs.streamedStdout
      : (run.stdout || '') + '\n' + (run.stderr || '')
    const blob = new Blob([logText], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `run_${run.id}_execution.log`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  const dialogRef = useDialogA11y({ isOpen: true, onClose: () => d.terminal.setIsTerminalFullscreen(false) })
  return (
    <div
      className={styles.fullscreenTerminalOverlay}
      onClick={() => d.terminal.setIsTerminalFullscreen(false)}
      data-testid="fullscreen-terminal-overlay"
    >
      <div
        ref={dialogRef}
        className={styles.fullscreenTerminal}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fullscreen-terminal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header Controls */}
        <div className={styles.fullscreenTerminalHeader}>
          <div className={styles.terminalTitleGroup}>
            <Terminal size={16} className={styles.terminalHeaderIcon} />
            <h3 className={styles.fullscreenTerminalTitle} id="fullscreen-terminal-title">
              {d.t('fsTerminalTitle')}
              <span className={styles.fullscreenTerminalSub}>
                #{selectedRun.id}
              </span>
            </h3>
          </div>
              
          <div className={styles.fullscreenTerminalControls}>
            {/* Search Box */}
            <div className={styles.terminalSearchWrapper}>
              <Search size={12} className={styles.terminalSearchIcon} />
              <input 
                type="text" 
                className={styles.terminalSearchInput}
                placeholder={d.t('fsTerminalSearchPlaceholder')}
                value={d.terminal.logSearchQuery}
                onChange={(e) => d.terminal.setLogSearchQuery(e.target.value)}
              />
              {d.terminal.logSearchQuery && (
                <button
                  className={styles.terminalSearchClear}
                  onClick={() => d.terminal.setLogSearchQuery('')}
                  title={d.t('fsTerminalClearSearch')}
                  aria-label={d.t('fsTerminalClearSearch')}
                >
                  <X size={10} />
                </button>
              )}
            </div>

            {/* Log Level Capsule Filters */}
            <div className={styles.logLevelCapsules}>
              {(['ALL', 'ERROR', 'WARNING', 'SUCCESS'] as const).map(level => {
                const levelLabel =
                  level === 'ALL'
                    ? d.t('fsTerminalLevelAll')
                    : level === 'ERROR'
                      ? d.t('fsTerminalLevelError')
                      : level === 'WARNING'
                        ? d.t('fsTerminalLevelWarning')
                        : d.t('fsTerminalLevelSuccess');
                return (
                  <button
                    key={level}
                    className={`${styles.capsuleBtn} ${styles[`capsule_${level}`]} ${d.terminal.logLevelFilter === level ? styles.capsuleActive : ''}`}
                    onClick={() => d.terminal.setLogLevelFilter(level)}
                  >
                    {levelLabel}
                  </button>
                );
              })}
            </div>

            {/* Font Sizer Controls */}
            <div className={styles.fontSizeControls}>
              <button
                className={styles.fontSizeBtn}
                onClick={() => d.terminal.setTerminalFontSize(prev => Math.max(10, prev - 1))}
                title={d.t('fsTerminalDecreaseFontSize')}
                aria-label={d.t('fsTerminalDecreaseFontSize')}
              >
                <ZoomOut size={12} />
              </button>
              <span className={styles.fontSizeValue}>{d.terminal.terminalFontSize}px</span>
              <button
                className={styles.fontSizeBtn}
                onClick={() => d.terminal.setTerminalFontSize(prev => Math.min(24, prev + 1))}
                title={d.t('fsTerminalIncreaseFontSize')}
                aria-label={d.t('fsTerminalIncreaseFontSize')}
              >
                <ZoomIn size={12} />
              </button>
            </div>

            {/* Word Wrap Toggle */}
            <button 
              className={`${styles.terminalToolbarBtn} ${d.terminal.isWordWrapEnabled ? styles.terminalToolbarBtnActive : ''}`}
              onClick={() => d.terminal.setIsWordWrapEnabled(!d.terminal.isWordWrapEnabled)}
              title={d.terminal.isWordWrapEnabled ? d.t('fsTerminalDisableWordWrap') : d.t('fsTerminalEnableWordWrap')}
            >
              {d.t('fsTerminalWordWrap')}
            </button>

            {/* Auto Scroll Toggle */}
            <button 
              className={`${styles.terminalToolbarBtn} ${d.terminal.isAutoScrollEnabled ? styles.terminalToolbarBtnActive : ''}`}
              onClick={() => d.terminal.setIsAutoScrollEnabled(!d.terminal.isAutoScrollEnabled)}
              title={d.terminal.isAutoScrollEnabled ? d.t('fsTerminalFreezeScrolling') : d.t('fsTerminalAutoScroll')}
            >
              {d.t('fsTerminalScrollLock')}
            </button>

            {/* Copy Button */}
            <button 
              className={styles.terminalCopyButton}
              onClick={() => {
                const logsText = d.runs.isStreaming 
                  ? d.runs.streamedStdout 
                  : (selectedRun.stdout || '') + '\n' + (selectedRun.stderr || '');
                d.terminal.copyToClipboard(logsText);
              }}
            >
              {d.terminal.copySuccess ? <Check size={12} style={{ color: '#10b981' }} /> : <Copy size={12} />}
              <span>{d.terminal.copySuccess ? d.t('copied') : d.t('copy')}</span>
            </button>

            {/* Download Button */}
            <button 
              className={styles.terminalCopyButton}
              onClick={handleDownloadLogs}
              title={d.t('fsTerminalDownloadRawLog')}
            >
              <Download size={12} />
              <span>{d.t('download')}</span>
            </button>

            {/* Close Button */}
            <button
              className={styles.fullscreenTerminalCloseBtn}
              onClick={() => d.terminal.setIsTerminalFullscreen(false)}
              title={d.t('fsTerminalCloseFullscreen')}
              aria-label={d.t('fsTerminalCloseFullscreen')}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Terminal Content Body */}
        <div 
          className={`${styles.fullscreenTerminalBody} ${!d.terminal.isWordWrapEnabled ? styles.noWrapPre : ''}`}
          ref={d.fullscreenTerminalRef}
          style={{ fontSize: `${d.terminal.terminalFontSize}px` }}
        >
          <LogsBody
            filteredStdout={filteredStdout}
            filteredStderr={filteredStderr}
            filteredStreamed={filteredStreamed}
          />
        </div>
      </div>
    </div>
  )
}
