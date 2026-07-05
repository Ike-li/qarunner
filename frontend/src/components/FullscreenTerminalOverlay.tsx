import { Check, Copy, Download, Search, Terminal, X, ZoomIn, ZoomOut } from 'lucide-react'
import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'
import { useDialogA11y } from '../hooks/useDialogA11y'

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
              {d.lang === 'zh' ? '只读控制台终端' : 'Read-only Console Terminal'}
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
                placeholder={d.lang === 'zh' ? "搜索日志..." : "Search logs..."}
                value={d.terminal.logSearchQuery}
                onChange={(e) => d.terminal.setLogSearchQuery(e.target.value)}
              />
              {d.terminal.logSearchQuery && (
                <button
                  className={styles.terminalSearchClear}
                  onClick={() => d.terminal.setLogSearchQuery('')}
                  title={d.lang === 'zh' ? "清除搜索" : "Clear search"}
                  aria-label={d.lang === 'zh' ? "清除搜索" : "Clear search"}
                >
                  <X size={10} />
                </button>
              )}
            </div>

            {/* Log Level Capsule Filters */}
            <div className={styles.logLevelCapsules}>
              {(['ALL', 'ERROR', 'WARNING', 'SUCCESS'] as const).map(level => {
                let levelLabel: string = level;
                if (d.lang === 'zh') {
                  levelLabel = level === 'ALL' ? '全部' : level === 'ERROR' ? '异常' : level === 'WARNING' ? '警告' : '成功';
                } else {
                  levelLabel = level === 'ALL' ? 'ALL' : level === 'ERROR' ? 'ERR' : level === 'WARNING' ? 'WARN' : 'OK';
                }
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
                title={d.lang === 'zh' ? "减小字号" : "Decrease Font Size"}
                aria-label={d.lang === 'zh' ? "减小字号" : "Decrease Font Size"}
              >
                <ZoomOut size={12} />
              </button>
              <span className={styles.fontSizeValue}>{d.terminal.terminalFontSize}px</span>
              <button
                className={styles.fontSizeBtn}
                onClick={() => d.terminal.setTerminalFontSize(prev => Math.min(24, prev + 1))}
                title={d.lang === 'zh' ? "增大字号" : "Increase Font Size"}
                aria-label={d.lang === 'zh' ? "增大字号" : "Increase Font Size"}
              >
                <ZoomIn size={12} />
              </button>
            </div>

            {/* Word Wrap Toggle */}
            <button 
              className={`${styles.terminalToolbarBtn} ${d.terminal.isWordWrapEnabled ? styles.terminalToolbarBtnActive : ''}`}
              onClick={() => d.terminal.setIsWordWrapEnabled(!d.terminal.isWordWrapEnabled)}
              title={d.terminal.isWordWrapEnabled ? (d.lang === 'zh' ? "禁用自动换行" : "Disable word wrap") : (d.lang === 'zh' ? "启用自动换行" : "Enable word wrap")}
            >
              {d.lang === 'zh' ? '自动换行' : 'Word Wrap'}
            </button>

            {/* Auto Scroll Toggle */}
            <button 
              className={`${styles.terminalToolbarBtn} ${d.terminal.isAutoScrollEnabled ? styles.terminalToolbarBtnActive : ''}`}
              onClick={() => d.terminal.setIsAutoScrollEnabled(!d.terminal.isAutoScrollEnabled)}
              title={d.terminal.isAutoScrollEnabled ? (d.lang === 'zh' ? "锁定滚动" : "Freeze scrolling") : (d.lang === 'zh' ? "自动滚动" : "Auto scroll")}
            >
              {d.lang === 'zh' ? '滚动锁定' : 'Scroll Lock'}
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
              title={d.lang === 'zh' ? '下载完整日志' : 'Download raw log file'}
            >
              <Download size={12} />
              <span>{d.t('download')}</span>
            </button>

            {/* Close Button */}
            <button
              className={styles.fullscreenTerminalCloseBtn}
              onClick={() => d.terminal.setIsTerminalFullscreen(false)}
              title={d.lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
              aria-label={d.lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
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
          ) : (selectedRun.stdout || selectedRun.stderr || d.runs.streamedStdout) ? (
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
    </div>
  )
}
