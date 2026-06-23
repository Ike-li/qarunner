import { Activity, Clock, FolderGit2, Pencil, Play, SlidersHorizontal, X } from 'lucide-react'
import type { MouseEvent, ReactNode } from 'react'
import styles from '../App.module.css'
import { activateOnKey } from '../a11y'
import type { Lang, TranslationKey } from '../i18n'
import type { Profile, Run, Schedule } from '../types'

interface ProjectSidebarProps {
  t: (key: TranslationKey) => string
  lang: Lang
  runs: Run[]
  tests: string[]
  profiles: Profile[]
  schedules: Schedule[]
  selectedSuiteFilter: string | null
  setSelectedSuiteFilter: (value: string | null) => void
  setTestsPath: (value: string) => void
  setIsTriggerModalOpen: (value: boolean) => void
  setSelectedRunId: (value: string | null) => void
  getProfileRunStats: (profile: Profile) => { passRate: number; hasRuns: boolean; last5: Run[] }
  handleTriggerProfile: (profile: Profile) => void
  handleOpenEditProfile: (profile: Profile) => void
  handleOpenScheduleModal: (profile: Profile) => void
  handleDeleteProfile: (profileId: string, e?: MouseEvent) => void
}

/** Left column: "All Suites" selector + per-suite cards with nested saved-profile
 *  rows (pass-rate badge, last-5 history dots, run/edit/schedule/delete actions).
 *  Run stats are derived in App via getProfileRunStats; this component displays. */
