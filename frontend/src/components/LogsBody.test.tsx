import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'

import { LogsBody } from './LogsBody'

const dashboard = vi.hoisted(() => ({ current: {} as any }))

vi.mock('../hooks/DashboardContext', () => ({
  useDashboard: () => dashboard.current,
}))

function makeDashboard(overrides: Record<string, any> = {}) {
  const { runs: runsOverrides, terminal: terminalOverrides, ...rest } = overrides
  dashboard.current = {
    lang: 'en',
    t: (key: string) => key,
    terminal: {
      renderFormattedLogs: (text: string) => text,
      ...terminalOverrides,
    },
    runs: {
      selectedRun: { id: 'run-001', stdout: '', stderr: '' },
      selectedRunDetails: null,
      isStreaming: false,
      streamedStdout: '',
      detailsLoading: false,
      detailsError: false,
      ...runsOverrides,
    },
    ...rest,
  }
}

function renderLogsBody(props: Partial<React.ComponentProps<typeof LogsBody>> = {}) {
  return render(
    <LogsBody filteredStdout="" filteredStderr="" filteredStreamed="" {...props} />,
  )
}

describe('LogsBody', () => {
  it('shows the load-error placeholder when showDetailsError and detailsError are both set', () => {
    makeDashboard({ runs: { detailsError: true } })
    renderLogsBody({ showDetailsError: true })
    expect(screen.getByText('runDetailsLoadError')).toBeInTheDocument()
  })

  it('ignores detailsError when showDetailsError is not passed (FullscreenTerminalOverlay has no error state)', () => {
    makeDashboard({ runs: { detailsError: true, isStreaming: true, streamedStdout: 'x' } })
    renderLogsBody({ filteredStreamed: 'x' })
    expect(screen.queryByText('runDetailsLoadError')).not.toBeInTheDocument()
    expect(screen.getByText('x')).toBeInTheDocument()
  })

  it('streaming with matching filtered output renders the formatted log', () => {
    makeDashboard({ runs: { isStreaming: true, streamedStdout: 'hello world' } })
    renderLogsBody({ filteredStreamed: 'hello world' })
    expect(screen.getByText('hello world')).toBeInTheDocument()
  })

  it('streaming with output that the search filter excludes shows "no matching logs"', () => {
    makeDashboard({ runs: { isStreaming: true, streamedStdout: 'hello world' } })
    renderLogsBody({ filteredStreamed: '' })
    expect(screen.getByText('No matching logs found')).toBeInTheDocument()
  })

  it('streaming with no output yet shows the waiting placeholder', () => {
    makeDashboard({ runs: { isStreaming: true, streamedStdout: '' } })
    renderLogsBody({ filteredStreamed: '' })
    expect(screen.getByText('waitingLogs')).toBeInTheDocument()
  })

  it('static run with matching filtered stdout renders it', () => {
    makeDashboard({ runs: { selectedRun: { id: 'r', stdout: 'out', stderr: '' } } })
    renderLogsBody({ filteredStdout: 'out' })
    expect(screen.getByText('out')).toBeInTheDocument()
  })

  it('static run with stdout/stderr present but filtered out shows "no matching logs"', () => {
    makeDashboard({ runs: { selectedRun: { id: 'r', stdout: 'out', stderr: '' } } })
    renderLogsBody({ filteredStdout: '', filteredStderr: '', filteredStreamed: '' })
    expect(screen.getByText('No matching logs found')).toBeInTheDocument()
  })

  it('renders stderr alongside stdout when both are present and matched', () => {
    makeDashboard({ runs: { selectedRun: { id: 'r', stdout: 'out', stderr: 'err' } } })
    renderLogsBody({ filteredStdout: 'out', filteredStderr: 'err' })
    expect(screen.getByText('out')).toBeInTheDocument()
    expect(screen.getByText('err')).toBeInTheDocument()
  })

  it('no run output yet, still loading details, shows the loading placeholder', () => {
    makeDashboard({
      runs: { selectedRun: { id: 'r', stdout: '', stderr: '' }, detailsLoading: true },
    })
    renderLogsBody()
    expect(screen.getByText('Loading console logs...')).toBeInTheDocument()
  })

  it('no run output, not loading, falls back to the empty-state placeholder', () => {
    makeDashboard({
      runs: { selectedRun: { id: 'r', stdout: '', stderr: '' }, detailsLoading: false },
    })
    renderLogsBody()
    expect(screen.getByText('noLogsAvailable')).toBeInTheDocument()
  })
})
