import { describe, expect, it } from 'vitest'
import { summarizeRuns } from './runStats'
import type { Run } from './types'

function run(overrides: Partial<Run>): Run {
  return {
    id: 'run-0001',
    status: 'completed',
    runner: 'pytest',
    created_by: 'admin',
    tests_path: 'suite/',
    args: [],
    executor_mode: 'subprocess',
    summary: null,
    report: null,
    exit_code: 0,
    error: null,
    passed: true,
    created_at: '2026-07-01T10:00:00Z',
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

describe('summarizeRuns', () => {
  it('summarizes runs with the dashboard stat-card counting rules', () => {
    const result = summarizeRuns([
      run({
        id: 'run-passed',
        status: 'completed',
        passed: true,
        summary: {
          total: 5,
          passed: 5,
          failed: 0,
          skipped: 0,
          error: 0,
          duration_ms: 1000,
          pass_rate: 100,
        },
      }),
      run({
        id: 'run-completed-failed',
        status: 'completed',
        passed: false,
        summary: {
          total: 4,
          passed: 1,
          failed: 2,
          skipped: 0,
          error: 1,
          duration_ms: 1000,
          pass_rate: 25,
        },
      }),
      run({
        id: 'run-queued',
        status: 'queued',
        created_by: 'system:schedule',
      }),
      run({
        id: 'run-failed',
        status: 'failed',
        passed: null,
      }),
    ])

    expect(result).toEqual({
      totalRuns: 4,
      overallSuccessRate: '50',
      activeRunsCount: 1,
      failedRunsCount: 2,
      manualRunsCount: 3,
      scheduledRunsCount: 1,
      passedTestCases: 6,
      failedTestCases: 3,
      totalTestCases: 9,
    })
  })

  it('returns zero values when there are no runs', () => {
    expect(summarizeRuns([])).toEqual({
      totalRuns: 0,
      overallSuccessRate: '0',
      activeRunsCount: 0,
      failedRunsCount: 0,
      manualRunsCount: 0,
      scheduledRunsCount: 0,
      passedTestCases: 0,
      failedTestCases: 0,
      totalTestCases: 0,
    })
  })
})
