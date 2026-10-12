// The story list with keyboard shortcuts. Used by the feed and the saved list.
import { X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import type { FeedItem } from '../../api/types'
import { FeedRow } from './FeedRow'

interface Props {
  items: FeedItem[]
  onMarkOpened: (item: FeedItem) => void
  onToggleRead: (item: FeedItem) => void
  onToggleSaved: (item: FeedItem) => void
  onFilter?: (kind: 'topic' | 'entity', slug: string) => void
  onFeedback?: (item: FeedItem, verdict: 'relevant' | 'not_relevant') => void
  searchRef?: RefObject<HTMLInputElement | null>
  footer?: ReactNode
}

const KEYS: [string, string][] = [
  ['j / ↓', 'Next story'],
  ['k / ↑', 'Previous story'],
  ['o / Enter', 'Open at the source'],
  ['x', 'Show or hide details'],
  ['s', 'Save or unsave'],
  ['m', 'Mark read or unread'],
  ['/', 'Search'],
  ['?', 'Show these keys'],
]

function typingIn(target: EventTarget | null) {
  const el = target as HTMLElement | null
  return Boolean(el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)))
}

export function StoryList({ items, onMarkOpened, onToggleRead, onToggleSaved, onFilter, onFeedback, searchRef, footer }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [showKeys, setShowKeys] = useState(false)
  const listRef = useRef<HTMLUListElement>(null)
  const selected = items.find((i) => i.story_id === selectedId) ?? null

  const select = useCallback((item: FeedItem) => setSelectedId(item.story_id), [])
  const toggleExpand = useCallback((item: FeedItem) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(item.story_id)) next.delete(item.story_id)
      else next.add(item.story_id)
      return next
    })
  }, [])
  const open = useCallback(
    (item: FeedItem) => {
      setSelectedId(item.story_id)
      onMarkOpened(item)
    },
    [onMarkOpened],
  )

  useEffect(() => {
    if (selectedId) listRef.current?.querySelector(`[data-story="${selectedId}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typingIn(e.target)) return
      const index = selected ? items.indexOf(selected) : -1
      const go = (n: number) => {
        const item = items[Math.max(0, Math.min(items.length - 1, n))]
        if (item) setSelectedId(item.story_id)
      }
      switch (e.key) {
        case 'j':
        case 'ArrowDown':
          e.preventDefault()
          go(index + 1)
          break
        case 'k':
        case 'ArrowUp':
          e.preventDefault()
          go(index < 0 ? 0 : index - 1)
          break
        case 'o':
        case 'Enter':
          if (selected) {
            e.preventDefault()
            onMarkOpened(selected)
            window.open(selected.url, '_blank', 'noopener,noreferrer')
          }
          break
        case 'x':
          if (selected) toggleExpand(selected)
          break
        case 's':
          if (selected) onToggleSaved(selected)
          break
        case 'm':
          if (selected) onToggleRead(selected)
          break
        case '/':
          if (searchRef?.current) {
            e.preventDefault()
            searchRef.current.focus()
          }
          break
        case 'Escape':
          setShowKeys(false)
          setSelectedId(null)
          break
        case '?':
          setShowKeys((v) => !v)
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items, selected, onMarkOpened, onToggleRead, onToggleSaved, toggleExpand, searchRef])

  return (
    <>
      <ul className="fr-list" ref={listRef}>
        {items.map((item) => (
          <FeedRow
            key={item.story_id}
            item={item}
            selected={item.story_id === selectedId}
            expanded={expanded.has(item.story_id)}
            onSelect={select}
            onToggleExpand={toggleExpand}
            onOpen={open}
            onToggleRead={onToggleRead}
            onToggleSaved={onToggleSaved}
            onFilter={onFilter}
            onFeedback={onFeedback}
          />
        ))}
      </ul>
      {footer}
      {showKeys && (
        <div className="keys-overlay" role="dialog" aria-label="Keyboard shortcuts" onClick={() => setShowKeys(false)}>
          <div className="keys-card" onClick={(e) => e.stopPropagation()}>
            <div className="keys-head">
              <h2>Keyboard shortcuts</h2>
              <button type="button" className="icon-btn" onClick={() => setShowKeys(false)} aria-label="Close">
                <X size={16} />
              </button>
            </div>
            <dl>
              {KEYS.map(([key, what]) => (
                <div key={key}>
                  <dt>
                    {key.split(' / ').map((k) => (
                      <kbd key={k}>{k}</kbd>
                    ))}
                  </dt>
                  <dd>{what}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}
    </>
  )
}

export function StoryListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <ul className="fr-list" aria-busy="true" aria-label="Loading stories">
      {Array.from({ length: count }, (_, i) => (
        <li key={i} className="fr fr-skeleton">
          <span className="skeleton" style={{ width: 54, height: 54, borderRadius: '50%' }} />
          <div className="fr-body">
            <span className="skeleton" style={{ width: 180, height: 11 }} />
            <span className="skeleton" style={{ width: `${62 + ((i * 9) % 30)}%`, height: 18 }} />
            <span className="skeleton" style={{ width: '45%', height: 22, borderRadius: 999 }} />
          </div>
        </li>
      ))}
    </ul>
  )
}
