import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatsCards } from './StatsCards'

vi.mock('../hooks/DashboardContext', () => ({
  useDashboard: () => ({
    t: (key: string) => key,
    runs: {
      totalRuns: 42,
      manualRunsCount: 30,
      scheduledRunsCount: 12,
      overallSuccessRate: 85.5,
      passedTestCases: 100,
      failedTestCases: 15,
      totalTestCases: 115,
      failedRunsCount: 3,
      activeRunsCount: 2,
    },
  }),
}))

describe('StatsCards', () => {
  it('renders all four stat cards', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-total')).toBeVisible()
    expect(screen.getByTestId('stat-success-rate')).toBeVisible()
    expect(screen.getByTestId('stat-failed')).toBeVisible()
    expect(screen.getByTestId('stat-active')).toBeVisible()
  })

  it('displays total runs count', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-total')).toHaveTextContent('42')
  })

  it('displays manual and scheduled run breakdown', () => {
    render(<StatsCards />)
    const totalCard = screen.getByTestId('stat-total')
    expect(totalCard).toHaveTextContent('30')
    expect(totalCard).toHaveTextContent('12')
  })

  it('displays success rate percentage', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-success-rate')).toHaveTextContent('85.5%')
  })

  it('displays passed/failed/total test cases', () => {
    render(<StatsCards />)
    const rateCard = screen.getByTestId('stat-success-rate')
    expect(rateCard).toHaveTextContent('100')
    expect(rateCard).toHaveTextContent('15')
    expect(rateCard).toHaveTextContent('115')
  })

  it('displays failed runs count', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-failed')).toHaveTextContent('3')
  })

  it('displays active queue count', () => {
    render(<StatsCards />)
    expect(screen.getByTestId('stat-active')).toHaveTextContent('2')
  })
})
