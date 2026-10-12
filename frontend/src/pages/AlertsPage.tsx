import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BellRing, CheckCheck, ExternalLink, Pin, Send, Settings2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import * as endpoints from '../api/endpoints'
import type { AlertItem } from '../api/types'
import { StoryListSkeleton } from '../components/feed/StoryList'
import { ScoreRing } from '../components/TierBadge'
import { EmptyState } from '../components/Feedback'
import { relativeTime } from '../lib/time'
import '../styles/feed.css'

const TELEGRAM_LABEL: Record<AlertItem['telegram_status'], string | null> = {
  sent: 'Sent to Telegram',
  pending: 'Sending to Telegram…',
  failed: 'Telegram failed',
  quiet: 'Held: quiet hours',
  skipped: null,
}

function dayLabel(iso: string) {
  const date = new Date(iso)
  const today = new Date()
  const yesterday = new Date(Date.now() - 86_400_000)
  if (date.toDateString() === today.toDateString()) return 'Today'
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return date.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })
}

export function AlertsPage() {
  const queryClient = useQueryClient()
  const [unreadOnly, setUnreadOnly] = useState(false)
  const alerts = useInfiniteQuery({
    queryKey: ['alerts', 'list', unreadOnly],
    queryFn: ({ pageParam, signal }) => endpoints.alerts({ unread_only: unreadOnly, offset: pageParam }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['alerts'] })
  const readAll = useMutation({ mutationFn: endpoints.readAllAlerts, onSuccess: refresh })
  const readOne = useMutation({ mutationFn: endpoints.readAlert, onSuccess: refresh })

  const items = alerts.data?.pages.flatMap((p) => p.items) ?? []
  const unread = alerts.data?.pages[0]?.unread ?? 0
  const groups: [string, AlertItem[]][] = []
  for (const item of items) {
    const label = dayLabel(item.created_at)
    const last = groups[groups.length - 1]
    if (last && last[0] === label) last[1].push(item)
    else groups.push([label, [item]])
  }

  return (
    <div className="list-page">
      <header className="feed-header">
        <div className="feed-heading">
          <p className="feed-greeting">What crossed your alert line</p>
          <h1>Alerts</h1>
        </div>
        <div className="feed-controls">
          <Link to="/settings/notifications" className="btn btn-secondary">
            <Settings2 size={16} /> Notification settings
          </Link>
          <button type="button" className="btn btn-ghost" disabled={!unread || readAll.isPending} onClick={() => readAll.mutate()}>
            <CheckCheck size={16} /> Mark all read
          </button>
        </div>
      </header>

      <div className="feed-filters">
        <button type="button" className="pill-toggle" aria-pressed={!unreadOnly} onClick={() => setUnreadOnly(false)}>
          All
        </button>
        <button type="button" className="pill-toggle" aria-pressed={unreadOnly} onClick={() => setUnreadOnly(true)}>
          Unread {unread > 0 && <span className="count-badge">{unread}</span>}
        </button>
        <span className="feed-threshold">One alert per event: several outlets reporting the same thing alert you once.</span>
      </div>

      {alerts.isPending ? (
        <StoryListSkeleton count={4} />
      ) : items.length === 0 ? (
        <div className="feed-empty">
          <EmptyState
            icon={<BellRing size={24} />}
            title={unreadOnly ? 'No unread alerts' : 'No alerts yet'}
            action={
              <Link to="/settings/notifications" className="btn btn-secondary">
                Connect Telegram
              </Link>
            }
          >
            When a story crosses your alert line (or a pinned event happens) it appears here, as a pop-up in the app, and on Telegram if you connect it.
          </EmptyState>
        </div>
      ) : (
        groups.map(([label, group]) => (
          <section key={label}>
            <h2 className="list-day">
              {label} <span>{group.length}</span>
            </h2>
            <ul className="fr-list">
              {group.map((a) => {
                const telegram = TELEGRAM_LABEL[a.telegram_status]
                const why = a.reasons.filter((r) => r.kind !== 'importance' && r.kind !== 'ai').slice(0, 3)
                return (
                  <li key={a.id} className={`fr${a.read ? ' is-read' : ''}`} onClick={() => !a.read && readOne.mutate(a.id)}>
                    <ScoreRing score={a.score} tier={a.tier} size={54} />
                    <div className="fr-body">
                      <div className="fr-meta">
                        {a.tier && (
                          <span className={`fr-tier fr-${a.tier}`}>
                            <span className="fr-dot" /> {a.tier[0].toUpperCase() + a.tier.slice(1)}
                          </span>
                        )}
                        {a.pinned && (
                          <span className="fr-flag fr-pin">
                            <Pin size={11} /> Pinned
                          </span>
                        )}
                        <span className="fr-sep">·</span>
                        <span className="fr-source">{a.source}</span>
                        <time className="fr-time" dateTime={a.created_at} title={new Date(a.created_at).toLocaleString()}>
                          {relativeTime(a.created_at)}
                        </time>
                      </div>
                      <h3 className="fr-title">
                        {!a.read && <span className="fr-unread" aria-label="Unread" />}
                        <a href={a.url} target="_blank" rel="noopener noreferrer">
                          {a.title}
                        </a>
                      </h3>
                      <div className="fr-foot">
                        <div className="fr-chips">
                          {telegram && (
                            <span className={`fr-chip telegram-${a.telegram_status}`}>
                              <Send size={12} style={{ marginRight: 6 }} /> {telegram}
                            </span>
                          )}
                          {why.map((r) => (
                            <span key={r.label} className="fr-chip">
                              {r.label.replace(/^(Entity|Topic|Event|Keyword|Source|Region|Pinned): /, '')}
                            </span>
                          ))}
                        </div>
                        <div className="fr-actions">
                          {!a.read && (
                            <button type="button" className="fr-act is-strong" title="Mark read" aria-label="Mark read" onClick={() => readOne.mutate(a.id)}>
                              <CheckCheck size={16} />
                            </button>
                          )}
                          <a className="fr-act" href={a.url} target="_blank" rel="noopener noreferrer" title="Open at the source" aria-label="Open at the source">
                            <ExternalLink size={16} />
                          </a>
                        </div>
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          </section>
        ))
      )}
      {alerts.hasNextPage && (
        <button type="button" className="feed-more" onClick={() => alerts.fetchNextPage()}>
          Load more
        </button>
      )}
    </div>
  )
}
