import { describe, it, expect, vi, afterEach } from 'vitest'
import { apiMutate } from './useApi'

describe('apiMutate', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('exports a function', () => {
    expect(typeof apiMutate).toBe('function')
  })

  it('returns null on network error', async () => {
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    const failingFetch = async () => { throw new Error('network') }
    const result = await apiMutate(failingFetch, '/test', { method: 'GET' }, 'fallback')
    expect(result).toBeNull()
  })

  it('B4: alerts the fallback message on network error, like the non-ok path does', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const failingFetch = async () => { throw new Error('network') }
    await apiMutate(failingFetch, '/test', { method: 'GET' }, 'fallback')
    expect(alertSpy).toHaveBeenCalledWith('fallback')
  })

  it('returns null on non-ok response', async () => {
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    const mockFetch = async () => ({
      ok: false,
      json: async () => ({ detail: 'bad request' }),
    }) as any
    const result = await apiMutate(mockFetch, '/test', { method: 'GET' }, 'fallback')
    expect(result).toBeNull()
  })

  it('returns response on success', async () => {
    const mockResp = { ok: true, json: async () => ({}) }
    const mockFetch = async () => mockResp as any
    const result = await apiMutate(mockFetch, '/test', { method: 'GET' }, 'fallback')
    expect(result).toBe(mockResp)
  })
})
