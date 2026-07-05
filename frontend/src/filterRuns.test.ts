import { describe, it, expect } from 'vitest'
import { filterRuns, type RunFilters } from './filterRuns'
import type { Run } from './types'

const BASE_FILTERS: RunFilters = {
  suiteFilter: '',
  profileFilter: '',
  logFilterTab: 'All',
  searchRunId: '',
  filterStatus: 'ALL',
  filterOwner: 'ALL',
}

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
  run({ id: 'run-alpha', status: 'completed', created_by: 'admin', tests_path: 'suite_a/', profile_id: 'profile-a' }),
  run({ id: 'run-beta', status: 'failed', created_by: 'alice', tests_path: 'suite_b/' }),
  run({ id: 'run-gamma', status: 'completed', created_by: 'admin', tests_path: 'suite_a/', profile_id: 'profile-b' }),
  run({ id: 'run-delta', status: 'queued', created_by: 'system:schedule', tests_path: 'suite_c/' }),
]

describe('filterRuns', () => {
  it('returns all runs when no filters are active', () => {
    expect(filterRuns(RUNS, BASE_FILTERS)).toHaveLength(4)
  })

  describe('suiteFilter', () => {
    it('filters by tests_path', () => {
      const result = filterRuns(RUNS, { ...BASE_FILTERS, suiteFilter: 'suite_a/' })
      expect(result).toHaveLength(2)
      expect(result.every((r) => r.tests_path === 'suite_a/')).toBe(true)
    })

    it('returns empty when suite does not match', () => {
      expect(filterRuns(RUNS, { ...BASE_FILTERS, suiteFilter: 'nonexistent/' })).toHaveLength(0)
    })
  })

  describe('profileFilter', () => {
    it('filters by profile_id', () => {
      const result = filterRuns(RUNS, { ...BASE_FILTERS, profileFilter: 'profile-a' })
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe('run-alpha')
    })
  })

  describe('logFilterTab', () => {
    it('Manual tab excludes system:schedule runs', () => {
      const result = filterRuns(RUNS, { ...BASE_FILTERS, logFilterTab: 'Manual' })
      expect(result).toHaveLength(3)
      expect(result.every((r) => r.created_by !== 'system:schedule')).toBe(true)
    })

    it('Scheduled tab shows only system:schedule runs', () => {
      const result = filterRuns(RUNS, { ...BASE_FILTERS, logFilterTab: 'Scheduled' })
      expect(result).toHaveLength(1)
      expect(result[0].created_by).toBe('system:schedule')
    })
  })

  describe('searchRunId', () => {
    it('filters by partial ID match (case-insensitive)', () => {
      expect(filterRuns(RUNS, { ...BASE_FILTERS, searchRunId: 'alpha' })).toHaveLength(1)
      expect(filterRuns(RUNS, { ...BASE_FILTERS, searchRunId: 'ALPHA' })).toHaveLength(1)
    })

    it('returns empty for non-matching search', () => {
      expect(filterRuns(RUNS, { ...BASE_FILTERS, searchRunId: 'zzz' })).toHaveLength(0)
    })
  })

  describe('filterStatus', () => {
    it('filters by status', () => {
      const result = filterRuns(RUNS, { ...BASE_FILTERS, filterStatus: 'failed' })
      expect(result).toHaveLength(1)
      expect(result[0].status).toBe('failed')
    })

    it('ALL shows everything', () => {
      expect(filterRuns(RUNS, { ...BASE_FILTERS, filterStatus: 'ALL' })).toHaveLength(4)
    })
  })

  describe('filterOwner', () => {
    it('filters by owner', () => {
      const result = filterRuns(RUNS, { ...BASE_FILTERS, filterOwner: 'alice' })
      expect(result).toHaveLength(1)
      expect(result[0].created_by).toBe('alice')
    })

    it('ALL shows everything', () => {
      expect(filterRuns(RUNS, { ...BASE_FILTERS, filterOwner: 'ALL' })).toHaveLength(4)
    })
  })

  describe('combined filters', () => {
    it('applies owner + search together', () => {
      const result = filterRuns(RUNS, {
        ...BASE_FILTERS,
        filterOwner: 'admin',
        searchRunId: 'alpha',
      })
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe('run-alpha')
    })

    it('applies status + suite together', () => {
      const result = filterRuns(RUNS, {
        ...BASE_FILTERS,
        filterStatus: 'completed',
        suiteFilter: 'suite_a/',
      })
      expect(result).toHaveLength(2)
    })

    it('returns empty when combined filters have no intersection', () => {
      const result = filterRuns(RUNS, {
        ...BASE_FILTERS,
        filterStatus: 'failed',
        suiteFilter: 'suite_a/',
      })
      expect(result).toHaveLength(0)
    })
  })
})
