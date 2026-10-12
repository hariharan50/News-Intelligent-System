import { useQuery } from '@tanstack/react-query'
import { Activity, Bell, BellRing, Bookmark, Bot, ChevronsUpDown, CreditCard, LogOut, MailWarning, Menu, MonitorSmartphone, Newspaper, ShieldCheck, SlidersHorizontal, UserRound, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router'
import * as endpoints from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { useLive } from '../live/LiveContext'
import '../styles/app.css'
import '../styles/billing.css'
import { daysUntil } from '../lib/plans'
import { Logo } from './Logo'
import { ThemeToggle } from './ThemeToggle'
import { Toasts } from './Toasts'
import { RefreshButton } from './RefreshButton'
import { TopBar } from './TopBar'
import '../styles/topbar.css'
import { useFeatures } from '../hooks/useFeatures'

const LIVE_LABEL = { live: 'Live', connecting: 'Connecting', offline: 'Offline' } as const

function initials(name: string | null | undefined, email: string | undefined) {
  const source = (name || email || '?').trim()
  const parts = source.split(/[\s@._-]+/).filter(Boolean)
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase()
}

/** Current plan at a glance, with the next step: trial days left or an upgrade. */
function PlanPill() {
  const billing = useQuery({ queryKey: ['billing'], queryFn: endpoints.billing, staleTime: 5 * 60_000 })
  const b = billing.data
  if (!b) return null
  const { current, subscription: sub } = b
  let detail: string
  let cta: string | null = null
  if (current.source === 'trial') {
    const left = daysUntil(current.trial_ends_at)
    detail = `trial · ${left} day${left === 1 ? '' : 's'} left`
    cta = 'Choose plan'
  } else if (current.source === 'free') {
    detail = 'plan'
    cta = 'Upgrade'
  } else {
    detail = sub?.in_grace ? 'payment issue' : sub?.cancel_at_period_end ? 'ends soon' : 'plan'
  }
  return (
    <Link to="/billing" className="plan-pill">
      <span>
        <strong>{current.plan_name}</strong> {detail}
      </span>
      {cta && <span className="plan-pill-cta">{cta}</span>}
    </Link>
  )
}

function AccountMenu() {
  const { user, signOut } = useAuth()
  const features = useFeatures()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  return (
    <div className="account" ref={ref}>
      <button type="button" className="account-button" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="avatar">{initials(user?.full_name, user?.email)}</span>
        <span className="account-text">
          <strong>{user?.full_name || user?.email?.split('@')[0]}</strong>
          <span>{user?.email}</span>
        </span>
        <ChevronsUpDown size={15} className="muted" />
      </button>
      {open && (
        <div className="account-menu" role="menu">
          <Link to="/account" role="menuitem" onClick={() => setOpen(false)}>
            <UserRound size={16} /> My account
          </Link>
          {features.billing && (
            <Link to="/billing" role="menuitem" onClick={() => setOpen(false)}>
              <CreditCard size={16} /> Plan & billing
            </Link>
          )}
          <Link to="/settings/notifications" role="menuitem" onClick={() => setOpen(false)}>
            <Bell size={16} /> Notifications
          </Link>
          <Link to="/settings/devices" role="menuitem" onClick={() => setOpen(false)}>
            <MonitorSmartphone size={16} /> Signed-in devices
          </Link>
          <Link to="/system" role="menuitem" onClick={() => setOpen(false)}>
            <Activity size={16} /> System status
          </Link>
          <hr />
          <button
            type="button"
            role="menuitem"
            className="menu-danger"
            onClick={async () => {
              setOpen(false)
              await signOut()
              navigate('/', { replace: true })
            }}
          >
            <LogOut size={16} /> Sign out
          </button>
        </div>
      )}
    </div>
  )
}

/** Until the email is confirmed: say so, and offer a fresh link. */
const DISMISS_KEY = 'nis-verify-banner-dismissed'

function wasDismissed(userId: string) {
  try {
    return localStorage.getItem(DISMISS_KEY) === userId
  } catch {
    return false
  }
}

function VerifyEmailBanner() {
  const { user } = useAuth()
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle')
  const [error, setError] = useState('')
  const [dismissed, setDismissed] = useState(false)
  if (!user || user.email_verified || dismissed || wasDismissed(user.id)) return null
  // Hidden on this browser for this account; My account still shows "not verified".
  const dismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, user.id)
    } catch {
      // private mode: hide for now anyway
    }
    setDismissed(true)
  }
  const resend = async () => {
    setState('sending')
    try {
      await endpoints.resendVerification()
      setState('sent')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not send.')
      setState('error')
    }
  }
  return (
    <div className="verify-banner" role="status">
      <MailWarning size={16} />
      <span>
        Confirm your email: we sent a link to <strong>{user.email}</strong>. You need it to recover your account.
      </span>
      {state === 'sent' ? (
        <span className="muted">Sent. Check your inbox.</span>
      ) : state === 'error' ? (
        <span className="status-error">{error}</span>
      ) : (
        <button type="button" className="btn btn-ghost btn-sm" onClick={resend} disabled={state === 'sending'}>
          {state === 'sending' ? 'Sending…' : 'Resend email'}
        </button>
      )}
      <button type="button" className="icon-btn verify-banner-close" onClick={dismiss} aria-label="Dismiss" title="Dismiss">
        <X size={16} />
      </button>
    </div>
  )
}

