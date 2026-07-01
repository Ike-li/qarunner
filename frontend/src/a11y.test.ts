import { describe, it, expect, vi } from 'vitest'
import { activateOnKey } from './a11y'

describe('activateOnKey', () => {
  it('calls action on Enter key', () => {
    const action = vi.fn()
    const handler = activateOnKey(action)
    handler({ key: 'Enter', preventDefault: vi.fn() } as any)
    expect(action).toHaveBeenCalledOnce()
  })

  it('calls action on Space key', () => {
    const action = vi.fn()
    const handler = activateOnKey(action)
    handler({ key: ' ', preventDefault: vi.fn() } as any)
    expect(action).toHaveBeenCalledOnce()
  })

  it('prevents default on Space', () => {
    const preventDefault = vi.fn()
    const handler = activateOnKey(vi.fn())
    handler({ key: ' ', preventDefault } as any)
    expect(preventDefault).toHaveBeenCalledOnce()
  })

  it('prevents default on Enter', () => {
    const preventDefault = vi.fn()
    const handler = activateOnKey(vi.fn())
    handler({ key: 'Enter', preventDefault } as any)
    expect(preventDefault).toHaveBeenCalledOnce()
  })

  it('does not call action on other keys', () => {
    const action = vi.fn()
    const handler = activateOnKey(action)
    handler({ key: 'Tab', preventDefault: vi.fn() } as any)
    handler({ key: 'Escape', preventDefault: vi.fn() } as any)
    handler({ key: 'a', preventDefault: vi.fn() } as any)
    expect(action).not.toHaveBeenCalled()
  })
})
