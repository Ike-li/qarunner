import type { Run } from './types'

export interface RunFilters {
  suiteFilter: string | null
  logFilterTab: string
  searchRunId: string
  filterStatus: string
  filterOwner: string
}

/**
 * Pure filter pipeline for the RunsTable.  Extracted from the component so it
 * can be unit-tested without rendering.
 */
export function filterRuns(runs: Run[], filters: RunFilters): Run[] {
  return runs.filter((r) => {
    if (filters.suiteFilter && r.tests_path !== filters.suiteFilter) return false
    if (filters.logFilterTab === 'Manual' && r.created_by === 'system:schedule') return false
    if (filters.logFilterTab === 'Scheduled' && r.created_by !== 'system:schedule') return false
    if (filters.searchRunId && !r.id.toLowerCase().includes(filters.searchRunId.toLowerCase())) return false
    if (filters.filterStatus !== 'ALL' && r.status !== filters.filterStatus) return false
    if (filters.filterOwner !== 'ALL' && r.created_by !== filters.filterOwner) return false
    return true
  })
}
