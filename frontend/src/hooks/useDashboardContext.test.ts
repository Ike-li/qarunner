import { describe, it, expect } from 'vitest'
import { useDashboard } from './DashboardContext'

describe('useDashboard', () => {
  it('exports a function', () => {
    expect(typeof useDashboard).toBe('function')
  })
})
