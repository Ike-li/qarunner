import { useState, useCallback, useMemo } from 'react'
import styles from '../App.module.css'
import { classifyLogLine, matchesLogLevel } from '../logUtils'

/**
 * Owns the run-details terminal/drawer UI preferences (which tab, log-level
 * filter, search query, font size, width/height expansion, fullscreen, word
 * wrap, auto-scroll, copy feedback) plus the pure log-filtering and styled
 * line-rendering helpers. Lifted out of App so the drawer/terminal components
 * stay presentational. The streaming data itself (streamedStdout/isStreaming)
 * and the SSE/polling/auto-scroll effects + refs stay in App; App reads these
 * values for effect deps and forwards them down as props.
 */
export function useTerminalView() {
  const [drawerTab, setDrawerTab] = useState<'logs' | 'report' | 'diff' | 'ai-insights'>('logs')
  const [copySuccess, setCopySuccess] = useState(false)
  const [logLevelFilter, setLogLevelFilter] = useState<'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS'>('ALL')

  // Log & drawer controls to resolve "logs are too small" issue
  const [isTerminalHeightExpanded, setIsTerminalHeightExpanded] = useState<boolean>(false)
  const [terminalFontSize, setTerminalFontSize] = useState<number>(13)
  const [logSearchQuery, setLogSearchQuery] = useState<string>('')

  // Fullscreen terminal & report features (Word Wrap, Auto Scroll lock, Fullscreen Report)
  const [isTerminalFullscreen, setIsTerminalFullscreen] = useState<boolean>(false)
  const [isReportFullscreen, setIsReportFullscreen] = useState<boolean>(false)
  const [isWordWrapEnabled, setIsWordWrapEnabled] = useState<boolean>(true)
  const [isAutoScrollEnabled, setIsAutoScrollEnabled] = useState<boolean>(true)


  const copyToClipboard = useCallback((text: string) => {
    try {
      void navigator.clipboard?.writeText(text).catch(() => undefined)
    } catch {
      // The visual feedback is still useful in non-secure/browser-test contexts
      // where the Clipboard API is unavailable.
    }
    setCopySuccess(true)
    setTimeout(() => setCopySuccess(false), 2000)
  }, [])

  // Log filtering helper (matchesLogLevel lives in ./logUtils, unit-tested)
  const getFilteredLogs = useCallback((text: string) => {
    if (!text) return '';
    let lines = text.split('\n');

    // First stage: Filter by log level
    if (logLevelFilter !== 'ALL') {
      lines = lines.filter(line => matchesLogLevel(line, logLevelFilter));
    }

    // Second stage: Filter by search query
    if (logSearchQuery) {
      const query = logSearchQuery.toLowerCase();
      lines = lines.filter(line => line.toLowerCase().includes(query));
    }

    return lines.join('\n');
  }, [logLevelFilter, logSearchQuery]);

  // Map the (pure, unit-tested) line classification to its styled span.
  const formatLogLine = useCallback((line: string) => {
    const kind = classifyLogLine(line)
    if (kind === 'header') return <span className={styles.logHeaderLine}>{line}</span>
    if (kind === 'success') return <span className={styles.logSuccessLine}>{line}</span>
    if (kind === 'error') return <span className={styles.logErrorLine}>{line}</span>
    if (kind === 'warning') return <span className={styles.logWarningLine}>{line}</span>
    return <span>{line}</span>
  }, [])

  const renderFormattedLogs = useCallback((text: string) => {
    if (!text) return null
    const lines = text.split('\n')
    return lines.map((line, idx) => (
      <div key={idx} className={styles.terminalLineRow}>
        <span className={styles.terminalLineNumber}>{idx + 1}</span>
        <span className={styles.terminalLineContent}>{formatLogLine(line)}</span>
      </div>
    ))
  }, [formatLogLine])

  return useMemo(() => ({
    drawerTab,
    setDrawerTab,
    copySuccess,
    logLevelFilter,
    setLogLevelFilter,
    isTerminalHeightExpanded,
    setIsTerminalHeightExpanded,
    terminalFontSize,
    setTerminalFontSize,
    logSearchQuery,
    setLogSearchQuery,
    isTerminalFullscreen,
    setIsTerminalFullscreen,
    isReportFullscreen,
    setIsReportFullscreen,
    isWordWrapEnabled,
    setIsWordWrapEnabled,
    isAutoScrollEnabled,
    setIsAutoScrollEnabled,
    copyToClipboard,
    getFilteredLogs,
    formatLogLine,
    renderFormattedLogs,
  }), [
    drawerTab,
    copySuccess,
    logLevelFilter,
    isTerminalHeightExpanded,
    terminalFontSize,
    logSearchQuery,
    isTerminalFullscreen,
    isReportFullscreen,
    isWordWrapEnabled,
    isAutoScrollEnabled,
    copyToClipboard,
    getFilteredLogs,
    formatLogLine,
    renderFormattedLogs,
  ])
}
