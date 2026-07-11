import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'

interface Props {
  filteredStdout: string
  filteredStderr: string
  filteredStreamed: string
  /** RunDetailsDrawer-only: show a load-error placeholder ahead of every other state. */
  showDetailsError?: boolean
}

/**
 * The terminal body's four-state branch (streaming / static stdout-stderr /
 * loading / empty), each with its own filtered-vs-unfiltered placeholder.
 * Shared by RunDetailsDrawer's inline console and FullscreenTerminalOverlay,
 * which previously duplicated this logic verbatim.
 */
export function LogsBody({ filteredStdout, filteredStderr, filteredStreamed, showDetailsError }: Props) {
  const d = useDashboard()
  const selectedRun = d.runs.selectedRun!

  if (showDetailsError && d.runs.detailsError) {
    return <span className={styles.terminalPlaceholder}>{d.t('runDetailsLoadError')}</span>
  }

  if (d.runs.isStreaming) {
    if (filteredStreamed) {
      return <pre className={styles.stdoutPre}>{d.terminal.renderFormattedLogs(filteredStreamed)}</pre>
    }
    if (d.runs.streamedStdout) {
      return (
        <span className={styles.terminalPlaceholder}>
          {d.t('logsBodyNoMatch')}
        </span>
      )
    }
    return (
      <span className={styles.terminalPlaceholder}>
        <span className={styles.waitingLogs}>
          <span className={styles.pulsingText}>{d.t('waitingLogs')}</span>
        </span>
      </span>
    )
  }

  if (selectedRun.stdout || selectedRun.stderr || d.runs.streamedStdout) {
    if (filteredStdout || filteredStderr || filteredStreamed) {
      return (
        <>
          {(filteredStdout || (d.runs.selectedRunDetails ? null : filteredStreamed)) && (
            <pre className={styles.stdoutPre}>
              {d.terminal.renderFormattedLogs(filteredStdout || filteredStreamed)}
            </pre>
          )}
          {filteredStderr && <pre className={styles.stderrPre}>{d.terminal.renderFormattedLogs(filteredStderr)}</pre>}
        </>
      )
    }
    return (
      <span className={styles.terminalPlaceholder}>
        {d.t('logsBodyNoMatch')}
      </span>
    )
  }

  if (d.runs.detailsLoading) {
    return (
      <span className={styles.terminalPlaceholder}>
        <span className={styles.waitingLogs}>
          <span className={styles.pulsingText}>
            {d.t('logsBodyLoadingConsole')}
          </span>
        </span>
      </span>
    )
  }

  return <span className={styles.terminalPlaceholder}>{d.t('noLogsAvailable')}</span>
}
