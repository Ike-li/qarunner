import { Activity, BarChart3, Box, CheckCircle2, ChevronRight, Clock, Cpu, FolderGit2, Lock, Play, RotateCw, Sparkles, Unlock, Users, XCircle } from 'lucide-react'
import type { MouseEvent } from 'react'
import styles from '../App.module.css'
import { formatDuration } from '../logUtils'
import type { Lang, TranslationKey } from '../i18n'
import type { Run } from '../types'

interface RunsTableProps {
  t: (key: TranslationKey) => string
  lang: Lang
  runs: Run[]
  filteredRuns: Run[]
  loading: boolean
  logFilterTab: 'All' | 'Manual' | 'Scheduled'
  setLogFilterTab: (value: 'All' | 'Manual' | 'Scheduled') => void
  selectedRunId: string | null
  setSelectedRunId: (value: string | null) => void
  setIsTriggerModalOpen: (value: boolean) => void
  fetchRuns: () => void
  handleToggleLock: (runId: string, e: MouseEvent) => void
  formatDate: (isoStr: string | null) => string
}

/** Right column: execution-records table with segmented All/Manual/Scheduled
 *  filter tabs, refresh, loading/empty states, and one row per run
 *  (status/engine/owner badges, results, pass-rate bar, duration, lock toggle). */
