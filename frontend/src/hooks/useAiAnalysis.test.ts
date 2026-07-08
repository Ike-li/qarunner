import { describe, it, expect, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useAiAnalysis } from './useAiAnalysis'

const _DIAG = {
  category: 'assertion',
  confidence: 'HIGH',
  summary: 'an assertion failed',
  evidence: ['expected 200'],
  is_likely_regression: true,
  next_steps: [{ kind: 'inspect_log', action: 'read the traceback', reference: null }],
}

function okFetch(body: unknown) {
  return vi.fn(async () => ({ ok: true, json: async () => body }) as unknown as Response)
}

function failFetch() {
  return vi.fn(async () => ({ ok: false, json: async () => ({}) }) as unknown as Response)
}

function throwFetch() {
  return vi.fn(async () => {
    throw new Error('network')
  }) as unknown as ReturnType<typeof okFetch>
}

describe('useAiAnalysis', () => {
  it('load populates the diagnosis from GET', async () => {
    const apiFetch = okFetch({ enabled: true, diagnosis: _DIAG, detail: null })
    const { result } = renderHook(() => useAiAnalysis(apiFetch))
    await act(async () => {
      await result.current.load('r1')
    })
    expect(result.current.diagnosis?.category).toBe('assertion')
    expect(result.current.enabled).toBe(true)
    expect(result.current.loading).toBe(false)
    expect(apiFetch).toHaveBeenCalledWith('/runs/r1/ai-analysis', undefined)
  })

  it('load reflects a disabled provider', async () => {
    const apiFetch = okFetch({ enabled: false, diagnosis: null, detail: null })
    const { result } = renderHook(() => useAiAnalysis(apiFetch))
    await act(async () => {
      await result.current.load('r1')
    })
    expect(result.current.enabled).toBe(false)
    expect(result.current.diagnosis).toBeNull()
  })

  it('load flags error on a non-ok response', async () => {
    const { result } = renderHook(() => useAiAnalysis(failFetch()))
    await act(async () => {
      await result.current.load('r1')
    })
    expect(result.current.error).toBe(true)
    expect(result.current.loading).toBe(false)
  })

  it('load flags error when the request throws', async () => {
    const { result } = renderHook(() => useAiAnalysis(throwFetch()))
    await act(async () => {
      await result.current.load('r1')
    })
    expect(result.current.error).toBe(true)
  })

  it('generate POSTs and populates the diagnosis', async () => {
    const apiFetch = okFetch({ enabled: true, diagnosis: _DIAG, detail: null })
    const { result } = renderHook(() => useAiAnalysis(apiFetch))
    await act(async () => {
      await result.current.generate('r2')
    })
    expect(result.current.diagnosis?.confidence).toBe('HIGH')
    expect(apiFetch).toHaveBeenCalledWith('/runs/r2/ai-analysis', { method: 'POST' })
  })

  it('generate flags error on a non-ok response', async () => {
    const { result } = renderHook(() => useAiAnalysis(failFetch()))
    await act(async () => {
      await result.current.generate('r2')
    })
    expect(result.current.error).toBe(true)
  })

  it('generate flags error when the request throws', async () => {
    const { result } = renderHook(() => useAiAnalysis(throwFetch()))
    await act(async () => {
      await result.current.generate('r2')
    })
    expect(result.current.error).toBe(true)
  })

  it('reset restores the initial state', async () => {
    const apiFetch = okFetch({ enabled: false, diagnosis: null, detail: 'no failures' })
    const { result } = renderHook(() => useAiAnalysis(apiFetch))
    await act(async () => {
      await result.current.load('r1')
    })
    act(() => {
      result.current.reset()
    })
    expect(result.current.enabled).toBe(true)
    expect(result.current.detail).toBeNull()
    expect(result.current.error).toBe(false)
  })
})
