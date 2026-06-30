// Pure helpers for the per-case cross-run history strip (stage 3). Kept in a
// .ts module so vitest covers the status->tone classification that the .tsx
// drawer only paints. Mirrors the API's CaseHistoryResponse (schemas
// CaseHistoryResponse); the flaky verdict itself is computed server-side
// (core/flaky.py owns the threshold) and only rendered here.

import type { CaseHistoryPoint } from './types'

export type CaseTone = 'pass' | 'fail' | 'skip' | 'error'

export interface CaseCell {
  tone: CaseTone
  status: string
  at: string
}

// Same fail/non-fail split the server's flaky detector uses: failed/error are
// failures, passed is a pass, everything else (skipped, xfail, ...) is muted.
const STATUS_TONE: Record<string, CaseTone> = {
  passed: 'pass',
  failed: 'fail',
  error: 'error',
  skipped: 'skip',
}

/** Map a chronological (oldest-first) history into renderable cells. Unknown
 *  statuses fall back to the muted 'skip' tone rather than dropping the run. */
export function caseCells(points: CaseHistoryPoint[]): CaseCell[] {
  return points.map((p) => ({
    tone: STATUS_TONE[p.status] ?? 'skip',
    status: p.status,
    at: p.created_at,
  }))
}
