import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useAuth } from './useAuth'

describe('useAuth', () => {
  it('exports a function', () => {
    expect(typeof useAuth).toBe('function')
  })

  it('PERF: the returned object is referentially stable across a re-render with no state change', () => {
    const apiFetch = vi.fn()
    const { result, rerender } = renderHook(() => useAuth({ apiFetch }))
    const first = result.current
    rerender()
    expect(result.current).toBe(first)
  })
})
