// One story in the feed: score ring, tier and source, headline, tags and actions.
// Clicking the row (not a link or button) expands why it ranked and every outlet's report.
import { Bookmark, BookmarkCheck, Bot, Check, ExternalLink, Pin, ThumbsDown, ThumbsUp, Zap } from 'lucide-react'
import { memo, useState, type MouseEvent } from 'react'
import type { FeedItem } from '../../api/types'
import { useReference } from '../../hooks/useReference'
import { relativeTime } from '../../lib/time'
import { ScoreRing } from '../TierBadge'

const TIER_LABEL = { critical: 'Critical', high: 'High', normal: 'Normal', low: 'Low' } as const
const BROAD = new Set(['india', 'united_states', 'china', 'russia', 'ukraine', 'israel', 'iran', 'pakistan', 'middle_east', 'european_union'])
const KIND_LABEL: Record<string, string> = {
  pin: 'Pinned',
  entity: 'Company / institution',
  event_type: 'Event',
  topic: 'Topic',
  keyword: 'Keyword',
  source: 'Source',
  region: 'Region',
  exclude: 'Muted',
}

interface Props {
  item: FeedItem
  selected: boolean
  expanded: boolean
  onSelect: (item: FeedItem) => void
  onToggleExpand: (item: FeedItem) => void
  onOpen: (item: FeedItem) => void
  onToggleRead: (item: FeedItem) => void
  onToggleSaved: (item: FeedItem) => void
  onFilter?: (kind: 'topic' | 'entity', slug: string) => void
  onFeedback?: (item: FeedItem, verdict: 'relevant' | 'not_relevant') => void
}

