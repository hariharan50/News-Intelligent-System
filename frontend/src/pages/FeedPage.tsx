import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUp, Clock, Inbox, Search, SlidersHorizontal, Sun, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import * as endpoints from '../api/endpoints'
import type { FeedQuery } from '../api/endpoints'
import type { FeedItem, Tier } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { EmptyState } from '../components/Feedback'
import { FeedbackNotice, type FeedbackNoticeState } from '../components/FeedbackNotice'
import { RichAnswer, Sources } from '../components/RichAnswer'
import { FeedRail } from '../components/feed/FeedRail'
import { StoryList, StoryListSkeleton } from '../components/feed/StoryList'
import '../styles/feed.css'
import { useReference } from '../hooks/useReference'
import { useStoryActions } from '../hooks/useStoryActions'
import { hourIst, todayIst } from '../lib/time'
import { useLive } from '../live/LiveContext'
import '../styles/ask.css'
import { useFeatures } from '../hooks/useFeatures'

const PAGE_SIZE = 25
const TIER_PILLS: { value: Tier | null; label: string }[] = [
  { value: null, label: 'All' },
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'normal', label: 'Normal' },
]

function useDebounced<T>(value: T, ms: number) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return debounced
}

/** How many stories of one tier the current view holds (for the stat cards). */
function useTierCount(base: FeedQuery, tier: Tier) {
  const query = { ...base, tier, limit: 1, unread_only: undefined }
  return useQuery({
    queryKey: ['feed', 'count', query],
    queryFn: ({ signal }) => endpoints.feed(query, signal),
    enabled: Boolean(base.profile_id),
    select: (page) => page.total,
  })
}

function greeting() {
  const hour = new Date().getHours()
  return hour < 5 ? 'Working late' : hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
}

