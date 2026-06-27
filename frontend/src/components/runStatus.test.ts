import { describe, expect, it } from 'vitest'

import type { Run } from '../types'
import { passRateColor, statusTagColor } from './runStatus'

describe('statusTagColor', () => {
  it('maps completed + passed to green', () => {
    expect(statusTagColor('completed', true)).toBe('green')
  })

  it('maps completed + NOT passed to orange — distinct from a failed run', () => {
    // The collision this fixes: before, completed-with-failures and a hard
    // failed run were both 'red', so the status column could not tell them
    // apart (the 75%-pass rows in the dashboard rendered red).
    expect(statusTagColor('completed', false)).toBe('orange')
    expect(statusTagColor('completed', null)).toBe('orange')
    expect(statusTagColor('completed', false)).not.toBe(statusTagColor('failed', false))
  })

  it('maps a hard failed run to red', () => {
    expect(statusTagColor('failed', false)).toBe('red')
  })

  it('keeps queued / running / timeout colors', () => {
    expect(statusTagColor('queued', false)).toBe('grey')
    expect(statusTagColor('running', false)).toBe('blue')
    expect(statusTagColor('timeout', false)).toBe('amber')
  })

  it('falls back to grey for an unknown status', () => {
    expect(statusTagColor('weird' as Run['status'], false)).toBe('grey')
  })
})

describe('passRateColor', () => {
  it('is success green at 80 and above', () => {
    expect(passRateColor(80)).toBe('var(--semi-color-success)')
    expect(passRateColor(100)).toBe('var(--semi-color-success)')
  })

  it('is warning orange in the 50–79 band (75% no longer reads as red)', () => {
    expect(passRateColor(50)).toBe('var(--semi-color-warning)')
    expect(passRateColor(75)).toBe('var(--semi-color-warning)')
    expect(passRateColor(79)).toBe('var(--semi-color-warning)')
  })

  it('is danger red below 50', () => {
    expect(passRateColor(49)).toBe('var(--semi-color-danger)')
    expect(passRateColor(0)).toBe('var(--semi-color-danger)')
  })
})
