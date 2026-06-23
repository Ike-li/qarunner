// Pure log-formatting helpers, extracted from App.tsx so they can be unit
// tested in isolation (FE-4). The component keeps only the thin JSX mapping.

export type LogLevelFilter = 'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS'

export type LogLineKind = 'header' | 'success' | 'error' | 'warning' | 'plain'

export function formatDuration(ms: number | undefined | null): string {
  if (ms === undefined || ms === null) return '-'
  if (ms < 1000) return `${ms}ms`
  const sec = (ms / 1000).toFixed(1)
  return `${sec}s`
}

export function matchesLogLevel(line: string, filter: LogLevelFilter): boolean {
  if (filter === 'ALL') return true
  const lowerLine = line.toLowerCase()
  if (filter === 'ERROR') {
    return (
      lowerLine.includes('failed') ||
      lowerLine.includes('error') ||
      lowerLine.includes('exception') ||
      lowerLine.includes('traceback') ||
      line.startsWith('E   ') ||
      line.startsWith('>   ')
    )
  }
  if (filter === 'WARNING') {
    return (
      lowerLine.includes('warning') ||
      lowerLine.includes('userwarning') ||
      lowerLine.includes('deprecationwarning')
    )
  }
  if (filter === 'SUCCESS') {
    return lowerLine.includes('passed')
  }
  return true
}

export function classifyLogLine(line: string): LogLineKind {
  if (line.startsWith('====') || line.startsWith('----') || line.includes('test session starts')) {
    return 'header'
  }
  // FE-4: a success line needs BOTH a pass token AND a summary marker. Without
  // the parentheses, `&&` bound tighter than `||`, so any line merely
  // containing 'PASSED' was coloured green regardless of context (e.g. a
  // failure line naming a test_PASSED_* case).
  if (
    (line.includes('PASSED') || line.includes('passed')) &&
    (line.includes('in ') || line.includes('==='))
  ) {
    return 'success'
  }
  if (
    line.includes('FAILED') ||
    line.includes('failed') ||
    line.includes('AssertionError') ||
    line.includes('ValueError') ||
    line.startsWith('E   ') ||
    line.startsWith('>   ')
  ) {
    return 'error'
  }
  if (line.includes('WARNING') || line.includes('warning') || line.includes('UserWarning')) {
    return 'warning'
  }
  return 'plain'
}
