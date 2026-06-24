import { MouseEvent, ReactNode } from 'react'
import { Button, Tooltip, Tag, Badge } from '@douyinfe/semi-ui'
import {
  IconFolder,
  IconActivity,
  IconPlay,
  IconEdit,
  IconClock,
  IconDelete,
  IconSetting,
  IconPlus
} from '@douyinfe/semi-icons'
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
  setIsAddSuiteModalOpen: (value: boolean) => void
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
  setIsAddSuiteModalOpen,
}: ProjectSidebarProps) {
  return (
    <div className={styles.sidebarCard}>
      <div className={styles.sidebarHeader} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <IconFolder style={{ color: 'var(--semi-color-primary)', marginRight: '8px', fontSize: '18px' }} />
          <h2>{t('workspaceSuites')}</h2>
        </div>
        <Tooltip content={lang === 'zh' ? '添加测试套件 / 绑定项目' : 'Add Test Suite / Link Project'}>
          <Button
            size="small"
            theme="light"
            type="primary"
            shape="circle"
            icon={<IconPlus style={{ fontSize: '12px' }} />}
            onClick={(e) => {
              e.stopPropagation()
              setIsAddSuiteModalOpen(true)
            }}
            style={{
              width: '20px',
              height: '20px',
              minWidth: '20px',
              padding: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          />
        </Tooltip>
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
            <IconActivity style={{ color: selectedSuiteFilter === null ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)', marginRight: '8px' }} />
            <span>{t('allSuites')}</span>
          </div>
          <Badge
            count={runs.length}
            overflowCount={999}
            style={{
              backgroundColor: selectedSuiteFilter === null ? 'var(--semi-color-primary-light-default)' : 'var(--semi-color-fill-1)',
              color: selectedSuiteFilter === null ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)',
              border: 'none',
              marginLeft: '8px'
            }}
          />
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
                    <IconFolder style={{ color: isFiltered ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)', marginRight: '8px' }} />
                    <span className={styles.suiteNameText} title={suite}>{suite}</span>
                  </div>
                  <div className={styles.sidebarItemActions} onClick={(e) => e.stopPropagation()}>
                    <Badge
                      count={suiteRunsCount}
                      overflowCount={999}
                      style={{
                        backgroundColor: isFiltered ? 'var(--semi-color-primary-light-default)' : 'var(--semi-color-fill-1)',
                        color: isFiltered ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)',
                        border: 'none'
                      }}
                    />
                    <Tooltip content={t('quickTrigger')}>
                      <Button
                        size="small"
                        theme="light"
                        type="primary"
                        shape="circle"
                        icon={<IconPlay style={{ fontSize: '10px' }} />}
                        onClick={() => {
                          setTestsPath(suite)
                          setIsTriggerModalOpen(true)
                        }}
                        style={{
                          width: '18px',
                          height: '18px',
                          minWidth: '18px',
                          padding: 0,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                        }}
                      />
                    </Tooltip>
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
                          <Tooltip key={`empty-${i}`} content={lang === 'zh' ? '无执行记录' : 'No execution'}>
                            <span 
                              className={`${styles.historyDot} ${styles.dotEmpty}`} 
                            />
                          </Tooltip>
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
                          <Tooltip key={run.id} content={tooltip}>
                            <span
                              className={`${styles.historyDot} ${dotClass}`}
                              role="button"
                              tabIndex={0}
                              onClick={() => setSelectedRunId(run.id)}
                              onKeyDown={activateOnKey(() => setSelectedRunId(run.id))}
                            />
                          </Tooltip>
                        );
                      });

                      return (
                        <div key={profile.id} className={styles.nestedProfileItem} title={profile.description || ''}>
                          {/* Row 1: Profile Main Info and Actions */}
                          <div className={styles.nestedProfileMainRow}>
                            <div className={styles.nestedProfileInfo}>
                              <IconSetting style={{ fontSize: '12px', color: 'var(--semi-color-text-2)', marginRight: '4px' }} />
                              <span className={styles.nestedProfileName}>{profile.name}</span>
                              <Tag
                                size="small"
                                color={(profile.runner || 'pytest') === 'playwright' ? 'blue' : 'green'}
                                type="light"
                                style={{
                                  fontSize: '10px',
                                  marginLeft: '6px',
                                  padding: '0 4px',
                                  borderRadius: '4px',
                                  height: '16px',
                                  lineHeight: '14px',
                                }}
                              >
                                {profile.runner || 'pytest'}
                              </Tag>
                              {isSchedActive && (
                                <Tooltip content={lang === 'zh' ? `定时已启用: ${existingSched?.cron_expression}` : `Schedule active: ${existingSched?.cron_expression}`}>
                                  <span className={styles.activeScheduleIndicator} />
                                </Tooltip>
                              )}
                            </div>
                            <div className={styles.nestedProfileActions}>
                              <Tooltip content={lang === 'zh' ? '立即执行' : 'Instant Run'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type="tertiary"
                                  icon={<IconPlay style={{ fontSize: '10px' }} />}
                                  onClick={() => handleTriggerProfile(profile)}
                                  style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                                />
                              </Tooltip>
                              <Tooltip content={lang === 'zh' ? '编辑方案内容' : 'Edit Profile'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type="tertiary"
                                  icon={<IconEdit style={{ fontSize: '10px' }} />}
                                  onClick={() => handleOpenEditProfile(profile)}
                                  style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                                />
                              </Tooltip>
                              <Tooltip content={lang === 'zh' ? '配置定时调度' : 'Configure Schedule'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type={isSchedActive ? "primary" : "tertiary"}
                                  icon={<IconClock style={{ fontSize: '10px' }} />}
                                  onClick={() => handleOpenScheduleModal(profile)}
                                  style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                                />
                              </Tooltip>
                              <Tooltip content={lang === 'zh' ? '删除方案' : 'Delete Profile'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type="danger"
                                  icon={<IconDelete style={{ fontSize: '10px' }} />}
                                  onClick={(e) => handleDeleteProfile(profile.id, e)}
                                  style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                                />
                              </Tooltip>
                            </div>
                          </div>

                          {/* Row 2: Performance metrics and historical circles */}
                          <div className={styles.nestedProfileStatsRow}>
                            {stats.hasRuns ? (
                              <Tag
                                size="small"
                                color={stats.passRate >= 80 ? 'green' : stats.passRate >= 50 ? 'amber' : 'red'}
                                style={{ fontSize: '10px', height: '16px', padding: '0 4px', borderRadius: '4px' }}
                              >
                                {stats.passRate}% {lang === 'zh' ? '通过率' : 'Pass'}
                              </Tag>
                            ) : (
                              <Tag
                                size="small"
                                color="grey"
                                style={{ fontSize: '10px', height: '16px', padding: '0 4px', borderRadius: '4px' }}
                              >
                                {lang === 'zh' ? '暂无记录' : 'No runs'}
                              </Tag>
                            )}
                            <div className={styles.profileHistoryDots}>
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
