import { Activity, CheckCircle2, RotateCw, XCircle } from 'lucide-react'
import styles from '../App.module.css'
import type { TranslationKey } from '../i18n'

interface StatsCardsProps {
  t: (key: TranslationKey) => string
  totalRuns: number
  overallSuccessRate: string
  failedRunsCount: number
  activeRunsCount: number
}

/** Dashboard header summary cards: total / success-rate / failed / active-queue. */
export function StatsCards({ t, totalRuns, overallSuccessRate, failedRunsCount, activeRunsCount }: StatsCardsProps) {
  return (
    <section className={styles.statsContainer}>
      <div className={styles.statCard} data-testid="stat-total">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(99, 102, 241, 0.1)', color: '#818cf8' }}>
          <Activity size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{t('totalExecutions')}</span>
          <h2 className={styles.statValue}>{totalRuns}</h2>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-success-rate">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(16, 185, 129, 0.1)', color: '#10b981' }}>
          <CheckCircle2 size={20} />
        </div>
        <div className={styles.statDetails}>
          <span className={styles.statLabel}>{t('successRate')}</span>
          <h2 className={styles.statValue}>{overallSuccessRate}%</h2>
        </div>
      </div>

      <div className={styles.statCard} data-testid="stat-failed">
        <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)', color: '#ef4444' }}>
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
        <div className={`${styles.statIconWrapper} ${activeRunsCount > 0 ? styles.pulseGlow : ''}`} style={{ backgroundColor: activeRunsCount > 0 ? 'rgba(245, 158, 11, 0.15)' : 'rgba(245, 158, 11, 0.05)', color: '#f59e0b' }}>
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
