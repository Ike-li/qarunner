import { Check, Copy, Download, Search, Terminal, X, ZoomIn, ZoomOut } from 'lucide-react'
import type { Dispatch, ReactNode, RefObject, SetStateAction } from 'react'
import styles from '../App.module.css'
import type { Lang, TranslationKey } from '../i18n'
import type { Run } from '../types'

interface FullscreenTerminalOverlayProps {
  t: (key: TranslationKey) => string
  lang: Lang
  selectedRun: Run
  selectedRunDetails: Run | null
  streamedStdout: string
  isStreaming: boolean
  filteredStdout: string
  filteredStderr: string
  filteredStreamed: string
  detailsLoading: boolean
  logSearchQuery: string
  setLogSearchQuery: (value: string) => void
  logLevelFilter: 'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS'
  setLogLevelFilter: (value: 'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS') => void
  terminalFontSize: number
  setTerminalFontSize: Dispatch<SetStateAction<number>>
  isWordWrapEnabled: boolean
  setIsWordWrapEnabled: (value: boolean) => void
  isAutoScrollEnabled: boolean
  setIsAutoScrollEnabled: (value: boolean) => void
  setIsTerminalFullscreen: (value: boolean) => void
  copySuccess: boolean
  copyToClipboard: (text: string) => void
  downloadLogs: (runId: string) => void
  renderFormattedLogs: (text: string) => ReactNode
  fullscreenTerminalRef: RefObject<HTMLDivElement>
}

/** Fullscreen read-only terminal overlay opened from the drawer logs tab. Mirrors
 *  the inline console (search/level/font + word-wrap + auto-scroll toggles) over
 *  the full viewport. Rendered only while isTerminalFullscreen (guard in App). */
