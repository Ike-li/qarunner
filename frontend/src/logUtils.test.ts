import { describe, expect, it } from 'vitest'

import { classifyLogLine, formatDuration, matchesLogLevel } from './logUtils'

describe('classifyLogLine', () => {
  it('marks a passed line as success', () => {
    // A pass line *without* the ==== banner — a banner line classifies as a
    // header (that check runs first), which the next case pins.
    expect(classifyLogLine('1 passed in 0.12s')).toBe('success')
    expect(classifyLogLine('tests/test_a.py::test_ok PASSED in 0.01s')).toBe('success')
  })

  it('treats the ==== banner as a header even when it mentions passed', () => {
    expect(classifyLogLine('===== 3 passed in 0.12s =====')).toBe('header')
  })

  it('does not colour a PASSED-naming failure line as success (FE-4 precedence bug)', () => {
    // Before the parenthesis fix, `||` / `&&` precedence made any line merely
    // containing 'PASSED' green, even a failure line. It must classify as error.
    expect(classifyLogLine('FAILED tests/test_x.py::test_PASSED_path')).toBe('error')
  })

  it('classifies headers, errors, warnings and plain lines', () => {
    expect(classifyLogLine('==== test session starts ====')).toBe('header')
    expect(classifyLogLine('---- captured stdout ----')).toBe('header')
    expect(classifyLogLine('E   AssertionError: nope')).toBe('error')
    expect(classifyLogLine('ValueError: bad input')).toBe('error')
    expect(classifyLogLine('WARNING: deprecated thing')).toBe('warning')
    expect(classifyLogLine('just some neutral text')).toBe('plain')
  })
})

describe('matchesLogLevel', () => {
  it('ALL matches every line', () => {
    expect(matchesLogLevel('whatever', 'ALL')).toBe(true)
  })

  it('ERROR matches failures, exceptions and tracebacks', () => {
    expect(matchesLogLevel('Traceback (most recent call last):', 'ERROR')).toBe(true)
    expect(matchesLogLevel('E   boom', 'ERROR')).toBe(true)
    expect(matchesLogLevel('1 passed in 0.1s', 'ERROR')).toBe(false)
  })

  it('WARNING and SUCCESS match their tokens', () => {
    expect(matchesLogLevel('DeprecationWarning: x', 'WARNING')).toBe(true)
    expect(matchesLogLevel('1 passed', 'SUCCESS')).toBe(true)
    expect(matchesLogLevel('1 failed', 'SUCCESS')).toBe(false)
  })
})

describe('formatDuration', () => {
  it('renders a dash for null/undefined', () => {
    expect(formatDuration(null)).toBe('-')
    expect(formatDuration(undefined)).toBe('-')
  })

  it('renders milliseconds below a second and seconds above', () => {
    expect(formatDuration(500)).toBe('500ms')
    expect(formatDuration(999)).toBe('999ms')
    expect(formatDuration(1500)).toBe('1.5s')
  })
})
