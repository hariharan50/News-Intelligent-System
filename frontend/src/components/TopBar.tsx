// The slim bar across the top of the app (desktop): where you are, quick search, the
// Indian market's state, alerts and your account. Phones use the compact mobile bar instead.
import { useQuery } from '@tanstack/react-query'
import { Bell, BellRing, Bookmark, Bot, CreditCard, LayoutList, MonitorSmartphone, Search, ShieldCheck, SlidersHorizontal, UserRound } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import * as endpoints from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { marketStatus } from '../lib/market'
import { RefreshButton } from './RefreshButton'

const PAGES: { prefix: string; title: string; icon: ReactNode }[] = [
  { prefix: '/feed', title: 'Feed', icon: <LayoutList size={16} /> },
  { prefix: '/alerts', title: 'Alerts', icon: <BellRing size={16} /> },
  { prefix: '/saved', title: 'Saved', icon: <Bookmark size={16} /> },
  { prefix: '/ask', title: 'Ask the news', icon: <Bot size={16} /> },
  { prefix: '/profiles', title: 'Profiles', icon: <SlidersHorizontal size={16} /> },
  { prefix: '/account', title: 'My account', icon: <UserRound size={16} /> },
  { prefix: '/billing', title: 'Plan & billing', icon: <CreditCard size={16} /> },
  { prefix: '/settings/notifications', title: 'Notifications', icon: <Bell size={16} /> },
  { prefix: '/settings/devices', title: 'Signed-in devices', icon: <MonitorSmartphone size={16} /> },
  { prefix: '/admin', title: 'Admin', icon: <ShieldCheck size={16} /> },
  { prefix: '/system', title: 'System status', icon: <MonitorSmartphone size={16} /> },
]

function initials(name: string | null | undefined, email: string | undefined) {
  const parts = (name || email || '?').trim().split(/[\s@._-]+/).filter(Boolean)
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase()
}

/** India time and whether NSE is trading, refreshed every 30 seconds. */
function MarketClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000)
    return () => window.clearInterval(id)
  }, [])
  const m = marketStatus(now)
  return (
    <div className={`topbar-market${m.open ? ' is-open' : ''}`} title="NSE regular session, Monday to Friday 09:15–15:30 IST (exchange holidays not included)">
      <span className="topbar-market-dot" />
      <span className="topbar-market-state">{m.open ? 'NSE open' : 'NSE closed'}</span>
      <span className="topbar-market-detail">{m.detail}</span>
      <span className="topbar-clock tabular">{m.time} IST</span>
    </div>
  )
}

export function TopBar() {
  const location = useLocation()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { user } = useAuth()
  const unread = useQuery({ queryKey: ['alerts', 'unread'], queryFn: endpoints.unreadAlerts, refetchInterval: 120_000 }).data?.unread ?? 0
  const page = PAGES.find((p) => location.pathname.startsWith(p.prefix))
  const onFeed = location.pathname === '/feed'
  const [query, setQuery] = useState('')
  const input = useRef<HTMLInputElement>(null)

  // On the feed, the page has its own search box; the bar's search jumps there from elsewhere.
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    const next = new URLSearchParams(onFeed ? params : undefined)
    next.set('q', q)
    navigate(`/feed?${next.toString()}`)
    setQuery('')
    input.current?.blur()
  }

  return (
    <header className="topbar">
      <div className="topbar-page">
        {page?.icon}
        <span>{page?.title ?? 'News Intelligence'}</span>
      </div>

      {!onFeed && (
        <form className="topbar-search" onSubmit={submit} role="search">
          <Search size={15} />
          <input ref={input} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search your feed" aria-label="Search your feed" />
          <kbd>Enter</kbd>
        </form>
      )}

      <div className="topbar-right">
        <RefreshButton />
        <MarketClock />
        <Link to="/alerts" className="topbar-icon" aria-label={unread ? `Alerts, ${unread} unread` : 'Alerts'} title="Alerts">
          <Bell size={18} />
          {unread > 0 && <span className="topbar-badge tabular">{unread > 99 ? '99+' : unread}</span>}
        </Link>
        <Link to="/account" className="topbar-avatar" title={user?.email ?? 'My account'} aria-label="My account">
          {initials(user?.full_name, user?.email)}
        </Link>
      </div>
    </header>
  )
}
