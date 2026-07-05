import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RunDetailsDrawer } from './RunDetailsDrawer'

const dashboard = vi.hoisted(() => ({ current: {} as any }))

vi.mock('@douyinfe/semi-ui', async () => {
  const React = await import('react')
  const Tabs = ({ children }: { children: React.ReactNode }) =>
    React.createElement('div', {}, children)
  ;(Tabs as any).TabPane = ({ tab }: { tab: React.ReactNode }) =>
    React.createElement('div', {}, tab)
  return {
    SideSheet: ({
      visible,
      title,
      children,
    }: {
      visible: boolean
      title: React.ReactNode
      children: React.ReactNode
    }) => (visible ? React.createElement('div', {}, title, children) : null),
    Tabs,
  }
})

vi.mock('../hooks/DashboardContext', () => ({
  useDashboard: () => dashboard.current,
}))

function makeDashboard() {
  const selectedRun = {
    id: 'run-001',
    status: 'completed',
    runner: 'pytest',
    created_by: 'alice',
    tests_path: 'checkout',
    args: [],
    executor_mode: 'docker',
    summary: {
      total: 2,
      passed: 1,
      failed: 1,
      skipped: 0,
      error: 0,
      duration_ms: 789,
      pass_rate: 0.5,
    },
    report: null,
    exit_code: 1,
    error: null,
    passed: false,
    created_at: '2026-07-01T00:00:00Z',
    started_at: '2026-07-01T00:00:01Z',
    finished_at: '2026-07-01T00:00:02Z',
    stdout: null,
    stderr: null,
    locked: true,
    cases: [
      {
        suite: 'checkout',
        name: 'test_guest_checkout',
        status: 'failed',
        duration_ms: 456,
        message: 'Expected confirmation',
      },
    ],
  }

  return {
    lang: 'en',
    t: (key: string) => key,
    apiFetch: vi.fn(async () =>
      new Response(JSON.stringify({ artifacts: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
    terminalRef: { current: null },
    runs: {
      selectedRun,
      selectedRunDetails: selectedRun,
      detailsLoading: false,
      streamedStdout: '',
      isStreaming: false,
      closeDrawer: vi.fn(),
      handleCancelRun: vi.fn(),
      handleRerunRun: vi.fn(),
      handleDeleteRun: vi.fn(),
    },
    terminal: {
      drawerTab: 'report',
      setDrawerTab: vi.fn(),
      getFilteredLogs: (value: string) => value,
      renderFormattedLogs: (value: string) => value,
      copyToClipboard: vi.fn(),
      copySuccess: false,
      isTerminalFullscreen: false,
      setIsTerminalFullscreen: vi.fn(),
      isReportFullscreen: false,
      setIsReportFullscreen: vi.fn(),
      isTerminalHeightExpanded: false,
      terminalFontSize: 13,
    },
  }
}

describe('RunDetailsDrawer', () => {
  beforeEach(() => {
    dashboard.current = makeDashboard()
  })

  it('shows persisted case results on the report tab', () => {
    render(<RunDetailsDrawer />)

    expect(screen.getByTestId('run-case-results')).toBeVisible()
    expect(screen.getByTestId('run-case-result-0')).toHaveTextContent(
      'checkout::test_guest_checkout',
    )
    expect(screen.getByTestId('run-case-result-0')).toHaveTextContent('failed')
    expect(screen.getByTestId('run-case-result-0')).toHaveTextContent('456ms')
    expect(screen.getByTestId('run-case-result-0')).toHaveTextContent(
      'Expected confirmation',
    )
  })

  it('shows downloadable runner artifacts on the report tab', async () => {
    dashboard.current.runs.selectedRun = {
      ...dashboard.current.runs.selectedRun,
      runner: 'playwright',
    }
    dashboard.current.apiFetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          artifacts: [
            {
              path: 'failed-case/screenshot.png',
              size_bytes: 2048,
              content_type: 'image/png',
            },
            {
              path: 'failed-case/video.webm',
              size_bytes: 4096,
              content_type: 'video/webm',
            },
            {
              path: 'failed-case/trace.zip',
              size_bytes: 1024,
              content_type: 'application/zip',
            },
            {
              path: 'metadata.json',
              size_bytes: 128,
              content_type: 'application/json',
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    render(<RunDetailsDrawer />)

    await waitFor(() => {
      expect(dashboard.current.apiFetch).toHaveBeenCalledWith('/runs/run-001/artifacts')
    })

    expect(await screen.findByTestId('run-artifacts')).toBeVisible()
    expect(screen.getByTestId('run-artifacts-download-all')).toHaveTextContent(
      'downloadAllArtifacts',
    )
    expect(screen.getByTestId('run-artifacts-download-all')).toHaveAttribute(
      'href',
      '/runs/run-001/artifacts.zip',
    )
    const traceGroup = screen.getByTestId('run-artifact-group-trace')
    const screenshotGroup = screen.getByTestId('run-artifact-group-screenshot')
    const videoGroup = screen.getByTestId('run-artifact-group-video')
    const otherGroup = screen.getByTestId('run-artifact-group-other')

    expect(traceGroup.compareDocumentPosition(screenshotGroup)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    expect(within(traceGroup).getByTestId('run-artifact-link-0')).toHaveTextContent(
      'failed-case/trace.zip',
    )
    expect(within(traceGroup).getByTestId('run-artifact-link-0')).toHaveTextContent(
      'application/zip',
    )
    expect(within(traceGroup).getByTestId('run-artifact-link-0')).toHaveAttribute(
      'href',
      '/runs/run-001/artifacts/failed-case%2Ftrace.zip',
    )
    expect(within(screenshotGroup).getByTestId('run-artifact-link-1')).toHaveTextContent(
      'failed-case/screenshot.png',
    )
    expect(within(screenshotGroup).getByTestId('run-artifact-link-1')).toHaveTextContent(
      'image/png',
    )
    expect(within(videoGroup).getByTestId('run-artifact-link-2')).toHaveTextContent(
      'failed-case/video.webm',
    )
    expect(within(otherGroup).getByTestId('run-artifact-link-3')).toHaveTextContent(
      'metadata.json',
    )
  })
})
