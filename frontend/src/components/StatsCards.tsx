import { Activity, CheckCircle2, RotateCw, XCircle } from 'lucide-react'
import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'

/** Dashboard header summary cards. Reads stats from DashboardContext. */
export function StatsCards() {
  const d = useDashboard()

  return (
    <section className={styles.statsContainer}>
      <div className={styles.statCard} data-testid="stat-total">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <Activity size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('totalExecutions')}</span>
          <h2 className={styles.statValue}>{d.runs.totalRuns}</h2>
          <span className={styles.statSubLabel}>
            {d.t('manualRuns')}: {d.runs.manualRunsCount} | {d.t('scheduledRuns')}: {d.runs.scheduledRunsCount}
          </span>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-success-rate">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <CheckCircle2 size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('successRate')}</span>
          <h2 className={styles.statValue}>{d.runs.overallSuccessRate}%</h2>
          <span className={styles.statSubLabel}>
            <span style={{ color: 'var(--semi-color-success)', fontWeight: 600 }}>{d.t('passedCases')}: {d.runs.passedTestCases}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>|</span>
            <span style={{ color: 'var(--semi-color-danger)', fontWeight: 600 }}>{d.t('failedCases')}: {d.runs.failedTestCases}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>|</span>
            <span style={{ color: 'var(--semi-color-text-2)' }}>{d.t('totalCases')}: {d.runs.totalTestCases}</span>
          </span>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-failed">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <XCircle size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('failedRuns')}</span>
          <h2 className={styles.statValue}>{d.runs.failedRunsCount}</h2>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-active">
        <div className={styles.statIconWrapper} style={{ backgroundColor: d.runs.activeRunsCount > 0 ? 'rgba(113, 113, 122, 0.16)' : 'rgba(113, 113, 122, 0.07)', color: '#71717a' }}>
          <RotateCw size={20} className={d.runs.activeRunsCount > 0 ? styles.spinIcon : ''} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{d.t('activeQueue')}</span>
          <h2 className={styles.statValue}>{d.runs.activeRunsCount}</h2>
        </div>
      </div>
    </section>
  )
}
