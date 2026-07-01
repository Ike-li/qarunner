import { describe, it, expect } from 'vitest'
import { apiMutate } from './useApi'

describe('apiMutate', () => {
  it('exports a function', () => {
    expect(typeof apiMutate).toBe('function')
  })

  it('returns null on network error', async () => {
    const failingFetch = async () => { throw new Error('network') }
    const result = await apiMutate(failingFetch, '/test', { method: 'GET' }, 'fallback')
    expect(result).toBeNull()
  })

  it('returns null on non-ok response', async () => {
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
