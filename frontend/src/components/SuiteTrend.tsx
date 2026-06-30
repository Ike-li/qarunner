import { useEffect, useState } from 'react'
import { TrendingUp } from 'lucide-react'
import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'
import { latestPassRatePct, trendGeometry } from '../runTrend'
import type { RunTrend } from '../types'

const W = 320
const H = 48

/** Pass-rate trend sparkline for the selected suite (cross-run stage 1).
 *  Renders only when a suite is filtered; lazily fetches GET /runs/trend and
 *  draws a hand-rolled SVG polyline (no chart dependency). */
export function SuiteTrend() {
  const d = useDashboard()
  const suite = d.selectedSuiteFilter
  const [trend, setTrend] = useState<RunTrend | null>(null)

  // Fetch when the filtered suite changes; cancel guard drops a stale response.
  useEffect(() => {
    if (!suite) {
      setTrend(null)
      return
    }
    let cancelled = false
    setTrend(null)
    d.apiFetch(`/runs/trend?tests_path=${encodeURIComponent(suite)}&limit=50`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (!cancelled) setTrend(data) })
      .catch(() => { if (!cancelled) setTrend(null) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suite])

  if (!suite) return null

  const points = trend?.points ?? []
  const pct = latestPassRatePct(points)
  const geo = trendGeometry(points, W, H)

  return (
    <section className={styles.statCard} data-testid="suite-trend" style={{ display: 'block' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <TrendingUp size={16} style={{ color: '#71717a' }} />
        <span className={styles.statLabel}>
          {d.t('trendTitle')}: <code>{suite}</code>
        </span>
        {pct !== null && (
          <span style={{ marginLeft: 'auto', fontWeight: 600 }} data-testid="suite-trend-latest">
            {pct}%
          </span>
        )}
      </div>
      {points.length < 2 ? (
        <span className={styles.statSubLabel} data-testid="suite-trend-insufficient">
          {d.t('trendInsufficient')}
        </span>
      ) : (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          height={H}
          preserveAspectRatio="none"
          data-testid="suite-trend-svg"
        >
          <polyline
            points={geo.polyline}
            fill="none"
            stroke="var(--semi-color-success)"
            strokeWidth="2"
          />
          {geo.last && (
            <circle cx={geo.last.x} cy={geo.last.y} r="3" fill="var(--semi-color-success)" />
          )}
        </svg>
      )}
    </section>
  )
}
