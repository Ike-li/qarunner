import { useMemo } from 'react'
import { Activity, CheckCircle2, RotateCw, XCircle } from 'lucide-react'
import styles from '../App.module.css'
import { filterRuns } from '../filterRuns'
import { useDashboard } from '../hooks/DashboardContext'
import { summarizeRuns } from '../runStats'

/** Dashboard header summary cards. Reads stats from DashboardContext. */
export function StatsCards() {
  const d = useDashboard()
  const stats = useMemo(() => {
    const filteredRuns = filterRuns(d.runs.runs, {
      suiteFilter: d.selectedSuiteFilter,
      profileFilter: d.selectedProfileFilter,
      logFilterTab: d.logFilterTab,
      searchRunId: d.searchRunId,
      filterStatus: d.filterStatus,
      filterOwner: d.filterOwner,
    })
    return summarizeRuns(filteredRuns)
  }, [
    d.runs.runs,
    d.selectedSuiteFilter,
    d.selectedProfileFilter,
    d.logFilterTab,
    d.searchRunId,
    d.filterStatus,
    d.filterOwner,
  ])

  return (
    <section className={styles.statsContainer}>
      <div className={styles.statCard} data-testid="stat-total">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <Activity size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('totalExecutions')}</span>
          <h2 className={styles.statValue}>{stats.totalRuns}</h2>
          <span className={styles.statSubLabel}>
            {d.t('manualRuns')}: {stats.manualRunsCount} | {d.t('scheduledRuns')}: {stats.scheduledRunsCount}
          </span>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-success-rate">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <CheckCircle2 size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('successRate')}</span>
          <h2 className={styles.statValue}>{stats.overallSuccessRate}%</h2>
          <span className={styles.statSubLabel}>
            <span style={{ color: 'var(--semi-color-success)', fontWeight: 600 }}>{d.t('passedCases')}: {stats.passedTestCases}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>|</span>
            <span style={{ color: 'var(--semi-color-danger)', fontWeight: 600 }}>{d.t('failedCases')}: {stats.failedTestCases}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>|</span>
            <span style={{ color: 'var(--semi-color-text-2)' }}>{d.t('totalCases')}: {stats.totalTestCases}</span>
          </span>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-failed">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <XCircle size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('failedRuns')}</span>
          <h2 className={styles.statValue}>{stats.failedRunsCount}</h2>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-active">
        <div className={styles.statIconWrapper} style={{ backgroundColor: stats.activeRunsCount > 0 ? 'rgba(113, 113, 122, 0.16)' : 'rgba(113, 113, 122, 0.07)', color: '#71717a' }}>
          <RotateCw size={20} className={stats.activeRunsCount > 0 ? styles.spinIcon : ''} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('activeQueue')}</span>
          <h2 className={styles.statValue}>{stats.activeRunsCount}</h2>
        </div>
      </div>
    </section>
  )
}
