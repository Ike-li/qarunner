import type { Run } from './types'

export interface RunStats {
  totalRuns: number
  overallSuccessRate: string
  activeRunsCount: number
  failedRunsCount: number
  manualRunsCount: number
  scheduledRunsCount: number
  passedTestCases: number
  failedTestCases: number
  totalTestCases: number
}

export function summarizeRuns(runs: Run[]): RunStats {
  const completedRuns = runs.filter((r) => r.status === 'completed')
  const passedRunsCount = completedRuns.filter((r) => r.passed).length

  return {
    totalRuns: runs.length,
    overallSuccessRate:
      completedRuns.length > 0
        ? ((passedRunsCount / completedRuns.length) * 100).toFixed(0)
        : '0',
    activeRunsCount: runs.filter(
      (r) => r.status === 'queued' || r.status === 'running',
    ).length,
    failedRunsCount: runs.filter(
      (r) => r.status === 'failed' || (r.status === 'completed' && !r.passed),
    ).length,
    manualRunsCount: runs.filter(
      (r) => r.created_by !== 'system:schedule',
    ).length,
    scheduledRunsCount: runs.filter(
      (r) => r.created_by === 'system:schedule',
    ).length,
    passedTestCases: runs.reduce(
      (sum, r) => sum + (r.summary?.passed ?? 0),
      0,
    ),
    failedTestCases: runs.reduce(
      (sum, r) => sum + (r.summary ? r.summary.failed + r.summary.error : 0),
      0,
    ),
    totalTestCases: runs.reduce(
      (sum, r) => sum + (r.summary?.total ?? 0),
      0,
    ),
  }
}
