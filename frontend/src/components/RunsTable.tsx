import React, { useMemo } from 'react'
import { Table, Tag, Progress, Button, Radio, RadioGroup, Tooltip, Spin, Input, Select } from '@douyinfe/semi-ui'
import {
  IconLock,
  IconUnlock,
  IconFolder,
  IconRefresh,
  IconChevronRight,
  IconActivity,
  IconUser,
  IconClock,
  IconBolt,
  IconPlay,
  IconSearch
} from '@douyinfe/semi-icons'
import styles from '../App.module.css'
import { activateOnKey } from '../a11y'
import { formatDuration } from '../logUtils'
import { passRateColor, statusTagColor } from './runStatus'
import type { Run } from '../types'
import { useDashboard } from '../hooks/DashboardContext'

/**
 * Right column: execution-records table leveraging Semi UI Table component.
 * Includes segmented All/Manual/Scheduled filter, refresh button, status colors,
 * progress bars, and Lock controls.
 */
export function RunsTable() {
  const d = useDashboard()

  const filteredRuns = useMemo(
    () =>
      d.runs.runs.filter((r) => {
        if (d.selectedSuiteFilter && r.tests_path !== d.selectedSuiteFilter) return false
        if (d.logFilterTab === 'Manual' && r.created_by === 'system:schedule') return false
        if (d.logFilterTab === 'Scheduled' && r.created_by !== 'system:schedule') return false
        if (d.searchRunId && !r.id.toLowerCase().includes(d.searchRunId.toLowerCase())) return false
        if (d.filterStatus !== 'ALL' && r.status !== d.filterStatus) return false
        if (d.filterOwner !== 'ALL' && r.created_by !== d.filterOwner) return false
        return true
      }),
    [
      d.runs.runs,
      d.selectedSuiteFilter,
      d.logFilterTab,
      d.searchRunId,
      d.filterStatus,
      d.filterOwner,
    ],
  )

  const uniqueOwners = useMemo(
    () => [...new Set(d.runs.runs.map((r) => r.created_by))].sort(),
    [d.runs.runs],
  )

  const formatDate = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString() : '-'

  const columns = [
    {
      title: d.t('runId'),
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
            ? (d.lang === 'zh' ? '已锁定 (保护文件不被清理)' : 'Locked (Protected from physical cleanup)')
            : (d.lang === 'zh' ? '未锁定 (可进行物理清理)' : 'Unlocked (Eligible for physical cleanup)')
          }
        >
          <Button
            type="tertiary"
            icon={locked ? <IconLock style={{ color: 'var(--semi-color-warning)' }} /> : <IconUnlock style={{ color: 'var(--semi-color-text-3)' }} />}
            size="small"
            onClick={(e) => {
              e.stopPropagation()
              d.runs.handleToggleLock(record.id, e)
            }}
          />
        </Tooltip>
      )
    },
    {
      title: d.t('targetSuite'),
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
      title: d.t('status'),
      dataIndex: 'status',
      key: 'status',
      render: (status: Run['status'], record: Run) => {
        const color = statusTagColor(status, record.passed)
        let icon: React.ReactNode = null
        if (status === 'queued') {
          icon = <IconRefresh spin />
        } else if (status === 'running') {
          icon = <IconActivity spin />
        }

        return (
          <Tag color={color} size="large" style={{ textTransform: 'capitalize', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
            {icon}
            {d.t(`status_${status}`)}
          </Tag>
        )
      }
    },
    {
      title: d.t('owner'),
      dataIndex: 'created_by',
      key: 'created_by',
      render: (text: string) => {
        return <Tag color="grey" size="large">{text}</Tag>
      }
    },
    {
      title: d.t('results'),
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
      title: d.t('passRate'),
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
      title: d.t('duration'),
      dataIndex: 'summary',
      key: 'duration',
      render: (summary: Run['summary']) => (
        <span style={{ fontFamily: 'var(--font-mono)' }}>
          {formatDuration(summary?.duration_ms)}
        </span>
      )
    },
    {
      title: d.t('createdAt'),
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

  const onRow = (record: Run | undefined) => {
    return {
      onClick: () => {
        if (record) d.runs.setSelectedRunId(record.id)
      },
      onKeyDown: activateOnKey(() => {
        if (record) d.runs.setSelectedRunId(record.id)
      }),
      className: record?.id === d.runs.selectedRunId ? styles.rowSelected : ''
    }
  }

  const resetFilters = () => {
    d.setSearchRunId('')
    d.setFilterStatus('ALL')
    d.setFilterOwner('ALL')
  }

  const isFilterActive = !!(d.searchRunId || d.filterStatus !== 'ALL' || d.filterOwner !== 'ALL')

  return (
    <div className={styles.tableCard}>
      <div className={styles.tableHeader} style={{ flexWrap: 'wrap', gap: '1rem' }}>
        <div className={styles.tableTitleGroup}>
          <IconActivity size="large" style={{ color: 'var(--semi-color-text-2)' }} />
          <h2 data-testid="execution-records-title" style={{ margin: 0 }}>{d.t('executionRecords')}</h2>
        </div>

        {/* Segmented Filter Radio Group */}
        <RadioGroup
          type="button"
          buttonSize="middle"
          value={d.logFilterTab}
          onChange={e => d.setLogFilterTab(e.target.value as any)}
          style={{ marginRight: 'auto' }}
        >
          <Radio value="All">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <IconActivity />
              <span>{d.lang === 'zh' ? '全部记录' : 'All Runs'}</span>
            </span>
          </Radio>
          <Radio value="Manual">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <IconUser />
              <span>{d.lang === 'zh' ? '手动触发' : 'Manually Triggered'}</span>
            </span>
          </Radio>
          <Radio value="Scheduled">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <IconClock />
              <span>{d.lang === 'zh' ? '定时触发' : 'Scheduled Runs'}</span>
            </span>
          </Radio>
        </RadioGroup>

        <Button
          icon={<IconRefresh />}
          onClick={d.runs.fetchRuns}
          aria-label={d.t('refreshLogs')}
          title={d.t('refreshLogs')}
        />
      </div>

      {/* 搜索与过滤工具栏 */}
      <div className={styles.filterToolbar}>
        {/* Run ID 搜索输入框 */}
        <Input
          prefix={<IconSearch style={{ color: 'var(--semi-color-text-3)' }} />}
          placeholder={d.t('searchRunIdPlaceholder')}
          value={d.searchRunId}
          onChange={value => d.setSearchRunId(value)}
          showClear
          style={{ width: '220px' }}
        />

        {/* 状态筛选下拉框 */}
        <Select
          value={d.filterStatus}
          onChange={value => d.setFilterStatus(value as string)}
          style={{ width: '150px' }}
          placeholder={d.t('filterStatusPlaceholder')}
        >
          <Select.Option value="ALL">{d.t('filterStatusPlaceholder')}</Select.Option>
          <Select.Option value="queued">{d.t('status_queued')}</Select.Option>
          <Select.Option value="running">{d.t('status_running')}</Select.Option>
          <Select.Option value="completed">{d.t('status_completed')}</Select.Option>
          <Select.Option value="failed">{d.t('status_failed')}</Select.Option>
          <Select.Option value="timeout">{d.t('status_timeout')}</Select.Option>
        </Select>

        {/* 执行人筛选下拉框 */}
        <Select
          value={d.filterOwner}
          onChange={value => d.setFilterOwner(value as string)}
          style={{ width: '150px' }}
          placeholder={d.t('filterOwnerPlaceholder')}
        >
          <Select.Option value="ALL">{d.t('filterOwnerPlaceholder')}</Select.Option>
          {uniqueOwners.map(owner => (
            <Select.Option key={owner} value={owner}>
              {owner}
            </Select.Option>
          ))}
        </Select>

        {/* 一键清除筛选 */}
        {isFilterActive && (
          <Button
            type="warning"
            theme="borderless"
            onClick={resetFilters}
            style={{ marginLeft: 'auto' }}
          >
            {d.t('clearFilters')}
          </Button>
        )}
      </div>

      {d.runs.loading && d.runs.runs.length === 0 ? (
        <div className={styles.loadingState}>
          <Spin size="large" />
          <p style={{ marginTop: '1rem' }}>{d.t('loadingHistory')}</p>
        </div>
      ) : d.runs.runs.length === 0 ? (
        <div className={styles.emptyState}>
          <IconBolt style={{ color: 'var(--semi-color-warning)', fontSize: '48px' }} />
          <h3>{d.t('noRunsTitle')}</h3>
          <p>{d.t('noRunsDesc')}</p>
          <Button
            theme="solid"
            type="primary"
            icon={<IconPlay />}
            onClick={() => d.setIsTriggerModalOpen(true)}
            style={{ marginTop: '1.5rem' }}
          >
            {d.t('launchFirstRun')}
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