/** Today's pre-market brief (Pro+), collapsed after the morning. */
function BriefCard() {
  const brief = useQuery({ queryKey: ['brief'], queryFn: endpoints.latestBrief, staleTime: 10 * 60_000 })
  const b = brief.data
  if (!b || b.date !== todayIst()) return null
  const morning = hourIst() < 12
  return (
    <details className="card brief-card" open={morning}>
      <summary>
        <Sun size={18} className="status-ok" />
        <h2>Pre-market brief</h2>
        <span className="muted small">from {b.story_count} stories · {new Date(b.created_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</span>
      </summary>
      <RichAnswer text={b.content} citations={b.citations} />
      <Sources citations={b.citations} />
    </details>
  )
}

export function FeedPage() {
  const features = useFeatures()
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const live = useLive()
  const actions = useStoryActions()
  const { user } = useAuth()
  const { label } = useReference()

  const profilesQuery = useQuery({ queryKey: ['profiles'], queryFn: endpoints.profiles })
  const profileId = params.get('profile') ?? profilesQuery.data?.[0]?.id

  const [search, setSearch] = useState(params.get('q') ?? '')
  const searchRef = useRef<HTMLInputElement>(null)
  const [notice, setNotice] = useState<FeedbackNoticeState | null>(null)
  const q = useDebounced(search.trim(), 350)

  const base: FeedQuery = {
    profile_id: profileId,
    sort: params.get('sort') === 'top' ? 'top' : 'latest', // newest first unless Top is picked
    since_hours: Number(params.get('hours') ?? 24),
    topic: params.get('topic') ?? undefined,
    entity: params.get('entity') ?? undefined,
    outlet: params.get('outlet') ?? undefined,
    unread_only: params.get('unread') === '1' || undefined,
    include_routine: params.get('routine') === '0' ? false : undefined,
    q: q.length >= 2 ? q : undefined,
  }
  const tier = (params.get('tier') as Tier | null) ?? undefined
  const query: FeedQuery = { ...base, tier, limit: PAGE_SIZE }

  const feed = useInfiniteQuery({
    queryKey: ['feed', query],
    queryFn: ({ pageParam, signal }) => endpoints.feed({ ...query, offset: pageParam }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    enabled: Boolean(profileId),
  })

  const criticalCount = useTierCount(base, 'critical')
  const highCount = useTierCount(base, 'high')
  const normalCount = useTierCount(base, 'normal')

  const set = (key: string, value: string | null) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        if (value === null || value === '') next.delete(key)
        else next.set(key, value)
        return next
      },
      { replace: true },
    )

  const first = feed.data?.pages[0]
  const items = feed.data?.pages.flatMap((p) => p.items) ?? []
  const loaded = new Set(items.map((i) => i.story_id))
  const fresh = profileId ? live.pending(profileId).filter((id) => !loaded.has(id)) : []
  const filtersActive = Boolean(tier || base.topic || base.entity || base.outlet || base.unread_only || base.include_routine === false || base.q)

  const showNew = async () => {
    if (!profileId) return
    live.clearPending(profileId)
    window.scrollTo({ top: 0, behavior: 'smooth' })
    await feed.refetch()
  }

  const markAll = async () => {
    await endpoints.markAllRead(profileId, base.since_hours ?? 24)
    await queryClient.invalidateQueries({ queryKey: ['feed'] })
  }

  const giveFeedback = async (item: FeedItem, verdict: 'relevant' | 'not_relevant') => {
    if (!profileId) return
    const result = await endpoints.sendFeedback(item.story_id, profileId, verdict)
    setNotice({ id: Date.now(), profileId, result })
    // Weights changed, so scores did too: refresh the stat cards and the list.
    if (result.adjustments.length) void queryClient.invalidateQueries({ queryKey: ['feed'] })
  }

  const clearFilters = () => {
    setSearch('')
    setParams(profileId && params.get('profile') ? { profile: profileId } : {}, { replace: true })
  }

  if (profilesQuery.data && profilesQuery.data.length === 0) {
    return (
      <div className="page">
        <EmptyState
          icon={<SlidersHorizontal size={24} />}
          title="Create your first profile"
          action={
            <Link to="/profiles" className="btn btn-primary">
              Set up a profile
            </Link>
          }
        >
          Tell us what you trade and we’ll rank the news around it.
        </EmptyState>
      </div>
    )
  }

  const name = user?.full_name?.split(' ')[0] || user?.email?.split('@')[0]
  const profiles = profilesQuery.data ?? []

  return (
    <div className="feed-layout">
        <header className="feed-header">
          <div className="feed-heading">
            <p className="feed-greeting">
              {greeting()}
              {name ? `, ${name}` : ''}
            </p>
            <h1>Your feed</h1>
          </div>
          <div className="feed-controls">
            <div className="segmented" role="group" aria-label="Sort">
              {(['latest', 'top'] as const).map((s) => (
                <button key={s} type="button" aria-pressed={base.sort === s} onClick={() => set('sort', s === 'latest' ? null : s)}>
                  {s === 'top' ? 'Top' : 'Latest'}
                </button>
              ))}
            </div>
            <label className="search-box feed-search">
              <Search size={16} />
              <input
                ref={searchRef}
                type="search"
                placeholder="Search headlines"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  set('q', e.target.value.trim() || null)
                }}
                aria-label="Search headlines"
              />
            </label>
            {profiles.length > 0 && (
              <select className="feed-profile" aria-label="Profile" value={profileId ?? ''} onChange={(e) => set('profile', e.target.value)} disabled={profiles.length < 2}>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </header>

        <div className="feed-filters">
          {TIER_PILLS.map((p) => (
            <button
              key={p.label}
              type="button"
              className={`pill-toggle${p.value ? ` pill-${p.value}` : ''}`}
              aria-pressed={(tier ?? null) === p.value}
              onClick={() => set('tier', p.value)}
            >
              {p.value && <span className="tier-dot" />}
              {p.label}
            </button>
          ))}
          <button type="button" className="pill-toggle" aria-pressed={Boolean(base.unread_only)} onClick={() => set('unread', base.unread_only ? null : '1')}>
            Unread only
          </button>
          {base.topic && (
            <button type="button" className="pill-toggle pill-removable" aria-pressed onClick={() => set('topic', null)}>
              {label('topic', base.topic)} <X size={13} />
            </button>
          )}
          {base.entity && (
            <button type="button" className="pill-toggle pill-removable" aria-pressed onClick={() => set('entity', null)}>
              {label('entity', base.entity)} <X size={13} />
            </button>
          )}
          {base.outlet && (
            <button type="button" className="pill-toggle pill-removable" aria-pressed onClick={() => set('outlet', null)}>
              {base.outlet} <X size={13} />
            </button>
          )}
          {filtersActive && (
            <button type="button" className="clear-filters" onClick={clearFilters}>
              Clear all
            </button>
          )}
          {first && (
            <span className="feed-threshold">
              showing scores {first.profile.show_threshold}+ · <Link to={`/profiles/${first.profile.id}`}>tune</Link>
            </span>
          )}
        </div>

        <div className="feed-body">
        {first && first.delay_minutes > 0 && (
          <p className="upsell feed-banner">
            <Clock size={14} /> Free plan: stories appear here {first.delay_minutes} minutes after they’re published. Alerts still reach you in real time.{' '}
            <Link to="/billing">Get the real-time feed</Link>
          </p>
        )}

        {features.ai && (
          <div className="feed-banner">
            <BriefCard />
          </div>
        )}

        {fresh.length > 0 && (
          <button type="button" className="new-banner" onClick={showNew}>
            <ArrowUp size={16} /> {fresh.length} new {fresh.length === 1 ? 'story' : 'stories'}
          </button>
        )}

        {feed.isPending ? (
          <StoryListSkeleton />
        ) : feed.isError ? (
          <div className="feed-empty">
            <EmptyState icon={<X size={24} />} title="Couldn’t load your feed">
              {feed.error.message}
            </EmptyState>
          </div>
        ) : items.length === 0 ? (
          <div className="feed-empty">
            <EmptyState
              icon={<Inbox size={24} />}
              title={filtersActive ? 'Nothing matches these filters' : 'All quiet for now'}
              action={
                filtersActive ? (
                  <button type="button" className="btn btn-secondary" onClick={clearFilters}>
                    Clear filters
                  </button>
                ) : (
                  <button type="button" className="btn btn-secondary" onClick={() => set('hours', '168')}>
                    Show the last 7 days
                  </button>
                )
              }
            >
              {filtersActive
                ? 'Try removing a filter or widening the time window.'
                : 'Nothing above your threshold yet. New stories appear here the moment they’re scored.'}
            </EmptyState>
          </div>
        ) : (
          <StoryList
            items={items}
            onMarkOpened={actions.markOpened}
            onToggleRead={actions.toggleRead}
            onToggleSaved={actions.toggleSaved}
            onFilter={(kind, slug) => set(kind, slug)}
            onFeedback={giveFeedback}
            searchRef={searchRef}
            footer={
              feed.hasNextPage ? (
                <button type="button" className="feed-more" onClick={() => feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
                  {feed.isFetchingNextPage ? 'Loading…' : 'Load more stories'}
                </button>
              ) : (
                items.length > 5 && <p className="feed-end">You’re all caught up.</p>
              )
            }
          />
        )}
      </div>

      <FeedRail
        counts={{ critical: criticalCount.data, high: highCount.data, normal: normalCount.data }}
        total={first?.total}
        unread={first?.unread}
        tier={tier}
        unreadOnly={Boolean(base.unread_only)}
        hideRoutine={base.include_routine === false}
        hours={base.since_hours ?? 24}
        items={items}
        activeOutlet={base.outlet}
        onTier={(t) => set('tier', t)}
        onShow={(mode) => {
          if (mode === 'all') {
            setParams(
              (p) => {
                const next = new URLSearchParams(p)
                next.delete('unread')
                next.delete('routine')
                return next
              },
              { replace: true },
            )
          } else if (mode === 'unread') set('unread', base.unread_only ? null : '1')
          else set('routine', base.include_routine === false ? null : '0')
        }}
        onHours={(h) => set('hours', h === 24 ? null : String(h))}
        onOutlet={(name) => set('outlet', name)}
        onMarkAll={markAll}
      />
      {notice && <FeedbackNotice key={notice.id} notice={notice} onClose={() => setNotice(null)} />}
    </div>
  )
}
