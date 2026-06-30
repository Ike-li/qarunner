import { describe, expect, it } from 'vitest'

import { latestPassRatePct, trendGeometry } from './runTrend'
import type { TrendPoint } from './types'

const pt = (rate: number): TrendPoint => ({
  run_id: 'r',
  created_at: '2026-01-01T00:00:00Z',
  pass_rate: rate,
  total: 10,
  passed: Math.round(rate * 10),
  failed: 10 - Math.round(rate * 10),
})

describe('trendGeometry', () => {
  it('returns empty for no points', () => {
    expect(trendGeometry([], 100, 40)).toEqual({ polyline: '', last: null })
  })

  it('pins a single point to the right edge', () => {
    const g = trendGeometry([pt(0.5)], 100, 40)
    expect(g.polyline).toBe('100.0,20.0')
    expect(g.last).toEqual({ x: 100, y: 20 })
  })

  it('maps a higher pass_rate to a smaller y (higher on screen)', () => {
    const g = trendGeometry([pt(0), pt(1)], 100, 40)
    expect(g.polyline).toBe('0.0,40.0 100.0,0.0')
    expect(g.last).toEqual({ x: 100, y: 0 })
  })
})

describe('latestPassRatePct', () => {
  it('is null with no points', () => {
    expect(latestPassRatePct([])).toBeNull()
  })

  it('returns the last point as a one-decimal percentage', () => {
    expect(latestPassRatePct([pt(0.5), pt(0.873)])).toBe(87.3)
  })
})
