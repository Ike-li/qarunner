// Pure helpers for the suite pass-rate trend sparkline (stage 1). Kept in a .ts
// module so vitest covers the coordinate maths the .tsx component only renders.
// Mirrors the API's RunTrendResponse shape.

import type { TrendPoint } from './types'

export interface TrendGeometry {
  /** "x,y x,y …" string for an SVG <polyline>. */
  polyline: string
  /** Last point's coords, for the marker dot. */
  last: { x: number; y: number } | null
}

// Map a pass-rate series to SVG coords in a (width × height) box. pass_rate is
// 0..1; a higher rate sits higher on screen (smaller y). A single point is
// pinned to the right edge so the user still sees a marker.
export function trendGeometry(
  points: TrendPoint[],
  width: number,
  height: number,
): TrendGeometry {
  if (points.length === 0) return { polyline: '', last: null }
  const xAt = (i: number) =>
    points.length === 1 ? width : (i * width) / (points.length - 1)
  const yAt = (rate: number) => (1 - rate) * height
  const coords = points.map((p, i) => ({ x: xAt(i), y: yAt(p.pass_rate) }))
  const polyline = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
  return { polyline, last: coords[coords.length - 1] }
}

// Latest point's pass_rate as a one-decimal percentage (null when no points).
export function latestPassRatePct(points: TrendPoint[]): number | null {
  if (points.length === 0) return null
  return Math.round(points[points.length - 1].pass_rate * 1000) / 10
}