export function Layout() {
  const { status } = useLive()
  const features = useFeatures()
  const location = useLocation()
  const [params] = useSearchParams()
  // The mobile drawer belongs to the page it was opened on: navigating closes it.
  const [drawerKey, setDrawerKey] = useState<string | null>(null)
  const drawerOpen = drawerKey === location.key
  const setDrawerOpen = (open: boolean) => setDrawerKey(open ? location.key : null)
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: endpoints.profiles })
  const { user } = useAuth()
  const unreadQuery = useQuery({ queryKey: ['alerts', 'unread'], queryFn: endpoints.unreadAlerts, refetchInterval: 120_000 })
  const unread = unreadQuery.data?.unread ?? 0

  const activeProfile = location.pathname === '/feed' ? (params.get('profile') ?? profiles.data?.[0]?.id) : undefined

  return (
    <div className="shell">
      <aside className={`sidebar${drawerOpen ? ' is-open' : ''}`} aria-label="Main navigation">
        <div className="sidebar-head">
          <Link to="/feed" className="sidebar-logo">
            <Logo size={28} />
          </Link>
          <button type="button" className="icon-btn sidebar-close" onClick={() => setDrawerOpen(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        <nav className="side-nav">
          <NavLink to="/feed" end>
            <Newspaper size={18} /> Feed
          </NavLink>
          <NavLink to="/alerts">
            <BellRing size={18} /> Alerts
            {unread > 0 && <span className="nav-badge tabular">{unread > 99 ? '99+' : unread}</span>}
          </NavLink>
          {features.ai && (
            <NavLink to="/ask">
              <Bot size={18} /> Ask
            </NavLink>
          )}
          <NavLink to="/saved">
            <Bookmark size={18} /> Saved
          </NavLink>
          <NavLink to="/profiles" end={false}>
            <SlidersHorizontal size={18} /> Profiles
          </NavLink>
          {user?.is_admin && (
            <NavLink to="/admin">
              <ShieldCheck size={18} /> Admin
            </NavLink>
          )}
        </nav>

        {profiles.data && profiles.data.length > 0 && (
          <div className="side-section">
            <span className="side-label">Your feeds</span>
            {profiles.data.map((p) => (
              <Link
                key={p.id}
                to={`/feed?profile=${p.id}`}
                className={`side-profile${activeProfile === p.id ? ' is-active' : ''}`}
              >
                <span className="side-profile-dot" />
                <span className="side-profile-name">{p.name}</span>
              </Link>
            ))}
          </div>
        )}

        <div className="sidebar-foot">
          <div className="sidebar-status">
            <span className={`live-pill live-${status}`}>
              <span className="live-dot" /> {LIVE_LABEL[status]}
            </span>
            <ThemeToggle />
          </div>
          {features.billing && <PlanPill />}
          <AccountMenu />
        </div>
      </aside>
      {drawerOpen && <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)} />}

      <div className="shell-main">
        <header className="mobile-bar">
          <button type="button" className="icon-btn" onClick={() => setDrawerOpen(true)} aria-label="Open menu">
            <Menu size={20} />
          </button>
          <Link to="/feed">
            <Logo size={24} />
          </Link>
          <span className={`live-pill live-${status} compact`} title={LIVE_LABEL[status]}>
            <span className="live-dot" />
          </span>
          <RefreshButton compact />
        </header>
        <TopBar />
        <main className="content">
          <VerifyEmailBanner />
          <Outlet />
        </main>
      </div>
      <Toasts />
    </div>
  )
}
