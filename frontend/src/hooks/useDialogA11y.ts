import { useEffect, useRef } from 'react'

// Elements that can receive keyboard focus inside a dialog.
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

interface DialogA11yOptions {
  /** Whether the dialog is currently shown. While true, focus is trapped and Esc closes. */
  isOpen: boolean
  /** Invoked on Escape (callers wire the same fn to the close button / overlay click). */
  onClose: () => void
}

/**
 * Dialog accessibility, dependency-free (FE-5 Stage D). While `isOpen`:
 *  - moves focus into the dialog (first focusable element, else the container),
 *  - traps Tab / Shift+Tab within the returned container,
 *  - closes on Escape,
 *  - restores focus to the previously-focused element on close / unmount.
 *
 * The keydown listener is attached to the container (not document) so that
 * stacked dialogs don't both react to a single Escape — only the dialog holding
 * focus does. Pure UI a11y: it never touches app / SSE state.
 */
export function useDialogA11y<T extends HTMLElement = HTMLDivElement>({
  isOpen,
  onClose,
}: DialogA11yOptions) {
  const containerRef = useRef<T>(null)
  // Hold the latest onClose without re-running the effect: callers pass a fresh
  // arrow each render, and re-running would steal focus on every parent re-render
  // (which happens constantly during SSE streaming / polling).
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const container = containerRef.current
    if (!isOpen || !container) return

    const previouslyFocused = document.activeElement as HTMLElement | null

    const focusables = () => Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    const initial = focusables()
    if (initial.length > 0) {
      initial[0].focus()
    } else {
      container.focus()
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    container.addEventListener('keydown', handleKeyDown)
    return () => {
      container.removeEventListener('keydown', handleKeyDown)
      previouslyFocused?.focus?.()
    }
  }, [isOpen])

  return containerRef
}
