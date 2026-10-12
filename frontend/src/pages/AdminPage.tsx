import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, Bot, ScrollText, ShieldAlert, CheckCircle2, FlaskConical, Plus, RefreshCw, Rss, Search, Send, Users } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import type { AdminSource, FeedTestResult } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { relativeTime } from '../lib/time'
import '../styles/ask.css'
import { formatInr } from '../lib/plans'
import { useFeatures } from '../hooks/useFeatures'

type Tab = 'overview' | 'sources' | 'users' | 'ai' | 'audit'

const STATUS_TONE: Record<AdminSource['status'], string> = {
  healthy: 'pill-ok',
  degraded: 'pill-warn',
  failing: 'pill-error',
  unknown: 'pill-muted',
}

function Stat({ label, value, hint, tone }: { label: string; value: string | number; hint?: string; tone?: string }) {
  return (
    <div className={`stat-card ${tone ?? ''}`}>
      <span className="stat-label">{label}</span>
      <strong className="tabular">{value}</strong>
      {hint && <span className="stat-hint">{hint}</span>}
    </div>
  )
}

const usd = (n: number) => `$${n.toFixed(n > 0 && n < 0.1 ? 3 : 2)}`

function AiTab() {
  const queryClient = useQueryClient()
  const ai = useQuery({ queryKey: ['admin', 'ai'], queryFn: endpoints.adminAi, refetchInterval: 15_000 })
  const toggle = useMutation({
    mutationFn: endpoints.adminSetAi,
    onSuccess: (data) => {
      queryClient.setQueryData(['admin', 'ai'], data)
      queryClient.invalidateQueries({ queryKey: ['admin', 'overview'] })
    },
  })
  const a = ai.data
  if (!a) return <div className="skeleton" style={{ height: 200, borderRadius: 16 }} />
  const used = Math.min(100, a.budget_usd > 0 ? (a.spent_today_usd / a.budget_usd) * 100 : 100)
  const written = a.summaries_24h.ok ?? 0
  const problems = (a.summaries_24h.failed ?? 0) + (a.summaries_24h.refused ?? 0)

  return (
    <>
      {!a.configured && (
        <p className="draft-banner" style={{ marginBottom: 16 }}>
          No Claude API key yet. Add <code>ANTHROPIC_API_KEY</code> to <code>.env</code> and restart the worker. Until then stories simply show no AI summary.
        </p>
      )}
      <section className="card">
        <div className="card-title-row">
          <div className="setting-head" style={{ marginBottom: 0 }}>
            <span className="row-icon">
              <Bot size={18} />
            </span>
            <div>
              <h2>AI summaries</h2>
              <p className="card-sub">
                {a.model} · effort {a.effort} · stories scoring {a.min_score}+ for any profile. Switching off stops new AI work at once; scores and alerts never depend on it.
              </p>
            </div>
          </div>
          <label className="switch-row">
            <input type="checkbox" checked={a.enabled} disabled={toggle.isPending} onChange={(e) => toggle.mutate(e.target.checked)} />
            <span>{a.enabled ? 'On' : 'Off'}</span>
          </label>
        </div>
        <div className="budget">
          <div className="budget-head">
            <span>
              Today (India time): <strong className="tabular">{usd(a.spent_today_usd)}</strong> of {usd(a.budget_usd)}
            </span>
            {a.over_budget && <span className="pill pill-error">Budget reached, paused until midnight IST</span>}
          </div>
          <div className="budget-bar" role="progressbar" aria-valuenow={Math.round(used)} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${used}%` }} className={used >= 100 ? 'is-over' : used >= 80 ? 'is-near' : ''} />
          </div>
          <p className="muted small">
            Last 24 h: {written} {written === 1 ? 'summary' : 'summaries'} written{problems > 0 ? ` · ${problems} failed or declined` : ''}. Cost is estimated from token counts at list prices.
          </p>
        </div>
      </section>

      <div className="card table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Day</th>
              <th className="num">Requests</th>
              <th className="num">Failed</th>
              <th className="num">Input tokens</th>
              <th className="num">Output tokens</th>
              <th className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {a.days.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No AI usage yet.
                </td>
              </tr>
            )}
            {a.days.map((d) => (
              <tr key={d.day}>
                <td>{new Date(d.day).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</td>
                <td className="num tabular">{d.requests}</td>
                <td className="num tabular">{d.failures}</td>
                <td className="num tabular">{d.input_tokens.toLocaleString()}</td>
                <td className="num tabular">{d.output_tokens.toLocaleString()}</td>
                <td className="num tabular">{usd(d.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {a.recent_errors.length > 0 && (
        <section className="card">
          <h2>Recent problems</h2>
          <ul className="coverage-list">
            {a.recent_errors.map((e) => (
              <li key={`${e.title}-${e.at}`}>
                <span>{e.title}</span>
                <span className="muted small">
                  {e.error} · {relativeTime(e.at)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function Overview() {
  const features = useFeatures()
  const overview = useQuery({ queryKey: ['admin', 'overview'], queryFn: endpoints.adminOverview, refetchInterval: 15_000 })
  const o = overview.data
  if (!o) return <div className="skeleton" style={{ height: 200, borderRadius: 16 }} />
  const heartbeatOk = o.pipeline_heartbeat_age_seconds !== null && o.pipeline_heartbeat_age_seconds < 150
  return (
    <>
      <div className="admin-stats">
        <Stat label="Pipeline" value={heartbeatOk ? 'Running' : 'Stalled'} hint={o.pipeline_heartbeat_age_seconds !== null ? `heartbeat ${Math.round(o.pipeline_heartbeat_age_seconds)}s ago` : 'no heartbeat'} tone={heartbeatOk ? 'stat-ok' : 'stat-critical'} />
        <Stat label="Articles 24h" value={o.news_24h.articles} hint={`${o.news_24h.stories} new stories`} />
        <Stat label="Alerts 24h" value={o.alerts_24h.total} hint={`${o.alerts_24h.telegram.sent ?? 0} sent to Telegram · ${o.alerts_24h.telegram.failed ?? 0} failed`} />
        <Stat label="Users" value={o.users.total} hint={`${o.users.active_7d} active this week · ${o.users.telegram_connected} on Telegram`} />
        <Stat
          label="Sources"
          value={o.sources.enabled}
          hint={`${o.sources.by_status.healthy ?? 0} healthy · ${(o.sources.by_status.degraded ?? 0) + (o.sources.by_status.failing ?? 0)} with problems · ${o.sources.disabled} off`}
          tone={(o.sources.by_status.failing ?? 0) > 0 ? 'stat-critical' : (o.sources.by_status.degraded ?? 0) > 0 ? 'stat-high' : 'stat-ok'}
        />
        <Stat label="Telegram bot" value={o.telegram_configured ? 'Configured' : 'Not set up'} hint={o.telegram_configured ? 'alerts are delivered' : 'add TELEGRAM_BOT_TOKEN to .env'} tone={o.telegram_configured ? 'stat-ok' : 'stat-high'} />
        {features.billing && (
          <Stat
            label="Paying users"
            value={o.billing.paying}
            hint={
              o.billing.configured
                ? `MRR ${formatInr(o.billing.mrr_paise)} · ${o.billing.trials} on trial · ${formatInr(o.billing.collected_30d_paise)} collected in 30 days`
                : 'add Razorpay keys to .env'
            }
            tone={o.billing.configured ? 'stat-ok' : 'stat-high'}
          />
        )}
        {features.ai && (
          <Stat
            label="AI summaries"
            value={!o.ai.configured ? 'Not set up' : o.ai.enabled ? 'On' : 'Off'}
            hint={o.ai.configured ? `${usd(o.ai.spent_today_usd)} of ${usd(o.ai.budget_usd)} today` : 'add ANTHROPIC_API_KEY to .env'}
            tone={!o.ai.configured || !o.ai.enabled ? 'stat-high' : o.ai.spent_today_usd >= o.ai.budget_usd ? 'stat-critical' : 'stat-ok'}
          />
        )}
      </div>
    </>
  )
}

function AddSource({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    name: '',
    url: '',
    homepage: '',
    category: 'press_india',
    region: 'IN',
    licence_class: 'headline_link',
    authority: 3,
    poll_interval_seconds: 300,
    is_priority: false,
  })
  const [test, setTest] = useState<FeedTestResult | null>(null)
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }))

  const runTest = useMutation({ mutationFn: () => endpoints.adminTestFeed(form.url, form.region), onSuccess: setTest })
  const create = useMutation({
    mutationFn: () => endpoints.adminCreateSource({ ...form, homepage: form.homepage || null }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin'] })
      onDone()
    },
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }

  return (
    <form className="card add-source" onSubmit={submit}>
      <h2>Add a news source</h2>
      <p className="card-sub">RSS or Atom feeds. Test it first: you’ll see exactly what we would collect. Check the publisher’s terms before enabling it.</p>
      <div className="form-grid">
        <label className="field span-2">
          <span>Feed URL</span>
          <input required type="url" placeholder="https://example.com/rss/markets.xml" value={form.url} onChange={(e) => { set('url', e.target.value); setTest(null) }} />
        </label>
        <label className="field">
          <span>Name</span>
          <input required minLength={2} placeholder="Example — Markets" value={form.name} onChange={(e) => set('name', e.target.value)} />
        </label>
        <label className="field">
          <span>Homepage (optional)</span>
          <input type="url" placeholder="https://example.com" value={form.homepage} onChange={(e) => set('homepage', e.target.value)} />
        </label>
        <label className="field">
          <span>Category</span>
          <select value={form.category} onChange={(e) => set('category', e.target.value)}>
            <option value="official_india">Official — India</option>
            <option value="official_global">Official — global</option>
            <option value="press_india">Press — India</option>
            <option value="press_global">Press — global</option>
            <option value="other">Other</option>
          </select>
        </label>
        <label className="field">
          <span>Region</span>
          <select value={form.region} onChange={(e) => set('region', e.target.value)}>
            {['IN', 'US', 'GB', 'EU', 'GLOBAL'].map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Licence</span>
          <select value={form.licence_class} onChange={(e) => set('licence_class', e.target.value)}>
            <option value="headline_link">Headline + link only</option>
            <option value="official">Official publication</option>
            <option value="licensed">Licensed</option>
          </select>
        </label>
        <label className="field">
          <span>Authority: {form.authority}</span>
          <input type="range" min={1} max={5} value={form.authority} onChange={(e) => set('authority', Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Check every</span>
          <select value={form.poll_interval_seconds} onChange={(e) => set('poll_interval_seconds', Number(e.target.value))}>
            {[30, 60, 120, 300, 600, 1800].map((s) => (
              <option key={s} value={s}>
                {s < 60 ? `${s} seconds` : `${s / 60} minute${s > 60 ? 's' : ''}`}
              </option>
            ))}
          </select>
        </label>
        <label className="switch-row">
          <input type="checkbox" checked={form.is_priority} onChange={(e) => set('is_priority', e.target.checked)} />
          Priority source
        </label>
      </div>

      {test && (
        <div className={`test-result ${test.ok ? 'ok' : 'bad'}`}>
          {test.ok ? (
            <>
              <strong>
                <CheckCircle2 size={16} /> Feed works: {test.item_count} items, {test.duration_ms} ms
              </strong>
              <ul>
                {test.items.map((i) => (
                  <li key={i.url}>
                    {i.title}
                    {i.published_at && <span className="muted small"> · {relativeTime(i.published_at)}</span>}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <strong>
              <AlertTriangle size={16} /> {test.error}
            </strong>
          )}
        </div>
      )}
      {(create.error || runTest.error) && <p className="auth-error">{((create.error ?? runTest.error) as ApiError).message}</p>}

      <div className="head-actions" style={{ marginTop: 16 }}>
        <button type="button" className="btn btn-secondary" disabled={!form.url || runTest.isPending} onClick={() => runTest.mutate()}>
          <FlaskConical size={16} /> {runTest.isPending ? 'Testing…' : 'Test feed'}
        </button>
        <button type="submit" className="btn btn-primary" disabled={create.isPending || !form.name || !form.url}>
          <Plus size={16} /> Add source
        </button>
        <button type="button" className="btn btn-ghost" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function Sources() {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const sources = useQuery({ queryKey: ['admin', 'sources'], queryFn: endpoints.adminSources, refetchInterval: 20_000 })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin'] })
  const update = useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: Partial<AdminSource> }) => endpoints.adminUpdateSource(slug, body),
    onSuccess: refresh,
  })
  const pollNow = useMutation({ mutationFn: endpoints.adminPollNow, onSuccess: refresh })

  return (
    <>
      {adding ? (
        <AddSource onDone={() => setAdding(false)} />
      ) : (
        <div className="head-actions" style={{ marginBottom: 14 }}>
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            <Plus size={16} /> Add source
          </button>
        </div>
      )}
      <div className="card table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Status</th>
              <th className="num">New 24h</th>
              <th className="num">Errors 24h</th>
              <th className="num">Avg</th>
              <th>Last polled</th>
              <th>On</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sources.data?.map((s) => (
              <tr key={s.slug} className={s.enabled ? '' : 'is-off'}>
                <td>
                  <div className="cell-main">
                    <strong>
                      {s.name}
                      {s.is_priority && <span className="tag">priority</span>}
                    </strong>
                    <span className="muted small">
                      every {s.poll_interval_seconds < 60 ? `${s.poll_interval_seconds}s` : `${Math.round(s.poll_interval_seconds / 60)}m`} · {new URL(s.url).host}
                    </span>
                    {s.enabled && s.last_error && <span className="error-text small">{s.last_error}</span>}
                    {!s.enabled && s.notes && <span className="muted small">{s.notes}</span>}
                  </div>
                </td>
                <td>{s.enabled ? <span className={`pill ${STATUS_TONE[s.status]}`}>{s.status}</span> : <span className="pill pill-muted">off</span>}</td>
                <td className="num tabular">{s.new_items_24h}</td>
                <td className={`num tabular${s.errors_24h ? ' error-text' : ''}`}>{s.errors_24h}</td>
                <td className="num tabular muted">{s.avg_ms_24h !== null ? `${s.avg_ms_24h}ms` : '—'}</td>
                <td className="muted small">{relativeTime(s.last_polled_at)}</td>
                <td>
                  <input
                    type="checkbox"
                    className="switch"
                    checked={s.enabled}
                    aria-label={`${s.enabled ? 'Disable' : 'Enable'} ${s.name}`}
                    onChange={(e) => update.mutate({ slug: s.slug, body: { enabled: e.target.checked } })}
                  />
                </td>
                <td>
                  <button type="button" className="icon-btn" title="Poll now" aria-label={`Poll ${s.name} now`} disabled={!s.enabled} onClick={() => pollNow.mutate(s.slug)}>
                    <RefreshCw size={16} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function UsersTab() {
  const queryClient = useQueryClient()
  const { user: me } = useAuth()
  const [q, setQ] = useState('')
  const users = useQuery({ queryKey: ['admin', 'users', q], queryFn: () => endpoints.adminUsers(q) })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { is_active?: boolean; is_admin?: boolean } }) => endpoints.adminUpdateUser(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  return (
    <>
      <label className="search-box" style={{ maxWidth: 360, marginBottom: 14 }}>
        <Search size={16} />
        <input type="search" placeholder="Search by email or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
      </label>
      {update.error && <p className="auth-error">{(update.error as ApiError).message}</p>}
      <div className="card table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Joined</th>
              <th>Last sign-in</th>
              <th>Plan</th>
              <th className="num">Profiles</th>
              <th className="num">Alerts 7d</th>
              <th>Telegram</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.data?.items.map((u) => (
              <tr key={u.id} className={u.is_active ? '' : 'is-off'}>
                <td>
                  <div className="cell-main">
                    <strong>
                      {u.full_name || u.email.split('@')[0]}
                      {u.is_admin && <span className="tag">admin</span>}
                      {!u.is_active && <span className="pill pill-error" style={{ marginLeft: 8 }}>disabled</span>}
                    </strong>
                    <span className="muted small">{u.email}</span>
                  </div>
                </td>
                <td className="muted small">{relativeTime(u.created_at)}</td>
                <td className="muted small">{relativeTime(u.last_login_at)}</td>
                <td>
                  <span className={`pill ${u.plan_source === 'subscription' ? 'pill-ok' : u.plan_source === 'trial' ? 'pill-warn' : 'pill-muted'}`}>
                    {u.plan === 'pro_plus' ? 'Pro+' : u.plan === 'pro' ? 'Pro' : 'Free'}
                    {u.plan_source === 'trial' ? ' trial' : ''}
                  </span>
                </td>
                <td className="num tabular">{u.profiles}</td>
                <td className="num tabular">{u.alerts_7d}</td>
                <td>{u.telegram_connected ? <Send size={15} className="status-ok" /> : <span className="muted">—</span>}</td>
                <td className="row-actions">
                  {u.id !== me?.id && (
                    <>
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => update.mutate({ id: u.id, body: { is_admin: !u.is_admin } })}>
                        {u.is_admin ? 'Remove admin' : 'Make admin'}
                      </button>
                      <button
                        type="button"
                        className={`btn btn-sm ${u.is_active ? 'btn-danger' : 'btn-secondary'}`}
                        onClick={() =>
                          (u.is_active ? window.confirm(`Disable ${u.email}? They are signed out everywhere immediately.`) : true) &&
                          update.mutate({ id: u.id, body: { is_active: !u.is_active } })
                        }
                      >
                        {u.is_active ? 'Disable' : 'Enable'}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

const ACTION_LABEL: Record<string, string> = {
  'user.update': 'Changed a user',
  'source.create': 'Added a source',
  'source.update': 'Changed a source',
  'source.poll_now': 'Polled a source now',
  'source.test': 'Tested a feed URL',
  'ai.switch': 'Switched AI',
}

function describe(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => {
      if (value && typeof value === 'object' && 'to' in value) {
        const change = value as { from: unknown; to: unknown }
        return `${key}: ${String(change.from)} → ${String(change.to)}`
      }
      return `${key}: ${String(value)}`
    })
    .join(' · ')
}

function AuditTab() {
  const [filter, setFilter] = useState('')
  const log = useQuery({ queryKey: ['admin', 'audit', filter], queryFn: () => endpoints.adminAudit(filter || undefined) })
  return (
    <>
      <div className="segmented" role="group" aria-label="Filter" style={{ marginBottom: 14 }}>
        {[
          ['', 'All'],
          ['user.', 'Users'],
          ['source.', 'Sources'],
          ['ai.', 'AI'],
        ].map(([value, label]) => (
          <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>
            {label}
          </button>
        ))}
      </div>
      <div className="card table-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>What</th>
              <th>Details</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            {log.data?.items.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  Nothing recorded yet.
                </td>
              </tr>
            )}
            {log.data?.items.map((e) => (
              <tr key={e.id}>
                <td className="muted small" title={new Date(e.created_at).toLocaleString()}>
                  {relativeTime(e.created_at)}
                </td>
                <td>{e.actor}</td>
                <td>
                  <strong>{ACTION_LABEL[e.action] ?? e.action}</strong>
                  {e.target_id && <span className="muted small"> · {e.target_type === 'user' ? (e.details.email as string) ?? e.target_id : e.target_id}</span>}
                </td>
                <td className="muted small">{describe(Object.fromEntries(Object.entries(e.details).filter(([k]) => k !== 'email')))}</td>
                <td className="muted small">{e.ip_address ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {log.data && log.data.total > log.data.items.length && <p className="muted small">Showing the latest {log.data.items.length} of {log.data.total}.</p>}
    </>
  )
}

const TABS: { id: Tab; label: string; icon: typeof Activity }[] = [
  { id: 'overview', label: 'Overview', icon: Activity },
  { id: 'sources', label: 'Sources', icon: Rss },
  { id: 'users', label: 'Users', icon: Users },
  { id: 'ai', label: 'AI', icon: Bot },
  { id: 'audit', label: 'Audit log', icon: ScrollText },
]

export function AdminPage() {
  const features = useFeatures()
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab | null) ?? 'overview'

  const probe = useQuery({ queryKey: ['admin', 'overview'], queryFn: endpoints.adminOverview, retry: false, enabled: Boolean(user?.is_admin) })
  if (user?.is_admin && probe.error instanceof ApiError && probe.error.code === 'admin_2fa_required') {
    return (
      <div className="page">
        <section className="card ask-locked">
          <ShieldAlert size={22} />
          <div>
            <h2>Turn on two-step sign-in first</h2>
            <p className="card-sub">The admin panel needs a code from an authenticator app as well as your password. It takes about a minute.</p>
          </div>
          <Link to="/account" className="btn btn-primary">
            Set it up in My account
          </Link>
        </section>
      </div>
    )
  }

  if (!user?.is_admin) {
    return (
      <div className="page">
        <p className="muted">Only administrators can see this page.</p>
      </div>
    )
  }

  return (
    <div className="page-wide">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Administration</p>
          <h1>Admin</h1>
        </div>
      </header>
      <div className="tabs" role="tablist">
        {TABS.filter((t) => t.id !== 'ai' || features.ai).map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className="tab" onClick={() => setParams(id === 'overview' ? {} : { tab: id })}>
            <Icon size={16} /> {label}
          </button>
        ))}
      </div>
      {tab === 'overview' && <Overview />}
      {tab === 'sources' && <Sources />}
      {tab === 'users' && <UsersTab />}
      {tab === 'ai' && features.ai && <AiTab />}
      {tab === 'audit' && <AuditTab />}
    </div>
  )
}
