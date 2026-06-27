import type { Run } from '../types'

/**
 * Semi UI Tag color for a run's status.
 *
 * `completed` splits on the pass/fail flag: a completed run with failing (or
 * unknown/null) cases is `orange` (a warning), while a hard `failed` run stays
 * `red`. This keeps the two visually distinct — previously both were `red` and
 * the status column could not tell "finished with failures" from "crashed".
 */
export function statusTagColor(status: Run['status'], passed: boolean | null) {
  switch (status) {
    case 'queued':
      return 'grey'
    case 'running':
      return 'blue'
    case 'completed':
      return passed ? 'green' : 'orange'
    case 'failed':
      return 'red'
    case 'timeout':
      return 'amber'
    case 'cancelled':
      return 'grey'
    default:
      return 'grey'
  }
}

/**
 * Progress-bar stroke color bucketed by pass-rate percentage (0–100):
 * `>=80` green, `50–79` warning orange, `<50` danger red. A 75% run reads as
 * a warning rather than a flat red.
 */
export function passRateColor(rate: number): string {
  if (rate >= 80) return 'var(--semi-color-success)'
  if (rate >= 50) return 'var(--semi-color-warning)'
  return 'var(--semi-color-danger)'
}
