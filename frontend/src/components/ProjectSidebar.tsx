import { ReactNode } from 'react'
import { Button, Tooltip, Tag, Badge } from '@douyinfe/semi-ui'
import {
  IconFolder,
  IconActivity,
  IconPlay,
  IconEdit,
  IconClock,
  IconDelete,
  IconSetting,
  IconPlus,
  IconRefresh,
  IconDownload
} from '@douyinfe/semi-icons'
import styles from '../App.module.css'
import { activateOnKey } from '../a11y'
import { useDashboard } from '../hooks/DashboardContext'

/** Left column: "All Suites" selector + per-suite cards with nested saved-profile
 *  rows (pass-rate badge, last-5 history dots, run/edit/schedule/delete actions).
 *  Run stats are computed inline via d.runs.runs; this component displays. */
export function ProjectSidebar() {
  const d = useDashboard()
  // name → source/repo/ref, so each suite row can render source-aware actions.
  const suiteInfoByName = new Map(d.suites.suites.map((s) => [s.name, s]))
  return (
    <div className={styles.sidebarCard}>
      <div className={styles.sidebarHeader} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <IconFolder style={{ color: 'var(--semi-color-primary)', marginRight: '8px', fontSize: '18px' }} />
          <h2>{d.t('workspaceSuites')}</h2>
        </div>
        <Tooltip content={d.lang === 'zh' ? '添加测试套件 / 绑定项目' : 'Add Test Suite / Link Project'}>
          <Button
            size="small"
            theme="light"
            type="primary"
            shape="circle"
            data-testid="open-add-suite-button"
            icon={<IconPlus style={{ fontSize: '12px' }} />}
            aria-label={d.lang === 'zh' ? '添加测试套件 / 绑定项目' : 'Add Test Suite / Link Project'}
            onClick={(e) => {
              e.stopPropagation()
              d.setIsAddSuiteModalOpen(true)
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
          className={`${styles.sidebarItem} ${d.selectedSuiteFilter === null ? styles.sidebarItemActive : ''}`}
          role="button"
          tabIndex={0}
          onClick={() => {
            d.setSelectedSuiteFilter(null)
            d.setSelectedProfileFilter(null)
          }}
          onKeyDown={activateOnKey(() => {
            d.setSelectedSuiteFilter(null)
            d.setSelectedProfileFilter(null)
          })}
        >
          <div className={styles.sidebarItemMain}>
            <IconActivity style={{ color: d.selectedSuiteFilter === null ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)', marginRight: '8px' }} />
            <span>{d.t('allSuites')}</span>
          </div>
          <Badge
            count={d.runs.runs.length}
            overflowCount={999}
            style={{
              backgroundColor: d.selectedSuiteFilter === null ? 'var(--semi-color-primary-light-default)' : 'var(--semi-color-fill-1)',
              color: d.selectedSuiteFilter === null ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)',
              border: 'none',
              marginLeft: '8px'
            }}
          />
        </div>

        {/* Scanned test directories */}
        {d.suites.tests.length === 0 ? (
          <div className={styles.sidebarEmpty}>
            <p>{d.t('noSuitesScanned')}</p>
          </div>
        ) : (
          d.suites.tests.map((suite) => {
            const isFiltered = d.selectedSuiteFilter === suite
            const suiteRunsCount = d.runs.runs.filter(r => r.tests_path === suite).length
            const suiteProfiles = d.profiles.profiles.filter(p => p.tests_path === suite)
            const isGit = suiteInfoByName.get(suite)?.source === 'git'
            return (
              <div 
                key={suite}
                className={styles.sidebarItemContainer}
              >
                <div
                  className={`${styles.sidebarItemRow} ${isFiltered ? styles.sidebarItemRowActive : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => {
                    d.setSelectedSuiteFilter(suite)
                    d.setSelectedProfileFilter(null)
                  }}
                  onKeyDown={activateOnKey(() => {
                    d.setSelectedSuiteFilter(suite)
                    d.setSelectedProfileFilter(null)
                  })}
                >
                  <div className={styles.sidebarItemMain}>
                    <IconFolder style={{ color: isFiltered ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)', marginRight: '8px' }} />
                    <span className={styles.suiteNameText} title={suite}>{suite}</span>
                    <Tag
                      size="small"
                      color={isGit ? 'blue' : 'grey'}
                      type="light"
                      style={{ fontSize: '10px', marginLeft: '6px', padding: '0 4px', height: '16px', lineHeight: '14px', flexShrink: 0 }}
                    >
                      {isGit ? 'git' : 'local'}
                    </Tag>
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
                    <Tooltip content={d.t('quickTrigger')}>
                      <Button
                        size="small"
                        theme="light"
                        type="primary"
                        shape="circle"
                        icon={<IconPlay style={{ fontSize: '10px' }} />}
                        aria-label={d.t('quickTrigger')}
                        onClick={() => {
                          d.form.setTestsPath(suite)
                          d.setIsTriggerModalOpen(true)
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
                    {isGit && (
                      <Tooltip content={d.lang === 'zh' ? '更新 (git pull)' : 'Update (git pull)'}>
                        <Button
                          size="small"
                          theme="borderless"
                          type="tertiary"
                          data-testid={`suite-update-${suite}`}
                          icon={<IconRefresh style={{ fontSize: '10px' }} />}
                          aria-label={d.lang === 'zh' ? '更新 (git pull)' : 'Update (git pull)'}
                          onClick={() => d.suites.handlePullSuite(suite)}
                          style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                        />
                      </Tooltip>
                    )}
                    {isGit && (
                      <Tooltip content={d.lang === 'zh' ? '准备依赖 (npm ci)' : 'Prepare deps (npm ci)'}>
                        <Button
                          size="small"
                          theme="borderless"
                          type="tertiary"
                          data-testid={`suite-prepare-${suite}`}
                          icon={<IconDownload style={{ fontSize: '10px' }} />}
                          aria-label={d.lang === 'zh' ? '准备依赖 (npm ci)' : 'Prepare deps (npm ci)'}
                          onClick={() => d.suites.handlePrepareSuite(suite)}
                          style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                        />
                      </Tooltip>
                    )}
                    <Tooltip content={d.lang === 'zh' ? '移除套件' : 'Remove suite'}>
                      <Button
                        size="small"
                        theme="borderless"
                        type="danger"
                        data-testid={`suite-remove-${suite}`}
                        icon={<IconDelete style={{ fontSize: '10px' }} />}
                        aria-label={d.lang === 'zh' ? '移除套件' : 'Remove suite'}
                        onClick={() => d.suites.handleDeleteSuite(suite, () => {
                          if (d.selectedSuiteFilter===suite) {
                            d.setSelectedSuiteFilter(null)
                            d.setSelectedProfileFilter(null)
                          }
                        })}
                        style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                      />
                    </Tooltip>
                  </div>
                </div>

                {suiteProfiles.length > 0 && (
                  <div className={styles.nestedProfileList} onClick={(e) => e.stopPropagation()}>
                    {suiteProfiles.map(profile => {
                      const existingSched = d.schedules.schedules.find(s => s.profile_id === profile.id);
                      const isSchedActive = existingSched?.enabled;
                      const isProfileFiltered = d.selectedProfileFilter === profile.id;

                      // Compute dynamic run statistics
                      const profileRuns = d.runs.runs.filter((r) => r.profile_id === profile.id)
                      const finishedRuns = profileRuns.filter(
                        (r) => r.status === 'completed' || r.status === 'failed' || r.status === 'timeout',
                      )
                      let passRate = 0
                      if (finishedRuns.length > 0) {
                        const passedCount = finishedRuns.filter(
                          (r) => r.status === 'completed' && r.passed === true,
                        ).length
                        passRate = Math.round((passedCount / finishedRuns.length) * 100)
                      }
                      const stats = { passRate, hasRuns: finishedRuns.length > 0, last5: profileRuns.slice(0, 5).reverse() }
                      const dots: ReactNode[] = [];

                      // Pad with empty dots to keep layout consistent at 5 dots
                      for (let i = 0; i < 5 - stats.last5.length; i++) {
                        dots.push(
                          <Tooltip key={`empty-${i}`} content={d.lang === 'zh' ? '无执行记录' : 'No execution'}>
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
                          tooltip = d.lang === 'zh' ? '运行中...' : 'Running...';
                        } else if (run.status === 'completed' && run.passed) {
                          dotClass = styles.dotPass;
                          tooltip = d.lang === 'zh' 
                            ? `已通过 (耗时: ${run.summary?.duration_ms ? Math.round(run.summary.duration_ms / 1000) : 0}秒)\n${new Date(run.created_at).toLocaleString()}` 
                            : `Passed (${run.summary?.duration_ms ? Math.round(run.summary.duration_ms / 1000) : 0}s)\n${new Date(run.created_at).toLocaleString()}`;
                        } else {
                          dotClass = styles.dotFail;
                          tooltip = d.lang === 'zh' 
                            ? `未通过\n${new Date(run.created_at).toLocaleString()}` 
                            : `Failed\n${new Date(run.created_at).toLocaleString()}`;
                        }

                        dots.push(
                          <Tooltip key={run.id} content={tooltip}>
                            <span
                              className={`${styles.historyDot} ${dotClass}`}
                              role="button"
                              tabIndex={0}
                              onClick={() => d.runs.setSelectedRunId(run.id)}
                              onKeyDown={activateOnKey(() => d.runs.setSelectedRunId(run.id))}
                            />
                          </Tooltip>
                        );
                      });

                      return (
                        <div
                          key={profile.id}
                          className={`${styles.nestedProfileItem} ${isProfileFiltered ? styles.nestedProfileItemActive : ''}`}
                          title={profile.description || ''}
                          data-testid={`profile-filter-${profile.id}`}
                        >
                          {/* Row 1: Profile Main Info and Actions */}
                          <div className={styles.nestedProfileMainRow}>
                            <div
                              className={styles.nestedProfileInfo}
                              role="button"
                              tabIndex={0}
                              onClick={() => {
                                d.setSelectedSuiteFilter(profile.tests_path)
                                d.setSelectedProfileFilter(profile.id)
                              }}
                              onKeyDown={activateOnKey(() => {
                                d.setSelectedSuiteFilter(profile.tests_path)
                                d.setSelectedProfileFilter(profile.id)
                              })}
                            >
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
                                <Tooltip content={d.lang === 'zh' ? `定时已启用: ${existingSched?.cron_expression}` : `Schedule active: ${existingSched?.cron_expression}`}>
                                  <span className={styles.activeScheduleIndicator} />
                                </Tooltip>
                              )}
                            </div>
                            <div className={styles.nestedProfileActions} onClick={(e) => e.stopPropagation()}>
                              <Tooltip content={d.lang === 'zh' ? '立即执行' : 'Instant Run'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type="tertiary"
                                  icon={<IconPlay style={{ fontSize: '10px' }} />}
                                  aria-label={d.lang === 'zh' ? '立即执行' : 'Instant Run'}
                                  onClick={() => d.profiles.handleTriggerProfile(profile, (runId) => { d.runs.fetchRuns(); d.runs.setSelectedRunId(runId) })}
                                  style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                                />
                              </Tooltip>
                              <Tooltip content={d.lang === 'zh' ? '编辑方案内容' : 'Edit Profile'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type="tertiary"
                                  icon={<IconEdit style={{ fontSize: '10px' }} />}
                                  aria-label={d.lang === 'zh' ? '编辑方案内容' : 'Edit Profile'}
                                  onClick={() => { d.form.openEditProfile(profile); d.setSelectedFiles(profile.selected_files||[]); d.setSelectedMarkers(profile.selected_markers||[]); d.setIsTriggerModalOpen(true) }}
                                  style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                                />
                              </Tooltip>
                              <Tooltip content={d.lang === 'zh' ? '配置定时调度' : 'Configure Schedule'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type={isSchedActive ? "primary" : "tertiary"}
                                  icon={<IconClock style={{ fontSize: '10px' }} />}
                                  aria-label={d.lang === 'zh' ? '配置定时调度' : 'Configure Schedule'}
                                  onClick={() => d.schedules.handleOpenScheduleModal(profile)}
                                  style={{ padding: '2px', height: '18px', width: '18px', minWidth: '18px' }}
                                  data-testid="open-schedule-button"
                                />
                              </Tooltip>
                              <Tooltip content={d.lang === 'zh' ? '删除方案' : 'Delete Profile'}>
                                <Button
                                  size="small"
                                  theme="borderless"
                                  type="danger"
                                  icon={<IconDelete style={{ fontSize: '10px' }} />}
                                  aria-label={d.lang === 'zh' ? '删除方案' : 'Delete Profile'}
                                  onClick={(e) => {
                                    d.profiles.handleDeleteProfile(profile.id, e);
                                    if (d.form.selectedProfileId===profile.id) d.form.setSelectedProfileId('')
                                    if (d.selectedProfileFilter===profile.id) d.setSelectedProfileFilter(null)
                                  }}
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
                                {stats.passRate}% {d.lang === 'zh' ? '通过率' : 'Pass'}
                              </Tag>
                            ) : (
                              <Tag
                                size="small"
                                color="grey"
                                style={{ fontSize: '10px', height: '16px', padding: '0 4px', borderRadius: '4px' }}
                              >
                                {d.lang === 'zh' ? '暂无记录' : 'No runs'}
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
