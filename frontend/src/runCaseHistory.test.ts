import { describe, expect, it } from 'vitest'

import { caseCells } from './runCaseHistory'
import type { CaseHistoryPoint } from './types'

const pt = (status: string, at = '2026-01-01T00:00:00Z'): CaseHistoryPoint =>
  ({ status, created_at: at }) as CaseHistoryPoint

describe('caseCells', () => {
  it('maps each known status to its tone', () => {
    const cells = caseCells([pt('passed'), pt('failed'), pt('error'), pt('skipped')])
    expect(cells.map((c) => c.tone)).toEqual(['pass', 'fail', 'error', 'skip'])
  })

  it('falls back to the muted skip tone for unknown statuses', () => {
    expect(caseCells([pt('xfail')])[0].tone).toBe('skip')
  })

  it('preserves order, length, status and timestamp', () => {
    const points = [pt('passed', 't0'), pt('failed', 't1')]
    expect(caseCells(points)).toEqual([
      { tone: 'pass', status: 'passed', at: 't0' },
      { tone: 'fail', status: 'failed', at: 't1' },
    ])
  })

  it('maps an empty history to no cells', () => {
    expect(caseCells([])).toEqual([])
  })
})
