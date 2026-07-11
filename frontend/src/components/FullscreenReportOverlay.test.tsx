import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { FullscreenReportOverlay } from './FullscreenReportOverlay'

const dashboard = vi.hoisted(() => ({ current: {} as any }))

vi.mock('../hooks/DashboardContext', () => ({
  useDashboard: () => dashboard.current,
}))

function makeDashboard() {
  return {
    lang: 'en',
    t: (key: string) => key,
    runs: {
      selectedRun: { id: 'run-001' },
    },
    terminal: {
      setIsReportFullscreen: vi.fn(),
    },
  }
}

describe('FullscreenReportOverlay', () => {
  it('renders the report iframe with a sandbox attribute', () => {
    // S2: the report is a third party's HTML output (Allure, generated from
    // untrusted test code — attachment/step names are attacker-influenced)
    // served same-origin with no CSP. `sandbox` is the only isolation layer
    // between that content and an authenticated session.
    dashboard.current = makeDashboard()
    render(<FullscreenReportOverlay />)

    const iframe = screen.getByTitle('Allure Fullscreen Report')
    expect(iframe).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin')
  })
})
