import {
  Activity, AlertTriangle, BarChart3, Box, Check, CheckCircle2, ChevronDown,
  ChevronsLeft, ChevronsRight, ChevronUp, Clock, Copy, Cpu, Download,
  ExternalLink, Maximize2, RotateCw, Search, Terminal, X, XCircle, ZoomIn, ZoomOut,
} from 'lucide-react'
import { useState } from 'react'
import type { Dispatch, ReactNode, RefObject, SetStateAction } from 'react'
import { SideSheet, Tabs, Button } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import { formatDuration } from '../logUtils'
import type { Lang, TranslationKey } from '../i18n'
import type { Run } from '../types'

interface RunDetailsDrawerProps {
  t: (key: TranslationKey) => string
  lang: Lang
  selectedRun: Run | null
  selectedRunDetails: Run | null
  streamedStdout: string
  isStreaming: boolean
  filteredStdout: string
  filteredStderr: string
  filteredStreamed: string
  detailsLoading: boolean
  drawerTab: 'logs' | 'report'
  setDrawerTab: (value: 'logs' | 'report') => void
  isDrawerExpanded: boolean
  setIsDrawerExpanded: (value: boolean) => void
  setSelectedRunId: (value: string | null) => void
  logSearchQuery: string
  setLogSearchQuery: (value: string) => void
  logLevelFilter: 'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS'
  setLogLevelFilter: (value: 'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS') => void
  terminalFontSize: number
  setTerminalFontSize: Dispatch<SetStateAction<number>>
  isTerminalHeightExpanded: boolean
  setIsTerminalHeightExpanded: (value: boolean) => void
  setIsTerminalFullscreen: (value: boolean) => void
  setIsReportFullscreen: (value: boolean) => void
  copySuccess: boolean


  copyToClipboard: (text: string) => void
  downloadLogs: (runId: string) => void
  renderFormattedLogs: (text: string) => ReactNode
  formatDate: (isoStr: string | null) => string
  terminalRef: RefObject<HTMLDivElement>
}

/** Slide-in run-details drawer: status badge, info matrix, and a logs/report tab
 *  switch. Logs tab = console terminal (search/level/font/height/fullscreen
 *  controls + line-numbered log body) plus pytest args and error stacktrace;
 *  report tab = outcome breakdown chart and the inline Allure report iframe.
 *  All state lives in App (terminal prefs via useTerminalView); this displays. */
