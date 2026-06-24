import { Calendar, Clock } from 'lucide-react'
import { Modal, Banner, Input, Select, Checkbox, Row, Col, Button } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
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
  return (
    <Modal
      visible={true}
      onCancel={onClose}
      footer={null}
      width={600}
      bodyStyle={{ padding: '20px' }}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Clock size={20} style={{ color: 'var(--semi-color-primary)' }} />
          <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>
            {lang === 'zh' ? '配置定时运行计划' : 'Configure Scheduled Execution'}
          </h3>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <Banner
          type="info"
          description={
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', fontSize: '13px' }}>
              <strong style={{ fontWeight: 600 }}>{lang === 'zh' ? '执行方案: ' : 'Profile: '}</strong>
              <span>{profile.name}</span>
              <span style={{ opacity: 0.8 }}>({profile.tests_path})</span>
            </div>
          }
          style={{ borderRadius: '8px' }}
          closeIcon={null}
        />

        {previewError && (
          <Banner
            type="danger"
            description={previewError}
            style={{ borderRadius: '8px' }}
            closeIcon={null}
          />
        )}

        {/* Schedule Name */}
        <div className={styles.formField}>
          <label className={styles.label} htmlFor="sched-name">
            <span>{lang === 'zh' ? '计划名称' : 'Schedule Name'}</span>
            <span className={styles.requiredIndicator}>*</span>
          </label>
          <Input
            id="sched-name"
            value={schedName}
            onChange={(val) => setSchedName(val)}
            placeholder={lang === 'zh' ? '输入定时计划名称' : 'e.g. Daily Regression'}
            required
          />
        </div>

        {/* Expression and Timezone */}
        <Row gutter={16}>
          <Col span={16}>
            <label className={styles.label} htmlFor="sched-expression">
              <span>{lang === 'zh' ? 'Cron 表达式' : 'Cron Expression'}</span>
              <span className={styles.requiredIndicator}>*</span>
            </label>
            <Input
              id="sched-expression"
              value={schedExpression}
              onChange={(val) => setSchedExpression(val)}
              placeholder="e.g. 0 2 * * *"
              required
            />
            <span className={styles.fieldHelp} style={{ fontSize: '11px', color: 'var(--semi-color-text-2)', display: 'block', marginTop: '4px' }}>
              {lang === 'zh' ? '标准 5 位 Cron 语法 (分 时 日 月 周)' : 'Standard 5-field cron syntax (min hour day month day-of-week).'}
            </span>
          </Col>

          <Col span={8}>
            <label className={styles.label} htmlFor="sched-timezone">
              <span>{lang === 'zh' ? '时区' : 'Timezone'}</span>
            </label>
            <Select
              id="sched-timezone"
              value={schedTimezone}
              onChange={(val) => setSchedTimezone(val as string)}
              style={{ width: '100%' }}
            >
              <Select.Option value="UTC">UTC</Select.Option>
              <Select.Option value="Asia/Shanghai">Asia/Shanghai</Select.Option>
              <Select.Option value="America/New_York">America/New_York</Select.Option>
              <Select.Option value="Europe/London">Europe/London</Select.Option>
            </Select>
          </Col>
        </Row>

        {/* Enabled Checkbox */}
        <div>
          <Checkbox
            id="sched-enabled"
            checked={schedEnabled}
            onChange={(e) => setSchedEnabled(!!e.target.checked)}
          >
            {lang === 'zh' ? '启用此定时调度' : 'Enable this schedule'}
          </Checkbox>
        </div>

        {/* Real-time future runs preview widget */}
        <div style={{
          border: '1px solid var(--semi-color-border)',
          borderRadius: '8px',
          padding: '16px',
          backgroundColor: 'var(--semi-color-fill-0)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', fontSize: '13px', fontWeight: 600, color: 'var(--semi-color-text-1)' }}>
            <Calendar size={14} style={{ color: 'var(--semi-color-primary)' }} />
            <span>{lang === 'zh' ? '未来 5 次运行时间预测:' : 'Next 5 Projected Runs Preview (Static Check):'}</span>
          </div>
          {previewNextRuns.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {previewNextRuns.map((runTime, idx) => (
                <div key={runTime} style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '13px', color: 'var(--semi-color-text-2)' }}>
                  <span style={{ display: 'inline-flex', justifyContent: 'center', alignItems: 'center', width: '20px', height: '20px', borderRadius: '50%', backgroundColor: 'var(--semi-color-fill-1)', fontSize: '11px', fontWeight: 600, color: 'var(--semi-color-text-3)' }}>
                    {idx + 1}
                  </span>
                  <span style={{ fontFamily: 'monospace', fontWeight: 500, color: 'var(--semi-color-text-0)' }}>
                    {new Date(runTime).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US', { timeZone: schedTimezone })}
                  </span>
                  <span style={{ fontSize: '11px', opacity: 0.8 }}>({schedTimezone})</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '12px 0', fontSize: '13px', color: 'var(--semi-color-text-3)' }}>
              {previewError ? (
                <span style={{ color: 'var(--semi-color-danger)' }}>{previewError}</span>
              ) : (
                <span>{lang === 'zh' ? '请输入有效的 Cron 表达式' : 'Please enter a valid Cron expression'}</span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Modal Actions */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '16px' }}>
          <div>
            {schedules.some(s => s.profile_id === profile.id) && (
              <Button
                type="danger"
                theme="light"
                onClick={() => {
                  const sched = schedules.find(s => s.profile_id === profile.id)
                  if (sched) onDelete(sched.id)
                }}
              >
                {lang === 'zh' ? '注销调度' : 'Delete Schedule'}
              </Button>
            )}
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <Button
              theme="light"
              onClick={onClose}
            >
              {t('cancel')}
            </Button>
            <Button
              type="primary"
              theme="solid"
              onClick={onSave}
            >
              {lang === 'zh' ? '保存配置' : 'Save Config'}
            </Button>
          </div>
      </div>
    </Modal>
  )
}
