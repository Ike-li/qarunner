import { Calendar, Clock, Play } from 'lucide-react'
import { Modal, Banner, Input, Select, Checkbox, Row, Col, Button } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'
import { useDialogA11y } from '../hooks/useDialogA11y'

/** Cron schedule editor — reads everything from DashboardContext. */
export function ScheduleModal() {
  const d = useDashboard()
  const s = d.schedules

  if (!s.isScheduleModalOpen || !s.scheduleProfile) return null

  const onClose = () => s.setIsScheduleModalOpen(false)
  const dialogRef = useDialogA11y({ isOpen: s.isScheduleModalOpen, onClose })

  // The schedule (if any) already saved for this profile — computed once and
  // reused by the Delete / Run Now render guards and their handlers.
  const existing = s.schedules.find(
    (sc) => sc.profile_id === s.scheduleProfile?.id,
  )

  return (
    <Modal
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Calendar size={18} style={{ color: 'var(--semi-color-primary)' }} />
          {d.lang === 'zh' ? '定时调度' : 'Schedule'}
          <span style={{ fontSize: '0.8rem', color: 'var(--semi-color-text-2)', fontWeight: 400 }}>
            — {s.scheduleProfile.name}
          </span>
        </div>
      }
      visible={true}
      onCancel={() => s.setIsScheduleModalOpen(false)}
      footer={null}
      width={480}
      data-testid="schedule-modal"
    >
      <div ref={dialogRef} role="dialog" aria-modal="true" tabIndex={-1}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', padding: '0.5rem 0' }}>
        <Row gutter={16}>
          <Col span={18}>
            <Input
              placeholder={d.lang === 'zh' ? '调度名称' : 'Schedule name'}
              value={s.schedName}
              onChange={s.setSchedName}
              data-testid="schedule-name-input"
              aria-label={d.lang === 'zh' ? '调度名称' : 'Schedule name'}
            />
          </Col>
          <Col span={6}>
            <Checkbox checked={s.schedEnabled} onChange={(e) => s.setSchedEnabled((e.target as HTMLInputElement).checked)}>
              {d.lang === 'zh' ? '启用' : 'Enabled'}
            </Checkbox>
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={16}>
            <Input
              placeholder="Cron expression (e.g. 0 2 * * *)"
              value={s.schedExpression}
              onChange={s.setSchedExpression}
              prefix={<Clock size={14} />}
              data-testid="schedule-cron-input"
              aria-label={d.lang === 'zh' ? 'Cron 表达式' : 'Cron expression'}
            />
          </Col>
          <Col span={8}>
            <Select
              value={s.schedTimezone}
              onChange={(v) => s.setSchedTimezone(v as string)}
              style={{ width: '100%' }}
              data-testid="schedule-timezone-select"
              aria-label={d.lang === 'zh' ? '时区' : 'Timezone'}
            >
              <Select.Option value="UTC">UTC</Select.Option>
              <Select.Option value="Asia/Shanghai">Asia/Shanghai</Select.Option>
              <Select.Option value="Asia/Tokyo">Asia/Tokyo</Select.Option>
              <Select.Option value="America/New_York">America/New_York</Select.Option>
              <Select.Option value="Europe/London">Europe/London</Select.Option>
            </Select>
          </Col>
        </Row>

        {s.previewError && (
          <Banner type="danger" description={s.previewError} onClose={() => {}} data-testid="schedule-preview-error" />
        )}

        {s.previewNextRuns.length > 0 && (
          <div className={styles.schedulePreview} data-testid="schedule-preview">
            <span className={styles.schedulePreviewTitle}>
              {d.lang === 'zh' ? '接下来 5 次触发时间' : 'Next 5 fire times'}
            </span>
            <ul className={styles.schedulePreviewList}>
              {s.previewNextRuns.map((iso, i) => (
                <li key={i}>
                  <Clock size={12} />
                  {new Date(iso).toLocaleString()}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
          {existing && (
            <Button
              type="danger"
              theme="light"
              onClick={() => s.handleDeleteSchedule(existing.id)}
              data-testid="schedule-delete-button"
            >
              {d.lang === 'zh' ? '删除调度' : 'Delete'}
            </Button>
          )}
          {existing && (
            <Button
              theme="light"
              icon={<Play size={14} />}
              onClick={async () => {
                const ok = await s.handleTriggerSchedule(existing.id)
                if (ok) {
                  d.runs.fetchRuns()
                  s.setIsScheduleModalOpen(false)
                  alert(d.lang === 'zh' ? '已触发一次运行' : 'Run triggered')
                }
              }}
              data-testid="schedule-trigger-button"
            >
              {d.lang === 'zh' ? '立即触发' : 'Run Now'}
            </Button>
          )}
          <Button type="primary" theme="solid" onClick={s.handleSaveSchedule} data-testid="schedule-save-button">
            {d.lang === 'zh' ? '保存调度' : 'Save'}
          </Button>
        </div>
      </div>
      </div>
    </Modal>
  )
}
