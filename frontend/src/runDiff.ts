// Pure helpers for the cross-run diff view (stage 2). Kept in a .ts module so
// vitest covers the bucket ordering / emptiness logic that the .tsx drawer only
// renders. Mirrors the API's RegressionDiff shape (schemas RunDiffResponse).

import type { RunDiff } from './types'

export type DiffTone = 'danger' | 'warning' | 'success' | 'info' | 'muted'

export interface DiffBucketView {
  key: keyof RunDiff['diff']
  tone: DiffTone
  cases: RunDiff['diff'][keyof RunDiff['diff']]
}

// Display order: regressions first (new failures on top), then still-failing,
// fixed, and finally the membership changes — mirrors the plan's
// "new_failures 置顶着色".
const BUCKET_ORDER: { key: keyof RunDiff['diff']; tone: DiffTone }[] = [
  { key: 'new_failures', tone: 'danger' },
  { key: 'still_failing', tone: 'warning' },
  { key: 'fixed', tone: 'success' },
  { key: 'new_cases', tone: 'info' },
  { key: 'removed_cases', tone: 'muted' },
]

export function diffBuckets(diff: RunDiff['diff']): DiffBucketView[] {
  return BUCKET_ORDER.map(({ key, tone }) => ({ key, tone, cases: diff[key] }))
}

export function diffTotal(diff: RunDiff['diff']): number {
  return BUCKET_ORDER.reduce((n, { key }) => n + diff[key].length, 0)
}

export function diffIsEmpty(diff: RunDiff['diff']): boolean {
  return diffTotal(diff) === 0
}
