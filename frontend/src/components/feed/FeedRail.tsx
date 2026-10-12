// The feed's right-hand panel: priority breakdown, filters, the tags in this feed.
import { Check, CheckCheck } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { FeedItem, Tier } from '../../api/types'

const WINDOWS = [
  { hours: 12, label: 'Last 12 hours' },
  { hours: 24, label: 'Last 24 hours' },
  { hours: 72, label: 'Last 3 days' },
  { hours: 168, label: 'Last 7 days' },
]

interface Props {
  counts: { critical?: number; high?: number; normal?: number }
  total?: number
  unread?: number
  tier?: Tier
  unreadOnly: boolean
  hideRoutine: boolean
  hours: number
  items: FeedItem[]
  activeOutlet?: string
  onTier: (tier: Tier | null) => void
  onShow: (mode: 'all' | 'unread' | 'routine') => void
  onHours: (hours: number) => void
  onOutlet: (name: string | null) => void
  onMarkAll: () => void
}

const TIERS: { tier: 'critical' | 'high' | 'normal'; label: string }[] = [
  { tier: 'critical', label: 'Critical' },
  { tier: 'high', label: 'High' },
  { tier: 'normal', label: 'Normal' },
]

export function FeedRail(props: Props) {
  const { counts, total, unread, tier, unreadOnly, hideRoutine, hours, items, activeOutlet } = props
  const max = Math.max(1, counts.critical ?? 0, counts.high ?? 0, counts.normal ?? 0)

  const [showAllSources, setShowAllSources] = useState(false)

  // Which outlets the stories in view come from (a story reported by three outlets counts
  // for each of them). One click shows only that outlet's stories.
  const sources = useMemo(() => {
    const seen = new Map<string, number>()
    for (const item of items) for (const name of item.outlets ?? []) seen.set(name, (seen.get(name) ?? 0) + 1)
    return [...seen.entries()].map(([name, n]) => ({ name, n })).sort((a, b) => b.n - a.n || a.name.localeCompare(b.name))
  }, [items])
  const topSource = Math.max(1, ...sources.map((x) => x.n))
  const shownSources = showAllSources ? sources : sources.slice(0, 6)

  const filterOptions = [
    { mode: 'all' as const, label: 'All stories', on: !unreadOnly && !hideRoutine },
    { mode: 'unread' as const, label: 'Unread only', on: unreadOnly },
    { mode: 'routine' as const, label: 'Hide routine', on: hideRoutine },
  ]

  return (
    <aside className="rail" aria-label="Feed summary and filters">
      <section className="rail-card">
        <h2 className="rail-title">Priority breakdown</h2>
        <ul className="rail-tiers">
          {TIERS.map(({ tier: t, label: name }) => {
            const n = counts[t]
            return (
              <li key={t}>
                <button type="button" className={`rail-tier rail-${t}${tier === t ? ' is-active' : ''}`} onClick={() => props.onTier(tier === t ? null : t)} aria-pressed={tier === t}>
                  <span className="rail-tier-row">
                    <span className="rail-dot" /> {name}
                    <strong className="tabular">{n ?? '–'}</strong>
                  </span>
                  <span className="rail-bar">
                    <span style={{ width: `${((n ?? 0) / max) * 100}%` }} />
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
        <p className="rail-unread">
          <span>Unread</span>
          <span>
            <strong className="tabular">{unread ?? '–'}</strong> of {total ?? '–'}
          </span>
        </p>
      </section>

      <section className="rail-card">
        <h2 className="rail-title">Filter</h2>
        <div className="rail-options" role="group" aria-label="Show">
          {filterOptions.map((o) => (
            <button key={o.mode} type="button" className={`rail-option${o.on ? ' is-on' : ''}`} aria-pressed={o.on} onClick={() => props.onShow(o.mode)}>
              <Check size={15} className="rail-check" /> {o.label}
            </button>
          ))}
        </div>
        <label className="rail-field">
          <span>Time range</span>
          <select value={hours} onChange={(e) => props.onHours(Number(e.target.value))}>
            {WINDOWS.map((w) => (
              <option key={w.hours} value={w.hours}>
                {w.label}
              </option>
            ))}
          </select>
        </label>
      </section>

      {(sources.length > 0 || activeOutlet) && (
        <section className="rail-card">
          <h2 className="rail-title">Sources in this feed</h2>
          <ul className="rail-sources">
            {activeOutlet && !sources.some((x) => x.name === activeOutlet) && (
              <li>
                <SourceRow name={activeOutlet} n={0} max={1} active onClick={() => props.onOutlet(null)} />
              </li>
            )}
            {shownSources.map(({ name, n }) => (
              <li key={name}>
                <SourceRow name={name} n={n} max={topSource} active={activeOutlet === name} onClick={() => props.onOutlet(activeOutlet === name ? null : name)} />
              </li>
            ))}
          </ul>
          {sources.length > 6 && (
            <button type="button" className="rail-more" onClick={() => setShowAllSources((v) => !v)}>
              {showAllSources ? 'Show fewer' : `Show all ${sources.length} sources`}
            </button>
          )}
        </section>
      )}

      <button type="button" className="rail-markall" onClick={props.onMarkAll} disabled={!unread}>
        <CheckCheck size={16} /> Mark all read
      </button>
      <p className="rail-hint">
        Press <kbd>?</kbd> for keyboard shortcuts
      </p>
    </aside>
  )
}

/** "Economic Times - Economy" -> publisher "Economic Times", section "Economy". */
function splitName(name: string): { publisher: string; section: string | null } {
  const at = name.indexOf(' - ')
  return at > 0 ? { publisher: name.slice(0, at), section: name.slice(at + 3) } : { publisher: name, section: null }
}

function initials(publisher: string) {
  const words = publisher.replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean)
  return (words.length > 1 ? words[0][0] + words[1][0] : publisher.slice(0, 2)).toUpperCase()
}

// A stable colour per publisher, so the same outlet always looks the same.
function hue(publisher: string) {
  let h = 0
  for (const ch of publisher) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}

function SourceRow({ name, n, max, active, onClick }: { name: string; n: number; max: number; active: boolean; onClick: () => void }) {
  const { publisher, section } = splitName(name)
  return (
    <button type="button" className={`rail-source${active ? ' is-active' : ''}`} onClick={onClick} aria-pressed={active} title={active ? 'Show all sources' : `Only stories from ${name}`} data-outlet={name}>
      <span className="rail-source-badge" style={{ ['--h' as string]: hue(publisher) }}>
        {initials(publisher)}
      </span>
      <span className="rail-source-text">
        <span className="rail-source-name">{publisher}</span>
        {section && <span className="rail-source-section">{section}</span>}
      </span>
      <span className="rail-source-count">
        <strong className="tabular">{n}</strong>
        <span className="rail-source-bar">
          <span style={{ width: `${(n / max) * 100}%` }} />
        </span>
      </span>
    </button>
  )
}
