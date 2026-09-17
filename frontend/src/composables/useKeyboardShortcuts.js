import { onMounted, onBeforeUnmount, unref } from 'vue'
import { isDialogOpen } from '@/utils/dialogs'

/**
 * Generic global keyboard shortcuts composable.
 *
 * Usage:
 * useKeyboardShortcuts({
 *   active: () => true,            // boolean | () => boolean (reactive allowed)
 *   shortcuts: [
 *     { keys: 'Escape', action: close },
 *     { keys: ['Delete', 'Backspace'], action: onDelete },
 *     { match: e => (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'd', action: duplicate }
 *   ],
 *   ignoreTyping: true             // skip when focus is in input/textarea/contenteditable (default true)
 * })
 */
export function useKeyboardShortcuts(options) {
  const {
    active = true,
    shortcuts = [],
    ignoreTyping = true,
    target = typeof window !== 'undefined' ? window : null,
    skipWhenDialogOpen = true,
    capture = false,
  } = options || {}

  function isTypingEvent(e) {
    if (!ignoreTyping) return false
    const el = e.target
    if (!el) return false
    const tag = el.tagName
    return (
      el.isContentEditable ||
      tag === 'INPUT' ||
      tag === 'TEXTAREA' ||
      tag === 'SELECT' ||
      (el.closest && el.closest('[contenteditable="true"]'))
    )
  }

  function matchShortcut(def, e) {
    if (def.match) return def.match(e)
    // Bare keys like `c` must not steal Ctrl/Cmd+C (copy), Ctrl+F, etc.
    // Combos that need a modifier use `match` instead of `keys`.
    if (!def.allowModifiers && (e.metaKey || e.ctrlKey || e.altKey)) return false
    let keys = def.keys
    if (!keys) return false
    if (!Array.isArray(keys)) keys = [keys]
    return keys.some((k) => k === e.key)
  }

  function handler(e) {
    if (!target) return
    const isActive = typeof active === 'function' ? active() : unref(active)
    if (!isActive) return
    if (isTypingEvent(e)) return
    if (skipWhenDialogOpen && isDialogOpen()) return

    for (const def of shortcuts) {
      if (!def) continue
      if (def.guard && !def.guard(e)) continue
      if (matchShortcut(def, e)) {
        if (def.preventDefault !== false) e.preventDefault()
        if (def.stopPropagation) e.stopPropagation()
        def.action?.(e)
        break
      }
    }
  }

  onMounted(() => {
    target?.addEventListener('keydown', handler, capture)
  })
  onBeforeUnmount(() => {
    target?.removeEventListener('keydown', handler, capture)
  })

  return {
    stop: () => target?.removeEventListener('keydown', handler, capture),
  }
}
