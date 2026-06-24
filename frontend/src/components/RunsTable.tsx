import React from 'react'
import { Table, Tag, Progress, Button, Radio, RadioGroup, Tooltip, Spin } from '@douyinfe/semi-ui'
import {
  IconLock,
  IconUnlock,
  IconFolder,
  IconRefresh,
  IconChevronRight,
  IconActivity,
  IconUser,
  IconClock,
  IconBox,
  IconServer,
  IconBolt,
  IconPlay
} from '@douyinfe/semi-icons'
import styles from '../App.module.css'
import { activateOnKey } from '../a11y'
import { formatDuration } from '../logUtils'
import { passRateColor, statusTagColor } from './runStatus'
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
  handleToggleLock: (runId: string, e: React.MouseEvent) => void
  formatDate: (isoStr: string | null) => string
}

/**
 * Right column: execution-records table leveraging Semi UI Table component.
 * Includes segmented All/Manual/Scheduled filter, refresh button, status colors,
 * progress bars, and Lock controls.
 */
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

  const columns = [
    {
      title: t('runId'),
      dataIndex: 'id',
      key: 'id',
      render: (text: string) => <code style={{ fontFamily: 'var(--font-mono)' }}>{text.slice(0, 8)}</code>
    },
    {
      title: <IconLock style={{ fontSize: '14px' }} />,
      dataIndex: 'locked',
      key: 'locked',
      align: 'center' as const,
      render: (locked: boolean, record: Run) => (
        <Tooltip
          content={locked
            ? (lang === 'zh' ? '已锁定 (保护文件不被清理)' : 'Locked (Protected from physical cleanup)')
            : (lang === 'zh' ? '未锁定 (可进行物理清理)' : 'Unlocked (Eligible for physical cleanup)')
          }
        >
          <Button
            type="tertiary"
            icon={locked ? <IconLock style={{ color: 'var(--semi-color-warning)' }} /> : <IconUnlock style={{ color: 'var(--semi-color-text-3)' }} />}
            size="small"
            onClick={(e) => {
              e.stopPropagation()
              handleToggleLock(record.id, e)
            }}
          />
        </Tooltip>
      )
    },
    {
      title: t('targetSuite'),
      dataIndex: 'tests_path',
      key: 'tests_path',
      render: (text: string) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
          <IconFolder style={{ color: 'var(--semi-color-text-2)' }} />
          <span style={{ fontWeight: 500 }}>{text}</span>
        </span>
      )
    },
    {
      title: t('status'),
      dataIndex: 'status',
      key: 'status',
      render: (status: Run['status'], record: Run) => {
        const color: any = statusTagColor(status, record.passed)
        let icon: React.ReactNode = null
        if (status === 'queued') {
          icon = <IconRefresh spin />
        } else if (status === 'running') {
          icon = <IconActivity spin />
        }

        return (
          <Tag color={color} size="large" style={{ textTransform: 'capitalize', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            {icon}
            {t(`status_${status}`)}
          </Tag>
        )
      }
    },
    {
      title: t('engine'),
      dataIndex: 'executor_mode',
      key: 'executor_mode',
      render: (mode: Run['executor_mode']) => (
        <Tag color="violet" size="large">
          {mode === 'docker' ? <IconBox style={{ marginRight: '4px' }} /> : <IconServer style={{ marginRight: '4px' }} />}
          {t(`engine_${mode}`)}
        </Tag>
      )
    },
    {
      title: t('owner'),
      dataIndex: 'created_by',
      key: 'created_by',
      render: (text: string) => {
        let color: any = 'default'
        if (text === 'system' || text.startsWith('system:')) color = 'teal'
        else if (text === 'admin') color = 'red'
        else color = 'indigo'
        return <Tag color={color} size="large">{text}</Tag>
      }
    },
    {
      title: t('results'),
      dataIndex: 'summary',
      key: 'summary',
      render: (summary: Run['summary']) => {
        if (!summary) return <span style={{ color: 'var(--semi-color-text-3)' }}>-</span>
        return (
          <span style={{ fontWeight: 600 }}>
            <span style={{ color: 'var(--semi-color-success)' }}>{summary.passed}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>/</span>
            <span style={{ color: 'var(--semi-color-danger)' }}>{summary.failed + summary.error}</span>
            <span style={{ color: 'var(--semi-color-text-3)', margin: '0 4px' }}>/</span>
            <span>{summary.total}</span>
          </span>
        )
      }
    },
    {
      title: t('passRate'),
      dataIndex: 'summary',
      key: 'passRate',
      render: (summary: Run['summary']) => {
        if (!summary) return <span style={{ color: 'var(--semi-color-text-3)' }}>-</span>
        const rate = Math.round(summary.pass_rate * 100)
        const strokeColor = passRateColor(rate)
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: '100px' }}>
            <Progress percent={rate} stroke={strokeColor} style={{ width: '60px' }} size="small" />
            <span style={{ fontSize: '12px', minWidth: '32px', fontWeight: 600 }}>{rate}%</span>
          </div>
        )
      }
    },
    {
      title: t('duration'),
      dataIndex: 'summary',
      key: 'duration',
      render: (summary: Run['summary']) => (
        <span style={{ fontFamily: 'var(--font-mono)' }}>
          {formatDuration(summary?.duration_ms)}
        </span>
      )
    },
    {
      title: t('createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (text: string) => (
        <span style={{ color: 'var(--semi-color-text-2)' }}>
          {formatDate(text)}
        </span>
      )
    },
    {
      title: '',
      key: 'arrow',
      render: () => <IconChevronRight style={{ color: 'var(--semi-color-text-3)' }} />
    }
  ]

  const onRow = (record: any) => {
    return {
      onClick: () => {
        if (record) setSelectedRunId(record.id)
      },
      onKeyDown: activateOnKey(() => {
        if (record) setSelectedRunId(record.id)
      }),
      className: record?.id === selectedRunId ? styles.rowSelected : ''
    }
  }

  return (
    <div className={styles.tableCard}>
      <div className={styles.tableHeader} style={{ flexWrap: 'wrap', gap: '1rem' }}>
        <div className={styles.tableTitleGroup}>
          <IconActivity size="large" style={{ color: 'var(--semi-color-text-2)' }} />
          <h2 data-testid="execution-records-title" style={{ margin: 0 }}>{t('executionRecords')}</h2>
        </div>

        {/* Segmented Filter Radio Group */}
        <RadioGroup
          type="button"
          buttonSize="middle"
          value={logFilterTab}
          onChange={e => setLogFilterTab(e.target.value as any)}
          style={{ marginRight: 'auto' }}
        >
          <Radio value="All">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <IconActivity />
              <span>{lang === 'zh' ? '全部记录' : 'All Runs'}</span>
            </span>
          </Radio>
          <Radio value="Manual">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <IconUser />
              <span>{lang === 'zh' ? '手动触发' : 'Manually Triggered'}</span>
            </span>
          </Radio>
          <Radio value="Scheduled">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <IconClock />
              <span>{lang === 'zh' ? '定时触发' : 'Scheduled Runs'}</span>
            </span>
          </Radio>
        </RadioGroup>

        <Button
          icon={<IconRefresh />}
          onClick={fetchRuns}
          aria-label={t('refreshLogs')}
          title={t('refreshLogs')}
        />
      </div>

      {loading && runs.length === 0 ? (
        <div className={styles.loadingState}>
          <Spin size="large" />
          <p style={{ marginTop: '1rem' }}>{t('loadingHistory')}</p>
        </div>
      ) : runs.length === 0 ? (
        <div className={styles.emptyState}>
          <IconBolt style={{ color: 'var(--semi-color-warning)', fontSize: '48px' }} />
          <h3>{t('noRunsTitle')}</h3>
          <p>{t('noRunsDesc')}</p>
          <Button
            theme="solid"
            type="primary"
            icon={<IconPlay />}
            onClick={() => setIsTriggerModalOpen(true)}
            style={{ marginTop: '1.5rem' }}
          >
            {t('launchFirstRun')}
          </Button>
        </div>
      ) : (
        <div className={styles.tableWrapper}>
          <Table
            columns={columns}
            dataSource={filteredRuns}
            rowKey="id"
            onRow={onRow}
            pagination={filteredRuns.length > 10 ? { pageSize: 10 } : false}
            size="middle"
            style={{ width: '100%' }}
          />
        </div>
      )}
    </div>
  )
}