export function RunDetailsDrawer({
  t,
  lang,
  selectedRun,
  selectedRunDetails,
  streamedStdout,
  isStreaming,
  filteredStdout,
  filteredStderr,
  filteredStreamed,
  detailsLoading,
  drawerTab,
  setDrawerTab,
  isDrawerExpanded,
  setIsDrawerExpanded,
  setSelectedRunId,
  logSearchQuery,
  setLogSearchQuery,
  logLevelFilter,
  setLogLevelFilter,
  terminalFontSize,
  setTerminalFontSize,
  isTerminalHeightExpanded,
  setIsTerminalHeightExpanded,
  setIsTerminalFullscreen,
  setIsReportFullscreen,
  copySuccess,


  copyToClipboard,
  downloadLogs,
  renderFormattedLogs,
  formatDate,
  terminalRef,
}: RunDetailsDrawerProps) {
  const [isTopCollapsed, setIsTopCollapsed] = useState(false)



  return (
    <SideSheet
      visible={!!selectedRun}
      onCancel={() => {
        setSelectedRunId(null)
        setIsDrawerExpanded(false)
      }}
      width={isDrawerExpanded ? '85%' : '640px'}
      title={
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: '1rem' }}>
          <div className={styles.drawerTitleGroup}>
            <h3 id="run-details-title" style={{ margin: 0 }}>{t('executionDetails')}</h3>
            <code>{t('id')}: {selectedRun?.id}</code>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Button
              icon={isTopCollapsed ? <ChevronDown size={18} /> : <ChevronUp size={18} />}
              onClick={() => setIsTopCollapsed(!isTopCollapsed)}
              title={isTopCollapsed ? (lang === 'zh' ? "显示顶部详情" : "Show Top Details") : (lang === 'zh' ? "折叠顶部详情" : "Collapse Top Details")}
            />
            <Button
              icon={isDrawerExpanded ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
              onClick={() => setIsDrawerExpanded(!isDrawerExpanded)}
              title={isDrawerExpanded ? (lang === 'zh' ? "收起面板" : "Collapse Panel Width") : (lang === 'zh' ? "宽屏模式" : "Expand Panel Width")}
            />
          </div>
        </div>
      }
      closable={true}
      bodyStyle={{ padding: '16px' }}
    >
      {selectedRun && (
        <div className={styles.drawerContent} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            {/* Top Big Status Badge */}
            {!isTopCollapsed && (
              <div className={styles.drawerStatusSection}>
                <span className={`${styles.drawerBadge} ${styles[`badge_${selectedRun.status}`]}`}>
                  {selectedRun.status === 'queued' && <RotateCw size={16} className={styles.spinIcon} />}
                  {selectedRun.status === 'running' && <Activity size={16} className={styles.pulseIcon} />}
                  {selectedRun.status === 'completed' && selectedRun.passed && <CheckCircle2 size={16} />}
                  {selectedRun.status === 'completed' && !selectedRun.passed && <XCircle size={16} />}
                  {selectedRun.status === 'failed' && <XCircle size={16} />}
                  {selectedRun.status === 'timeout' && <Clock size={16} />}
                  <span>{t(`status_${selectedRun.status}`).toUpperCase()}</span>
                </span>
                {selectedRun.status === 'completed' && (
                  <span className={selectedRun.passed ? styles.textSuccessGlow : styles.textDangerGlow}>
                    {selectedRun.passed ? t('allTestsPassed') : t('suiteFailed')}
                  </span>
                )}
              </div>
            )}

            {/* Info Matrix grid */}
            {!isTopCollapsed && (
              <div className={styles.infoGrid}>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('runner')}</span>
                  <span className={styles.infoValue}>{selectedRun.runner}</span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('exitCode')}</span>
                  <span className={styles.infoValue}>
                    {selectedRun.exit_code !== null ? selectedRun.exit_code : '-'}
                  </span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('triggeredBy')}</span>
                  <span className={`${styles.ownerBadge} ${
                    selectedRun.created_by === 'system' ? styles.ownerBadge_system :
                    selectedRun.created_by === 'admin' ? styles.ownerBadge_admin : styles.ownerBadge_user
                  }`} style={{ marginTop: '0.15rem', alignSelf: 'flex-start' }}>
                    {selectedRun.created_by}
                  </span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('environment')}</span>
                  <span className={`${styles.engineBadge} ${
                    selectedRun.executor_mode === 'docker' ? styles.engineBadge_docker : styles.engineBadge_subprocess
                  }`} style={{ marginTop: '0.15rem', alignSelf: 'flex-start' }}>
                    {selectedRun.executor_mode === 'docker' ? <Box size={12} className={styles.inlineIcon} /> : <Cpu size={12} className={styles.inlineIcon} />}
                    <span>{t(`engine_${selectedRun.executor_mode}`)}</span>
                  </span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('created')}</span>
                  <span className={styles.infoValue}>{formatDate(selectedRun.created_at)}</span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('finished')}</span>
                  <span className={styles.infoValue}>{formatDate(selectedRun.finished_at)}</span>
                </div>
              </div>
            )}

            {/* Tab Selector Segmented Control */}
            <Tabs activeKey={drawerTab} onChange={key => setDrawerTab(key as any)} style={{ marginBottom: '1rem' }}>
              <Tabs.TabPane
                itemKey="logs"
                tab={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                    <Terminal size={14} />
                    <span>{t('consoleLogs')}</span>
                  </span>
                }
              />
              <Tabs.TabPane
                itemKey="report"
                tab={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                    <BarChart3 size={14} />
                    <span>{t('testReport')}</span>
                  </span>
                }
              />
            </Tabs>

            {drawerTab === 'logs' && (
              <>
                {/* Terminal Console Logs */}
                <div className={styles.terminalSection}>
                  <div className={styles.terminalHeader}>
                    <div className={styles.terminalTitleGroup}>
                      <Terminal size={14} className={styles.terminalHeaderIcon} />
                      <h4>{t('consoleLogs')}</h4>
                    </div>
                      
                    <div className={styles.terminalHeaderControls}>
                      {/* Search Input Box */}
                      <div className={styles.terminalSearchWrapper}>
                        <Search size={12} className={styles.terminalSearchIcon} />
                        <input 
                          type="text" 
                          className={styles.terminalSearchInput}
                          placeholder={lang === 'zh' ? "搜索日志..." : "Search logs..."}
                          value={logSearchQuery}
                          onChange={(e) => setLogSearchQuery(e.target.value)}
                        />
                        {logSearchQuery && (
                          <button
                            className={styles.terminalSearchClear}
                            onClick={() => setLogSearchQuery('')}
                            title={lang === 'zh' ? "清除搜索" : "Clear search"}
                            aria-label={lang === 'zh' ? "清除搜索" : "Clear search"}
                          >
                            <X size={10} />
                          </button>
                        )}
                      </div>

                      {/* Log Level Capsule Filters */}
                      <div className={styles.logLevelCapsules}>
                        {(['ALL', 'ERROR', 'WARNING', 'SUCCESS'] as const).map(level => {
                          let levelLabel: string = level;
                          if (lang === 'zh') {
                            levelLabel = level === 'ALL' ? '全部' : level === 'ERROR' ? '异常' : level === 'WARNING' ? '警告' : '成功';
                          } else {
                            levelLabel = level === 'ALL' ? 'ALL' : level === 'ERROR' ? 'ERR' : level === 'WARNING' ? 'WARN' : 'OK';
                          }
                          return (
                            <button
                              key={level}
                              className={`${styles.capsuleBtn} ${styles[`capsule_${level}`]} ${logLevelFilter === level ? styles.capsuleActive : ''}`}
                              onClick={() => setLogLevelFilter(level)}
                            >
                              {levelLabel}
                            </button>
                          );
                        })}
                      </div>

                      {/* Font Sizer Controls */}
                      <div className={styles.fontSizeControls}>
                        <button 
                          className={styles.fontSizeBtn}
                          onClick={() => setTerminalFontSize(prev => Math.max(10, prev - 1))}
                          title={lang === 'zh' ? "减小字号" : "Decrease Font Size"}
                          aria-label={lang === 'zh' ? "减小字号" : "Decrease Font Size"}
                        >
                          <ZoomOut size={12} />
                        </button>
                        <span className={styles.fontSizeValue}>{terminalFontSize}px</span>
                        <button
                          className={styles.fontSizeBtn}
                          onClick={() => setTerminalFontSize(prev => Math.min(20, prev + 1))}
                          title={lang === 'zh' ? "增大字号" : "Increase Font Size"}
                          aria-label={lang === 'zh' ? "增大字号" : "Increase Font Size"}
                        >
                          <ZoomIn size={12} />
                        </button>
                      </div>

                      {/* Height toggle button */}
                      <button
                        className={styles.terminalHeightBtn}
                        onClick={() => setIsTerminalHeightExpanded(!isTerminalHeightExpanded)}
                        title={isTerminalHeightExpanded ? (lang === 'zh' ? "折叠控制台高度" : "Minimize height") : (lang === 'zh' ? "展开控制台高度" : "Maximize height")}
                        aria-label={isTerminalHeightExpanded ? (lang === 'zh' ? "折叠控制台高度" : "Minimize height") : (lang === 'zh' ? "展开控制台高度" : "Maximize height")}
                      >
                        {isTerminalHeightExpanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
                      </button>

                      {/* Fullscreen toggle button */}
                      <button 
                        className={styles.terminalCopyButton}
                        onClick={() => setIsTerminalFullscreen(true)}
                        title={lang === 'zh' ? "全屏终端" : "Fullscreen Terminal"}
                      >
                        <Maximize2 size={12} />
                        <span>{lang === 'zh' ? "全屏终端" : "Fullscreen"}</span>
                      </button>

                      {/* Live Streaming indicator */}
                      {(selectedRun.status === 'running' || selectedRun.status === 'queued') && (
                        <span className={isStreaming ? styles.livePulse : styles.streamingIndicator}>
                          {!isStreaming && <span className={styles.streamingDot}></span>}
                          {isStreaming ? (lang === 'zh' ? '实时' : 'LIVE') : t('streaming')}
                        </span>
                      )}

                      {/* Copy Button */}
                      <button 
                        className={styles.terminalCopyButton}
                        onClick={() => {
                          const logsText = isStreaming 
                            ? streamedStdout 
                            : (selectedRun.stdout || '') + '\n' + (selectedRun.stderr || '');
                          copyToClipboard(logsText);
                        }}
                      >
                        {copySuccess ? <Check size={12} style={{ color: '#10b981' }} /> : <Copy size={12} />}
                        <span>{copySuccess ? t('copied') : t('copy')}</span>
                      </button>

                      {/* Download Button */}
                      <button 
                        className={styles.terminalCopyButton}
                        onClick={() => downloadLogs(selectedRun.id)}
                        title={lang === 'zh' ? '下载完整日志' : 'Download raw log file'}
                      >
                        <Download size={12} />
                        <span>{t('download')}</span>
                      </button>
                    </div>
                  </div>
                  <div 
                    className={`${styles.terminalBlock} ${isTerminalHeightExpanded ? styles.terminalBlockExpanded : ''}`} 
                    ref={terminalRef}
                    style={{ fontSize: `${terminalFontSize}px` }}
                  >
                    {isStreaming ? (
                      filteredStreamed ? (
                        <pre className={styles.stdoutPre}>{renderFormattedLogs(filteredStreamed)}</pre>
                      ) : streamedStdout ? (
                        <span className={styles.terminalPlaceholder}>
                          {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                        </span>
                      ) : (
                        <span className={styles.terminalPlaceholder}>
                          <span className={styles.waitingLogs}>
                            <span className={styles.pulsingText}>{t('waitingLogs')}</span>
                          </span>
                        </span>
                      )
                    ) : (selectedRun.stdout || selectedRun.stderr || streamedStdout) ? (
                      (filteredStdout || filteredStderr || filteredStreamed) ? (
                        <>
                          {(filteredStdout || (selectedRunDetails ? null : filteredStreamed)) && (
                            <pre className={styles.stdoutPre}>
                              {renderFormattedLogs(filteredStdout || filteredStreamed)}
                            </pre>
                          )}
                          {filteredStderr && <pre className={styles.stderrPre}>{renderFormattedLogs(filteredStderr)}</pre>}
                        </>
                      ) : (
                        <span className={styles.terminalPlaceholder}>
                          {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                        </span>
                      )
                    ) : detailsLoading ? (
                      <span className={styles.terminalPlaceholder}>
                        <span className={styles.waitingLogs}>
                          <span className={styles.pulsingText}>{lang === 'zh' ? '正在加载控制台日志...' : 'Loading console logs...'}</span>
                        </span>
                      </span>
                    ) : (
                      <span className={styles.terminalPlaceholder}>
                        {t('noLogsAvailable')}
                      </span>
                    )}
                  </div>
                </div>

                {/* Arguments box */}
                {selectedRun.args && selectedRun.args.length > 0 && (
                  <div className={styles.argsBlock}>
                    <h4>{t('pytestArguments')}</h4>
                    <div className={styles.argsWrapper}>
                      {selectedRun.args.map((arg: string, idx: number) => (
                        <code key={idx} className={styles.argBadge}>{arg}</code>
                      ))}
                    </div>
                  </div>
                )}

                {/* Error logs container */}
                {selectedRun.error && (
                  <div className={styles.errorLogSection}>
                    <div className={styles.errorLogHeader}>
                      <h4>{t('executionStacktrace')}</h4>
                      <button 
                        className={styles.copyButton}
                        onClick={() => copyToClipboard(selectedRun.error || '')}
                      >
                        {copySuccess ? <Check size={14} style={{ color: '#10b981' }} /> : <Copy size={14} />}
                        <span>{copySuccess ? t('copied') : t('copy')}</span>
                      </button>
                    </div>
                    <pre className={styles.errorText}>
                      <code>{selectedRun.error}</code>
                    </pre>
                  </div>
                )}
              </>
            )}

            {drawerTab === 'report' && (
              <>
                {/* Dynamic summary chart block */}
                {selectedRun.summary ? (
                  <div className={styles.compactSummaryBar}>
                    <div className={styles.compactSummaryMetrics}>
                      <span className={`${styles.compactMetric} ${styles.compactMetricPassed}`}>
                        <CheckCircle2 size={12} />
                        <span>{t('passed')}:</span>
                        <strong>{selectedRun.summary.passed}</strong>
                      </span>
                      <span className={`${styles.compactMetric} ${styles.compactMetricFailed}`}>
                        <XCircle size={12} />
                        <span>{t('failed')}:</span>
                        <strong>{selectedRun.summary.failed}</strong>
                      </span>
                      <span className={`${styles.compactMetric} ${styles.compactMetricError}`}>
                        <AlertTriangle size={12} />
                        <span>{t('error')}:</span>
                        <strong>{selectedRun.summary.error}</strong>
                      </span>
                      <span className={`${styles.compactMetric} ${styles.compactMetricSkipped}`}>
                        <Clock size={12} />
                        <span>{t('skipped')}:</span>
                        <strong>{selectedRun.summary.skipped}</strong>
                      </span>
                      <span className={styles.compactMetricDuration}>
                        <Clock size={12} />
                        <span>{t('durationLabel')}: {formatDuration(selectedRun.summary.duration_ms)}</span>
                      </span>
                    </div>

                    {/* Multi-segmented single bar chart */}
                    <div className={styles.compactSegmentBar}>
                      {selectedRun.summary.passed > 0 && (
                        <div 
                          className={styles.segmentPassed} 
                          style={{ width: `${(selectedRun.summary.passed / selectedRun.summary.total) * 100}%` }}
                          title={`${t('passed')}: ${selectedRun.summary.passed}`}
                        ></div>
                      )}
                      {selectedRun.summary.failed > 0 && (
                        <div 
                          className={styles.segmentFailed} 
                          style={{ width: `${(selectedRun.summary.failed / selectedRun.summary.total) * 100}%` }}
                          title={`${t('failed')}: ${selectedRun.summary.failed}`}
                        ></div>
                      )}
                      {selectedRun.summary.error > 0 && (
                        <div 
                          className={styles.segmentError} 
                          style={{ width: `${(selectedRun.summary.error / selectedRun.summary.total) * 100}%` }}
                          title={`${t('error')}: ${selectedRun.summary.error}`}
                        ></div>
                      )}
                      {selectedRun.summary.skipped > 0 && (
                        <div 
                          className={styles.segmentSkipped} 
                          style={{ width: `${(selectedRun.summary.skipped / selectedRun.summary.total) * 100}%` }}
                          title={`${t('skipped')}: ${selectedRun.summary.skipped}`}
                        ></div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className={styles.summaryPlaceholder}>
                    <Activity size={24} className={styles.pulseIcon} style={{ color: '#06b6d4', marginBottom: '0.75rem' }} />
                    <p>{t('execInProgress')}</p>
                    <span>{t('execInProgressDesc')}</span>
                  </div>
                )}

                {/* Premium Allure Report Portal Actions */}
                {selectedRun.report?.html_generated && selectedRun.report?.allure_report_file ? (
                  <div className={styles.reportPortalCardCompact}>
                    <div className={styles.portalActions} style={{ marginTop: 0 }}>
                      <button
                        type="button"
                        className={styles.portalBtnPrimary}
                        onClick={() => setIsReportFullscreen(true)}
                        title={lang === 'zh' ? '全屏查看测试报告' : 'View Test Report in Fullscreen'}
                      >
                        <Maximize2 size={14} />
                        <span>{lang === 'zh' ? '全屏查看报告' : 'Fullscreen Report'}</span>
                      </button>

                      <a 
                        href={`/runs/${selectedRun.id}/report`}
                        target="_blank"
                        rel="noreferrer"
                        className={styles.portalBtnSecondary}
                        title={lang === 'zh' ? '在新窗口中打开' : 'Open in New Window'}
                      >
                        <ExternalLink size={14} />
                        <span>{lang === 'zh' ? '在新窗口打开' : 'Open in New Window'}</span>
                      </a>
                    </div>
                  </div>
                ) : (
                  <div className={styles.reportPlaceholder}>
                    {selectedRun.status === 'running' || selectedRun.status === 'queued' ? (
                      <>
                        <RotateCw size={24} className={styles.spinIcon} style={{ color: '#a855f7', marginBottom: '0.75rem' }} />
                        <p>{t('generatingAllureReport')}</p>
                        <span>{t('generatingAllureReportDesc')}</span>
                      </>
                    ) : (
                      <>
                        <AlertTriangle size={24} style={{ color: '#f59e0b', marginBottom: '0.75rem' }} />
                        <p>{t('noReportGenerated')}</p>
                        <span>{t('noReportGeneratedDesc')}</span>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}
    </SideSheet>
  )
}
