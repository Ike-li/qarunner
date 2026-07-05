import { beforeEach, describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatsCards } from './StatsCards'
import type { Run } from '../types'

interface TestDashboardState {
  t: (key: string) => string
  runs: {
    runs: Run[]
  }
  selectedSuiteFilter: string | null
  selectedProfileFilter: string | null
  logFilterTab: 'All' | 'Manual' | 'Scheduled'
  searchRunId: string
  filterStatus: string
  filterOwner: string
}

const dashboard = vi.hoisted(() => ({
  state: null as unknown as TestDashboardState,
}))

vi.mock('../hooks/DashboardContext', () => ({
  useDashboard: () => dashboard.state,
}))

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

const RUNS: Run[] = [
  run({
    id: 'run-smoke-passed',
    profile_id: 'profile-smoke',
    tests_path: 'tests/smoke',
    passed: true,
    summary: {
      total: 8,
      passed: 8,
      failed: 0,
      skipped: 0,
      error: 0,
      duration_ms: 1000,
      pass_rate: 100,
    },
  }),
  run({
    id: 'run-regression-failed',
    profile_id: 'profile-regression',
    tests_path: 'tests/regression',
    created_by: 'alice',
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
    id: 'run-smoke-queued',
    status: 'queued',
    profile_id: 'profile-smoke',
    tests_path: 'tests/smoke',
    created_by: 'system:schedule',
  }),
  run({
    id: 'run-smoke-failed',
    status: 'failed',
    profile_id: 'profile-smoke',
    tests_path: 'tests/smoke',
    passed: null,
  }),
]

function createDashboardState(overrides: Partial<TestDashboardState> = {}): TestDashboardState {
  return {
    t: (key: string) => key,
    runs: {
      runs: RUNS,
    },
    selectedSuiteFilter: null,
    selectedProfileFilter: null,
    logFilterTab: 'All',
    searchRunId: '',
    filterStatus: 'ALL',
    filterOwner: 'ALL',
    ...overrides,
  }
}

describe('StatsCards', () => {
  beforeEach(() => {
    dashboard.state = createDashboardState()
  })

  it('renders all four stat cards', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-total')).toBeVisible()
    expect(screen.getByTestId('stat-success-rate')).toBeVisible()
    expect(screen.getByTestId('stat-failed')).toBeVisible()
    expect(screen.getByTestId('stat-active')).toBeVisible()
  })

  it('displays total runs count', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-total').querySelector('h2')).toHaveTextContent('4')
  })

  it('displays manual and scheduled run breakdown', () => {
    render(<StatsCards />)
    const totalCard = screen.getByTestId('stat-total')
    expect(totalCard).toHaveTextContent('manualRuns: 3')
    expect(totalCard).toHaveTextContent('scheduledRuns: 1')
  })

  it('displays success rate percentage', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-success-rate').querySelector('h2')).toHaveTextContent('50%')
  })

  it('displays passed/failed/total test cases', () => {
    render(<StatsCards />)
    const rateCard = screen.getByTestId('stat-success-rate')
    expect(rateCard).toHaveTextContent('passedCases: 9')
    expect(rateCard).toHaveTextContent('failedCases: 3')
    expect(rateCard).toHaveTextContent('totalCases: 12')
  })

  it('displays failed runs count', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-failed').querySelector('h2')).toHaveTextContent('2')
  })

  it('displays active queue count', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-active').querySelector('h2')).toHaveTextContent('1')
  })

  it('summarizes only runs matching the selected profile filter', () => {
    dashboard.state = createDashboardState({
      selectedProfileFilter: 'profile-smoke',
    })

    render(<StatsCards />)

    expect(screen.getByTestId('stat-total').querySelector('h2')).toHaveTextContent('3')
    expect(screen.getByTestId('stat-success-rate').querySelector('h2')).toHaveTextContent('100%')
    expect(screen.getByTestId('stat-failed').querySelector('h2')).toHaveTextContent('1')
    expect(screen.getByTestId('stat-active').querySelector('h2')).toHaveTextContent('1')
  })
})
