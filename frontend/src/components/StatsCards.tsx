import { Activity, CheckCircle2, RotateCw, XCircle } from 'lucide-react'
import styles from '../App.module.css'
import type { TranslationKey } from '../i18n'

interface StatsCardsProps {
  t: (key: TranslationKey) => string
  totalRuns: number
  manualRunsCount: number
  scheduledRunsCount: number
  overallSuccessRate: string
  passedTestCases: number
  failedTestCases: number
  totalTestCases: number
  failedRunsCount: number
  activeRunsCount: number
}

/** Dashboard header summary cards: total / success-rate / failed / active-queue. */
export function StatsCards({
  t,
  totalRuns,
  manualRunsCount,
  scheduledRunsCount,
  overallSuccessRate,
  passedTestCases,
  failedTestCases,
  totalTestCases,
  failedRunsCount,
  activeRunsCount
}: StatsCardsProps) {
  return (
    <section className={styles.statsContainer}>
      <div className={styles.statCard} data-testid="stat-total">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <Activity size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{t('totalExecutions')}</span>
          <h2 className={styles.statValue}>{totalRuns}</h2>
          <span className={styles.statSubLabel}>
            {t('manualRuns')}: {manualRunsCount} | {t('scheduledRuns')}: {scheduledRunsCount}
          </span>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-success-rate">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <CheckCircle2 size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{t('successRate')}</span>
          <h2 className={styles.statValue}>{overallSuccessRate}%</h2>
          <span className={styles.statSubLabel}>
            <span style={{ color: 'var(--semi-color-success)', fontWeight: 600 }}>{t('passedCases')}: {passedTestCases}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>|</span>
            <span style={{ color: 'var(--semi-color-danger)', fontWeight: 600 }}>{t('failedCases')}: {failedTestCases}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>|</span>
            <span style={{ color: 'var(--semi-color-text-2)' }}>{t('totalCases')}: {totalTestCases}</span>
          </span>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-failed">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(113, 113, 122, 0.1)', color: '#71717a' }}>
          <XCircle size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{t('failedRuns')}</span>
          <h2 className={styles.statValue}>
            {failedRunsCount}
          </h2>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-active">
        <div className={styles.statIconWrapper} style={{ backgroundColor: activeRunsCount > 0 ? 'rgba(113, 113, 122, 0.16)' : 'rgba(113, 113, 122, 0.07)', color: '#71717a' }}>
          <RotateCw size={20} className={activeRunsCount > 0 ? styles.spinIcon : ''} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{t('activeQueue')}</span>
          <h2 className={styles.statValue}>{activeRunsCount}</h2>
        </div>
      </div>
    </section>
  )
}