export function ProjectSidebar({
  t,
  lang,
  runs,
  tests,
  profiles,
  schedules,
  selectedSuiteFilter,
  setSelectedSuiteFilter,
  setTestsPath,
  setIsTriggerModalOpen,
  setSelectedRunId,
  getProfileRunStats,
  handleTriggerProfile,
  handleOpenEditProfile,
  handleOpenScheduleModal,
  handleDeleteProfile,
}: ProjectSidebarProps) {
  return (
    <div className={styles.sidebarCard}>
      <div className={styles.sidebarHeader}>
        <FolderGit2 size={16} className={styles.iconAccent} />
        <h2>{t('workspaceSuites')}</h2>
      </div>
      <div className={styles.sidebarContent}>
        {/* "All Suites" selector */}
        <div
          className={`${styles.sidebarItem} ${selectedSuiteFilter === null ? styles.sidebarItemActive : ''}`}
          role="button"
          tabIndex={0}
          onClick={() => setSelectedSuiteFilter(null)}
          onKeyDown={activateOnKey(() => setSelectedSuiteFilter(null))}
        >
          <div className={styles.sidebarItemMain}>
            <Activity size={14} className={styles.sidebarIcon} />
            <span>{t('allSuites')}</span>
          </div>
          <span className={styles.suiteCountBadge}>
            {runs.length}
          </span>
        </div>

        {/* Scanned test directories */}
        {tests.length === 0 ? (
          <div className={styles.sidebarEmpty}>
            <p>{t('noSuitesScanned')}</p>
          </div>
        ) : (
          tests.map((suite) => {
            const isFiltered = selectedSuiteFilter === suite
            const suiteRunsCount = runs.filter(r => r.tests_path === suite).length
            const suiteProfiles = profiles.filter(p => p.tests_path === suite)
            return (
              <div 
                key={suite}
                className={styles.sidebarItemContainer}
              >
                <div
                  className={`${styles.sidebarItemRow} ${isFiltered ? styles.sidebarItemRowActive : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedSuiteFilter(suite)}
                  onKeyDown={activateOnKey(() => setSelectedSuiteFilter(suite))}
                >
                  <div className={styles.sidebarItemMain}>
                    <FolderGit2 size={14} className={styles.sidebarIcon} />
                    <span className={styles.suiteNameText} title={suite}>{suite}</span>
                  </div>
                  <div className={styles.sidebarItemActions} onClick={(e) => e.stopPropagation()}>
                    <span className={styles.suiteCountBadge}>
                      {suiteRunsCount}
                    </span>
                    <button
                      className={styles.quickPlayButton}
                      title={t('quickTrigger')}
                      aria-label={t('quickTrigger')}
                      onClick={() => {
                        setTestsPath(suite)
                        setIsTriggerModalOpen(true)
                      }}
                    >
                      <Play size={10} fill="currentColor" />
                    </button>
                  </div>
                </div>

                {suiteProfiles.length > 0 && (
                  <div className={styles.nestedProfileList} onClick={(e) => e.stopPropagation()}>
                    {suiteProfiles.map(profile => {
                      const existingSched = schedules.find(s => s.profile_id === profile.id);
                      const isSchedActive = existingSched?.enabled;

                      // Compute dynamic run statistics
                      const stats = getProfileRunStats(profile);
                      const dots: ReactNode[] = [];

                      // Pad with empty dots to keep layout consistent at 5 dots
                      for (let i = 0; i < 5 - stats.last5.length; i++) {
                        dots.push(
                          <span 
                            key={`empty-${i}`} 
                            className={`${styles.historyDot} ${styles.dotEmpty}`} 
                            title={lang === 'zh' ? '无执行记录' : 'No execution'} 
                          />
                        );
                      }

                      // Fill with recent execution colored dots
                      stats.last5.forEach(run => {
                        let dotClass = styles.dotEmpty;
                        let tooltip = '';
                        if (run.status === 'running' || run.status === 'queued') {
                          dotClass = styles.dotRunning;
                          tooltip = lang === 'zh' ? '运行中...' : 'Running...';
                        } else if (run.status === 'completed' && run.passed) {
                          dotClass = styles.dotPass;
                          tooltip = lang === 'zh' 
                            ? `已通过 (耗时: ${run.summary?.duration_ms ? Math.round(run.summary.duration_ms / 1000) : 0}秒)\n${new Date(run.created_at).toLocaleString()}` 
                            : `Passed (${run.summary?.duration_ms ? Math.round(run.summary.duration_ms / 1000) : 0}s)\n${new Date(run.created_at).toLocaleString()}`;
                        } else {
                          dotClass = styles.dotFail;
                          tooltip = lang === 'zh' 
                            ? `未通过\n${new Date(run.created_at).toLocaleString()}` 
                            : `Failed\n${new Date(run.created_at).toLocaleString()}`;
                        }

                        dots.push(
                          <span
                            key={run.id}
                            className={`${styles.historyDot} ${dotClass}`}
                            title={tooltip}
                            role="button"
                            tabIndex={0}
                            onClick={() => setSelectedRunId(run.id)}
                            onKeyDown={activateOnKey(() => setSelectedRunId(run.id))}
                          />
                        );
                      });

                      return (
                        <div key={profile.id} className={styles.nestedProfileItem} title={profile.description || ''}>
                          {/* Row 1: Profile Main Info and Actions */}
                          <div className={styles.nestedProfileMainRow}>
                            <div className={styles.nestedProfileInfo}>
                              <SlidersHorizontal size={11} className={styles.nestedProfileIcon} />
                              <span className={styles.nestedProfileName}>{profile.name}</span>
                              {isSchedActive && (
                                <span className={styles.activeScheduleIndicator} title={lang === 'zh' ? `定时已启用: ${existingSched?.cron_expression}` : `Schedule active: ${existingSched?.cron_expression}`} />
                              )}
                            </div>
                            <div className={styles.nestedProfileActions}>
                              <button
                                className={styles.nestedProfilePlayButton}
                                title={lang === 'zh' ? '立即执行' : 'Instant Run'}
                                aria-label={lang === 'zh' ? '立即执行' : 'Instant Run'}
                                onClick={() => handleTriggerProfile(profile)}
                              >
                                <Play size={8} fill="currentColor" />
                              </button>
                              <button
                                className={styles.nestedProfileEditButton}
                                title={lang === 'zh' ? '编辑方案内容' : 'Edit Profile'}
                                aria-label={lang === 'zh' ? '编辑方案内容' : 'Edit Profile'}
                                onClick={() => handleOpenEditProfile(profile)}
                              >
                                <Pencil size={8} />
                              </button>
                              <button
                                className={`${styles.nestedProfileClockButton} ${isSchedActive ? styles.nestedProfileClockButtonActive : ''}`}
                                title={lang === 'zh' ? '配置定时调度' : 'Configure Schedule'}
                                aria-label={lang === 'zh' ? '配置定时调度' : 'Configure Schedule'}
                                onClick={() => handleOpenScheduleModal(profile)}
                              >
                                <Clock size={8} />
                              </button>
                              <button
                                className={styles.nestedProfileDeleteButton}
                                title={lang === 'zh' ? '删除方案' : 'Delete Profile'}
                                aria-label={lang === 'zh' ? '删除方案' : 'Delete Profile'}
                                onClick={(e) => handleDeleteProfile(profile.id, e)}
                              >
                                <X size={8} />
                              </button>
                            </div>
                          </div>

                          {/* Row 2: Performance metrics and historical circles */}
                          <div className={styles.nestedProfileStatsRow}>
                            {stats.hasRuns ? (
                              <span className={`${styles.profilePassRateBadge} ${stats.passRate >= 80 ? styles.badgeHighPass : stats.passRate >= 50 ? styles.badgeMediumPass : styles.badgeLowPass}`}>
                                {stats.passRate}% {lang === 'zh' ? '通过率' : 'Pass'}
                              </span>
                            ) : (
                              <span className={styles.profileNoRunsBadge}>
                                {lang === 'zh' ? '暂无记录' : 'No runs'}
                              </span>
                            )}
                            <div className={styles.profileHistoryDots} title={lang === 'zh' ? '最近 5 次执行历史 (从左至右: 较早 -> 最新，点击圆点可载入日志)' : 'Last 5 runs (left to right: older -> newest, click to load logs)'}>
                              {dots}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