export function FullscreenTerminalOverlay({
  t,
  lang,
  selectedRun,
  selectedRunDetails,
  streamedStdout,
  isStreaming,
  filteredStdout,
  filteredStderr,
  filteredStreamed,
  detailsLoading,
  logSearchQuery,
  setLogSearchQuery,
  logLevelFilter,
  setLogLevelFilter,
  terminalFontSize,
  setTerminalFontSize,
  isWordWrapEnabled,
  setIsWordWrapEnabled,
  isAutoScrollEnabled,
  setIsAutoScrollEnabled,
  setIsTerminalFullscreen,
  copySuccess,
  copyToClipboard,
  downloadLogs,
  renderFormattedLogs,
  fullscreenTerminalRef,
}: FullscreenTerminalOverlayProps) {
  return (
    <div 
      className={styles.fullscreenTerminalOverlay} 
      onClick={() => setIsTerminalFullscreen(false)}
    >
      <div 
        className={styles.fullscreenTerminal} 
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header Controls */}
        <div className={styles.fullscreenTerminalHeader}>
          <div className={styles.terminalTitleGroup}>
            <Terminal size={16} className={styles.terminalHeaderIcon} />
            <h3 className={styles.fullscreenTerminalTitle}>
              {lang === 'zh' ? '只读控制台终端' : 'Read-only Console Terminal'}
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
                placeholder={lang === 'zh' ? "搜索日志..." : "Search logs..."}
                value={logSearchQuery}
                onChange={(e) => setLogSearchQuery(e.target.value)}
              />
              {logSearchQuery && (
                <button
                  className={styles.terminalSearchClear}
                  onClick={() => setLogSearchQuery('')}
                  title={lang === 'zh' ? "清除搜索" : "Clear search"}
                  aria-label={lang === 'zh' ? "清除搜索" : "Clear search"}
                >
                  <X size={10} />
                </button>
              )}
            </div>

            {/* Log Level Capsule Filters */}
            <div className={styles.logLevelCapsules}>
              {(['ALL', 'ERROR', 'WARNING', 'SUCCESS'] as const).map(level => {
                let levelLabel: string = level;
                if (lang === 'zh') {
                  levelLabel = level === 'ALL' ? '全部' : level === 'ERROR' ? '异常' : level === 'WARNING' ? '警告' : '成功';
                } else {
                  levelLabel = level === 'ALL' ? 'ALL' : level === 'ERROR' ? 'ERR' : level === 'WARNING' ? 'WARN' : 'OK';
                }
                return (
                  <button
                    key={level}
                    className={`${styles.capsuleBtn} ${styles[`capsule_${level}`]} ${logLevelFilter === level ? styles.capsuleActive : ''}`}
                    onClick={() => setLogLevelFilter(level)}
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
                onClick={() => setTerminalFontSize(prev => Math.max(10, prev - 1))}
                title={lang === 'zh' ? "减小字号" : "Decrease Font Size"}
                aria-label={lang === 'zh' ? "减小字号" : "Decrease Font Size"}
              >
                <ZoomOut size={12} />
              </button>
              <span className={styles.fontSizeValue}>{terminalFontSize}px</span>
              <button
                className={styles.fontSizeBtn}
                onClick={() => setTerminalFontSize(prev => Math.min(24, prev + 1))}
                title={lang === 'zh' ? "增大字号" : "Increase Font Size"}
                aria-label={lang === 'zh' ? "增大字号" : "Increase Font Size"}
              >
                <ZoomIn size={12} />
              </button>
            </div>

            {/* Word Wrap Toggle */}
            <button 
              className={`${styles.terminalToolbarBtn} ${isWordWrapEnabled ? styles.terminalToolbarBtnActive : ''}`}
              onClick={() => setIsWordWrapEnabled(!isWordWrapEnabled)}
              title={isWordWrapEnabled ? (lang === 'zh' ? "禁用自动换行" : "Disable word wrap") : (lang === 'zh' ? "启用自动换行" : "Enable word wrap")}
            >
              {lang === 'zh' ? '自动换行' : 'Word Wrap'}
            </button>

            {/* Auto Scroll Toggle */}
            <button 
              className={`${styles.terminalToolbarBtn} ${isAutoScrollEnabled ? styles.terminalToolbarBtnActive : ''}`}
              onClick={() => setIsAutoScrollEnabled(!isAutoScrollEnabled)}
              title={isAutoScrollEnabled ? (lang === 'zh' ? "锁定滚动" : "Freeze scrolling") : (lang === 'zh' ? "自动滚动" : "Auto scroll")}
            >
              {lang === 'zh' ? '滚动锁定' : 'Scroll Lock'}
            </button>

            {/* Copy Button */}
            <button 
              className={styles.terminalCopyButton}
              onClick={() => {
                const logsText = isStreaming 
                  ? streamedStdout 
                  : (selectedRun.stdout || '') + '\n' + (selectedRun.stderr || '');
                copyToClipboard(logsText);
              }}
            >
              {copySuccess ? <Check size={12} style={{ color: '#10b981' }} /> : <Copy size={12} />}
              <span>{copySuccess ? t('copied') : t('copy')}</span>
            </button>

            {/* Download Button */}
            <button 
              className={styles.terminalCopyButton}
              onClick={() => downloadLogs(selectedRun.id)}
              title={lang === 'zh' ? '下载完整日志' : 'Download raw log file'}
            >
              <Download size={12} />
              <span>{t('download')}</span>
            </button>

            {/* Close Button */}
            <button
              className={styles.fullscreenTerminalCloseBtn}
              onClick={() => setIsTerminalFullscreen(false)}
              title={lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
              aria-label={lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Terminal Content Body */}
        <div 
          className={`${styles.fullscreenTerminalBody} ${!isWordWrapEnabled ? styles.noWrapPre : ''}`}
          ref={fullscreenTerminalRef}
          style={{ fontSize: `${terminalFontSize}px` }}
        >
          {isStreaming ? (
            filteredStreamed ? (
              <pre className={styles.stdoutPre}>{renderFormattedLogs(filteredStreamed)}</pre>
            ) : streamedStdout ? (
              <span className={styles.terminalPlaceholder}>
                {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
              </span>
            ) : (
              <span className={styles.terminalPlaceholder}>
                <span className={styles.waitingLogs}>
                  <span className={styles.pulsingText}>{t('waitingLogs')}</span>
                </span>
              </span>
            )
          ) : (selectedRun.stdout || selectedRun.stderr || streamedStdout) ? (
            (filteredStdout || filteredStderr || filteredStreamed) ? (
              <>
                {(filteredStdout || (selectedRunDetails ? null : filteredStreamed)) && (
                  <pre className={styles.stdoutPre}>
                    {renderFormattedLogs(filteredStdout || filteredStreamed)}
                  </pre>
                )}
                {filteredStderr && <pre className={styles.stderrPre}>{renderFormattedLogs(filteredStderr)}</pre>}
              </>
            ) : (
              <span className={styles.terminalPlaceholder}>
                {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
              </span>
            )
          ) : detailsLoading ? (
            <span className={styles.terminalPlaceholder}>
              <span className={styles.waitingLogs}>
                <span className={styles.pulsingText}>{lang === 'zh' ? '正在加载控制台日志...' : 'Loading console logs...'}</span>
              </span>
            </span>
          ) : (
            <span className={styles.terminalPlaceholder}>
              {t('noLogsAvailable')}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
