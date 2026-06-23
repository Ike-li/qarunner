import { AlertTriangle, Calendar, Clock, X } from 'lucide-react'
import styles from '../App.module.css'
import { useDialogA11y } from '../hooks/useDialogA11y'
import type { Lang, TranslationKey } from '../i18n'
import type { Profile, Schedule } from '../types'

interface ScheduleModalProps {
  t: (key: TranslationKey) => string
  lang: Lang
  profile: Profile
  schedName: string
  setSchedName: (value: string) => void
  schedExpression: string
  setSchedExpression: (value: string) => void
  schedTimezone: string
  setSchedTimezone: (value: string) => void
  schedEnabled: boolean
  setSchedEnabled: (value: boolean) => void
  previewError: string | null
  previewNextRuns: string[]
  schedules: Schedule[]
  onClose: () => void
  onSave: () => void
  onDelete: (scheduleId: string) => void
}

/** Cron schedule editor for a saved profile, with a live next-runs preview.
 *  The preview is computed by an effect in App; this component only displays. */
export function ScheduleModal({
  t,
  lang,
  profile,
  schedName,
  setSchedName,
  schedExpression,
  setSchedExpression,
  schedTimezone,
  setSchedTimezone,
  schedEnabled,
  setSchedEnabled,
  previewError,
  previewNextRuns,
  schedules,
  onClose,
  onSave,
  onDelete,
}: ScheduleModalProps) {
  const dialogRef = useDialogA11y({ isOpen: true, onClose })
  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div
        ref={dialogRef}
        className={`${styles.modal} ${styles.scheduleModal}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="schedule-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <div className={styles.modalTitleGroup}>
            <Clock size={20} className={styles.iconAccent} />
            <h2 id="schedule-modal-title">{lang === 'zh' ? '配置定时运行计划' : 'Configure Scheduled Execution'}</h2>
          </div>
          <button className={styles.modalCloseButton} onClick={onClose} aria-label={lang === 'zh' ? '关闭' : 'Close'}>
            <X size={20} />
          </button>
        </div>

        <div className={styles.form}>
          <div className={styles.scheduleProfileBanner}>
            <span className={styles.bannerLabel}>{lang === 'zh' ? '执行方案: ' : 'Profile: '}</span>
            <span className={styles.bannerValue}>{profile.name}</span>
            <span className={styles.bannerSuite}>({profile.tests_path})</span>
          </div>

          {previewError && (
            <div className={styles.formErrorAlert} role="alert">
              <AlertTriangle size={16} aria-hidden="true" />
              <span>{previewError}</span>
            </div>
          )}

          <div className={styles.formField}>
            <label className={styles.label} htmlFor="sched-name">
              <span>{lang === 'zh' ? '计划名称' : 'Schedule Name'}</span>
              <span className={styles.requiredIndicator}>*</span>
            </label>
            <input
              id="sched-name"
              type="text"
              className={styles.input}
              value={schedName}
              onChange={(e) => setSchedName(e.target.value)}
              placeholder={lang === 'zh' ? '输入定时计划名称' : 'e.g. Daily Regression'}
              required
            />
          </div>

          <div className={styles.formRow}>
            <div className={styles.formField} style={{ flex: 1 }}>
              <label className={styles.label} htmlFor="sched-expression">
                <span>{lang === 'zh' ? 'Cron 表达式' : 'Cron Expression'}</span>
                <span className={styles.requiredIndicator}>*</span>
              </label>
              <input
                id="sched-expression"
                type="text"
                className={styles.input}
                value={schedExpression}
                onChange={(e) => setSchedExpression(e.target.value)}
                placeholder="e.g. 0 2 * * *"
                required
              />
              <span className={styles.fieldHelp}>
                {lang === 'zh' ? '标准 5 位 Cron 语法 (分 时 日 月 周)' : 'Standard 5-field cron syntax (min hour day month day-of-week).'}
              </span>
            </div>

            <div className={styles.formField} style={{ width: '150px' }}>
              <label className={styles.label} htmlFor="sched-timezone">
                <span>{lang === 'zh' ? '时区' : 'Timezone'}</span>
              </label>
              <div className={styles.selectWrapper}>
                <select
                  id="sched-timezone"
                  className={styles.select}
                  value={schedTimezone}
                  onChange={(e) => setSchedTimezone(e.target.value)}
                >
                  <option value="UTC">UTC</option>
                  <option value="Asia/Shanghai">Asia/Shanghai</option>
                  <option value="America/New_York">America/New_York</option>
                  <option value="Europe/London">Europe/London</option>
                </select>
              </div>
            </div>
          </div>

          <div className={styles.formField}>
            <label className={styles.checkboxLabel} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
              <input
                type="checkbox"
                className={styles.checkbox}
                checked={schedEnabled}
                onChange={(e) => setSchedEnabled(e.target.checked)}
              />
              <span>{lang === 'zh' ? '启用此定时调度' : 'Enable this schedule'}</span>
            </label>
          </div>

          {/* Real-time future runs preview widget */}
          <div className={styles.previewWidget}>
            <div className={styles.previewWidgetHeader}>
              <Calendar size={14} className={styles.previewIcon} />
              <span>{lang === 'zh' ? '未来 5 次运行时间预测:' : 'Next 5 Projected Runs Preview (Static Check):'}</span>
            </div>
            {previewNextRuns.length > 0 ? (
              <div className={styles.previewList}>
                {previewNextRuns.map((runTime, idx) => (
                  <div key={runTime} className={styles.previewItem}>
                    <span className={styles.previewIdx}>#{idx + 1}</span>
                    <span className={styles.previewTime}>{new Date(runTime).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US', { timeZone: schedTimezone })}</span>
                    <span className={styles.previewTz}>({schedTimezone})</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className={styles.previewEmpty}>
                {previewError ? (
                  <span className={styles.previewErrorText}>{previewError}</span>
                ) : (
                  <span>{lang === 'zh' ? '请输入有效的 Cron 表达式' : 'Please enter a valid Cron expression'}</span>
                )}
              </div>
            )}
          </div>

          <div className={styles.modalActions} style={{ display: 'flex', justifyContent: 'space-between', marginTop: '2rem' }}>
            <div>
              {schedules.some(s => s.profile_id === profile.id) && (
                <button
                  type="button"
                  className={`${styles.button} ${styles.buttonDanger}`}
                  onClick={() => {
                    const sched = schedules.find(s => s.profile_id === profile.id)
                    if (sched) onDelete(sched.id)
                  }}
                >
                  {lang === 'zh' ? '注销调度' : 'Delete Schedule'}
                </button>
              )}
            </div>
            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <button
                type="button"
                className={`${styles.button} ${styles.buttonSecondary}`}
                onClick={onClose}
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                className={`${styles.button} ${styles.buttonPrimary}`}
                onClick={onSave}
              >
                {lang === 'zh' ? '保存配置' : 'Save Config'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
