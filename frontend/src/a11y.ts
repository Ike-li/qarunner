// Accessibility helpers (FE-5). Kept dependency-free; pairs with role="button"
// + tabIndex={0} on non-button elements that carry an onClick, so they become
// reachable and activatable by keyboard.

import type { KeyboardEvent } from 'react'

/** onKeyDown handler that mirrors an onClick: fire `action` on Enter/Space
 *  (preventing the default page-scroll on Space). */
export function activateOnKey(action: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      action()
    }
  }
}