export function RunsTable({
  t,
  lang,
  runs,
  filteredRuns,
  loading,
  logFilterTab,
  setLogFilterTab,
  selectedRunId,
  setSelectedRunId,
  setIsTriggerModalOpen,
  fetchRuns,
  handleToggleLock,
  formatDate,
}: RunsTableProps) {
  return (
    <div className={styles.tableCard}>
      <div className={styles.tableHeader}>
        <div className={styles.tableTitleGroup}>
          <BarChart3 size={18} className={styles.iconMuted} />
          <h2>{t('executionRecords')}</h2>
        </div>

        {/* Segmented Filter Tab */}
        <div className={styles.logFilters}>
          <button 
            className={`${styles.logFilterButton} ${logFilterTab === 'All' ? styles.logFilterButtonActive : ''}`}
            onClick={() => setLogFilterTab('All')}
          >
            <Activity size={12} />
            <span>{lang === 'zh' ? '全部记录' : 'All Runs'}</span>
          </button>
          <button 
            className={`${styles.logFilterButton} ${logFilterTab === 'Manual' ? styles.logFilterButtonActive : ''}`}
            onClick={() => setLogFilterTab('Manual')}
          >
            <Users size={12} />
            <span>{lang === 'zh' ? '手动触发' : 'Manually Triggered'}</span>
          </button>
          <button 
            className={`${styles.logFilterButton} ${logFilterTab === 'Scheduled' ? styles.logFilterButtonActive : ''}`}
            onClick={() => setLogFilterTab('Scheduled')}
          >
            <Clock size={12} />
            <span>{lang === 'zh' ? '定时触发' : 'Scheduled Runs'}</span>
          </button>
        </div>

        <button 
          className={styles.refreshIconButton} 
          onClick={fetchRuns}
          title={t('refreshLogs')}
        >
          <RotateCw size={16} />
        </button>
      </div>

      {loading && runs.length === 0 ? (
        <div className={styles.loadingState}>
          <RotateCw size={36} className={styles.spinIcon} />
          <p>{t('loadingHistory')}</p>
        </div>
      ) : runs.length === 0 ? (
        <div className={styles.emptyState}>
          <Sparkles size={48} className={styles.iconSparkle} />
          <h3>{t('noRunsTitle')}</h3>
          <p>{t('noRunsDesc')}</p>
          <button 
            className={styles.triggerButton}
            onClick={() => setIsTriggerModalOpen(true)}
            style={{ marginTop: '1.5rem' }}
          >
            <Play size={16} fill="currentColor" />
            <span>{t('launchFirstRun')}</span>
          </button>
        </div>
      ) : (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{t('runId')}</th>
                <th style={{ width: '50px', textAlign: 'center' }}><Lock size={12} /></th>
                <th>{t('targetSuite')}</th>
                <th>{t('status')}</th>
                <th>{t('engine')}</th>
                <th>{t('owner')}</th>
                <th>{t('results')}</th>
                <th>{t('passRate')}</th>
                <th>{t('duration')}</th>
                <th>{t('createdAt')}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredRuns.map((run) => {
                const isSelected = run.id === selectedRunId
                return (
                  <tr 
                    key={run.id}
                    className={`${styles.tableRow} ${isSelected ? styles.rowSelected : ''}`}
                    onClick={() => setSelectedRunId(run.id)}
                  >
                    <td className={styles.cellId}>
                      <code>{run.id.slice(0, 8)}</code>
                    </td>
                    <td style={{ textAlign: 'center' }} onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className={`${styles.lockButton} ${run.locked ? styles.lockButtonActive : ''}`}
                        onClick={(e) => handleToggleLock(run.id, e)}
                        title={run.locked 
                          ? (lang === 'zh' ? '已锁定 (保护文件不被清理)' : 'Locked (Protected from physical cleanup)') 
                          : (lang === 'zh' ? '未锁定 (可进行物理清理)' : 'Unlocked (Eligible for physical cleanup)')
                        }
                      >
                        {run.locked ? (
                          <Lock size={12} className={styles.lockIconActive} />
                        ) : (
                          <Unlock size={12} className={styles.lockIconInactive} />
                        )}
                      </button>
                    </td>
                    <td className={styles.cellPath}>
                      <FolderGit2 size={15} className={styles.inlineIcon} />
                      <span>{run.tests_path}</span>
                    </td>
                    <td>
                      <span className={`${styles.badge} ${styles[`badge_${run.status}`]}`}>
                        {run.status === 'queued' && <RotateCw size={12} className={styles.spinIcon} />}
                        {run.status === 'running' && <Activity size={12} className={styles.pulseIcon} />}
                        {run.status === 'completed' && run.passed && <CheckCircle2 size={12} />}
                        {run.status === 'completed' && !run.passed && <XCircle size={12} />}
                        {run.status === 'failed' && <XCircle size={12} />}
                        {run.status === 'timeout' && <Clock size={12} />}
                        <span className={styles.badgeText}>{t(`status_${run.status}`)}</span>
                      </span>
                    </td>
                    <td>
                      <span className={`${styles.engineBadge} ${
                        run.executor_mode === 'docker' ? styles.engineBadge_docker : styles.engineBadge_subprocess
                      }`}>
                        {run.executor_mode === 'docker' ? <Box size={12} className={styles.inlineIcon} /> : <Cpu size={12} className={styles.inlineIcon} />}
                        <span>{t(`engine_${run.executor_mode}`)}</span>
                      </span>
                    </td>
                    <td>
                      <span className={`${styles.ownerBadge} ${
                        run.created_by === 'system' ? styles.ownerBadge_system :
                        run.created_by === 'admin' ? styles.ownerBadge_admin : styles.ownerBadge_user
                      }`}>
                        {run.created_by}
                      </span>
                    </td>
                    <td>
                      {run.summary ? (
                        <span className={styles.summaryStats}>
                          <span className={styles.textPassed}>{run.summary.passed}</span>
                          <span className={styles.statDivider}>/</span>
                          <span className={styles.textFailed}>{run.summary.failed + run.summary.error}</span>
                          <span className={styles.statDivider}>/</span>
                          <span>{run.summary.total}</span>
                        </span>
                      ) : (
                        <span className={styles.textMuted}>-</span>
                      )}
                    </td>
                    <td>
                      {run.summary ? (
                        <div className={styles.progressContainer}>
                          <div className={styles.progressBarWrapper}>
                            <div 
                              className={`${styles.progressBar} ${run.passed ? styles.bgPassed : styles.bgFailed}`}
                              style={{ width: `${run.summary.pass_rate * 100}%` }}
                            ></div>
                          </div>
                          <span className={styles.progressText}>
                            {(run.summary.pass_rate * 100).toFixed(0)}%
                          </span>
                        </div>
                      ) : (
                        <span className={styles.textMuted}>-</span>
                      )}
                    </td>
                    <td className={styles.textMono}>
                      {formatDuration(run.summary?.duration_ms)}
                    </td>
                    <td className={styles.textMuted}>
                      {formatDate(run.created_at)}
                    </td>
                    <td className={styles.cellArrow}>
                      <ChevronRight size={16} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