export const FeedRow = memo(function FeedRow(props: Props) {
  const { item, selected, expanded, onSelect, onToggleExpand, onOpen, onToggleRead, onToggleSaved, onFilter, onFeedback } = props
  const { label } = useReference()
  const [verdict, setVerdict] = useState<'relevant' | 'not_relevant' | null>(null)
  const tier = item.tier
  // At most four tags on the row (the event plus three more); the rest show in details.
  const entities = item.entities.filter((e) => !BROAD.has(e)).slice(0, 2)
  const topics = item.topics.slice(0, item.event_type ? 1 : 2)
  const reasons = item.reasons.filter((r) => r.kind !== 'importance' && r.kind !== 'ai')
  const notes = item.reasons.filter((r) => r.kind === 'importance' || r.kind === 'ai')

  // A click on the row's empty space expands it; links, buttons and chips do their own thing.
  const onRowClick = (e: MouseEvent) => {
    if ((e.target as HTMLElement).closest('a, button')) return
    onSelect(item)
    onToggleExpand(item)
  }
  const give = (v: 'relevant' | 'not_relevant') => {
    setVerdict(v)
    onFeedback?.(item, v)
  }

  return (
    <li
      className={`fr${item.is_read ? ' is-read' : ''}${selected ? ' is-selected' : ''}${expanded ? ' is-expanded' : ''}`}
      data-story={item.story_id}
      onClick={onRowClick}
      title={expanded ? undefined : 'Click for why it ranked and every outlet’s report'}
      aria-expanded={expanded}
    >
      <ScoreRing score={item.score} tier={tier} size={54} />

      <div className="fr-body">
        <div className="fr-meta">
          {tier && (
            <span className={`fr-tier fr-${tier}`}>
              <span className="fr-dot" /> {TIER_LABEL[tier]}
            </span>
          )}
          {item.alert && (
            <span className="fr-flag fr-alert" title="Crossed your alert line">
              <Zap size={11} /> Alert
            </span>
          )}
          {item.pinned && (
            <span className="fr-flag fr-pin" title="Pinned by your rules">
              <Pin size={11} /> Pinned
            </span>
          )}
          <span className="fr-sep">·</span>
          <span className="fr-source">{item.lead_source}</span>
          {item.source_count > 1 && <span className="fr-outlets">+{item.source_count - 1} more</span>}
          <time className="fr-time" dateTime={item.first_published_at} title={new Date(item.first_published_at).toLocaleString()}>
            {relativeTime(item.first_published_at)}
          </time>
        </div>

        <h3 className="fr-title">
          {!item.is_read && <span className="fr-unread" aria-label="Unread" />}
          <a href={item.url} target="_blank" rel="noopener noreferrer" onClick={() => onOpen(item)}>
            {item.title}
          </a>
        </h3>

        <div className="fr-foot">
          <div className="fr-chips">
            {item.event_type && <span className="fr-chip fr-chip-event">{label('event_type', item.event_type)}</span>}
            {entities.map((e) => (
              <button key={e} type="button" className="fr-chip" onClick={() => onFilter?.('entity', e)} title={`Only stories about ${label('entity', e)}`}>
                {label('entity', e)}
              </button>
            ))}
            {topics.map((t) => (
              <button key={t} type="button" className="fr-chip" onClick={() => onFilter?.('topic', t)} title={`Only ${label('topic', t)}`}>
                {label('topic', t)}
              </button>
            ))}
          </div>
          <div className="fr-actions">
            {onFeedback && (
              <>
                <button type="button" className={`fr-act${verdict === 'relevant' ? ' is-on' : ''}`} onClick={() => give('relevant')} title="More like this" aria-label="More like this">
                  <ThumbsUp size={16} />
                </button>
              </>
            )}
            <button type="button" className={`fr-act${item.is_read ? '' : ' is-strong'}`} onClick={() => onToggleRead(item)} title={item.is_read ? 'Mark unread (m)' : 'Mark read (m)'} aria-label={item.is_read ? 'Mark unread' : 'Mark read'}>
              <Check size={16} />
            </button>
            <button type="button" className={`fr-act${item.is_saved ? ' is-on' : ''}`} onClick={() => onToggleSaved(item)} aria-pressed={item.is_saved} title={item.is_saved ? 'Saved (s)' : 'Save (s)'} aria-label={item.is_saved ? 'Remove from saved' : 'Save'}>
              {item.is_saved ? <BookmarkCheck size={16} /> : <Bookmark size={16} />}
            </button>
            <a className="fr-act" href={item.url} target="_blank" rel="noopener noreferrer" onClick={() => onOpen(item)} title="Open at the source (o)" aria-label="Open at the source">
              <ExternalLink size={16} />
            </a>
          </div>
        </div>

        {expanded && (
          <div className="fr-details">
            {item.ai && (
              <section>
                <h4>
                  <Bot size={13} /> AI summary
                </h4>
                <p>{item.ai.summary}</p>
              </section>
            )}
            {reasons.length > 0 && (
              <section>
                <h4>Why it’s in your feed</h4>
                <ul className="fr-why">
                  {reasons.map((r) => (
                    <li key={r.label}>
                      <span className="fr-why-kind">{KIND_LABEL[r.kind] ?? r.kind}</span>
                      <span className="fr-why-label">{r.label.replace(/^(Entity|Topic|Event|Keyword|Source|Region|Pinned): /, '')}</span>
                      {r.weight !== null && (
                        <span className="fr-why-bar" title={`Weight ${r.weight} of 10`}>
                          <span style={{ width: `${r.weight * 10}%` }} />
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {notes.length > 0 && <p className="fr-notes">{notes.map((n) => n.label).join(' · ')}</p>}
              </section>
            )}
            {onFeedback && (
              <section className="fr-feedback">
                <h4>Tune your feed</h4>
                <div className="fr-feedback-buttons">
                  <button type="button" className={`btn btn-secondary btn-sm${verdict === 'relevant' ? ' is-on' : ''}`} onClick={() => give('relevant')}>
                    <ThumbsUp size={15} /> More like this
                  </button>
                  <button type="button" className={`btn btn-secondary btn-sm${verdict === 'not_relevant' ? ' is-on' : ''}`} onClick={() => give('not_relevant')}>
                    <ThumbsDown size={15} /> Less like this
                  </button>
                </div>
              </section>
            )}
            {item.articles.length > 0 && (
              <section>
                <h4>
                  Coverage · {item.source_count} {item.source_count === 1 ? 'outlet' : 'outlets'}
                </h4>
                <ol className="fr-coverage">
                  {item.articles.map((a) => (
                    <li key={a.url}>
                      <a href={a.url} target="_blank" rel="noopener noreferrer" onClick={() => onOpen(item)}>
                        {a.title}
                      </a>
                      <span>
                        {a.source} · {relativeTime(a.published_at)}
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </div>
        )}
      </div>
    </li>
  )
})
