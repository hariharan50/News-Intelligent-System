import type { ReactNode } from 'react'
import { fetchReadiness } from '../api/health'
import { fetchLatestArticles, fetchSources, type DevSource } from '../api/dev'
import { Stories } from '../components/Stories'
import { usePolling } from '../hooks/usePolling'
import { formatIst, relativeTime } from '../lib/time'

type Tone = 'ok' | 'warn' | 'error' | 'muted'

const loadLatest = (signal: AbortSignal) => fetchLatestArticles(30, signal)

function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>
}

function StatusRow({ label, value, tone }: { label: string; value: string; tone: Tone }) {
  return (
    <li className="row">
      <span>{label}</span>
      <Pill tone={tone}>{value}</Pill>
    </li>
  )
}

function SystemStatus() {
  const { data: ready, error } = usePolling(fetchReadiness, 10_000)
  if (error && !ready) return <Pill tone="error">API unreachable</Pill>
  if (!ready) return <p className="muted">Checking…</p>

  const p = ready.pipeline
  const pipelineTone: Tone = p.status === 'ok' ? 'ok' : p.status === 'stale' ? 'warn' : 'error'
  const pipelineLabel =
    p.status === 'ok'
      ? `ok · ${Math.round(p.last_heartbeat_age_seconds ?? 0)}s ago`
      : p.status === 'missing'
        ? 'waiting for first heartbeat'
        : p.status

  return (
    <ul className="list">
      <StatusRow label="API" value="ok" tone="ok" />
      <StatusRow label="Database" value={ready.database} tone={ready.database === 'ok' ? 'ok' : 'error'} />
      <StatusRow label="Redis" value={ready.redis} tone={ready.redis === 'ok' ? 'ok' : 'error'} />
      <StatusRow label="Pipeline (scheduler → worker)" value={pipelineLabel} tone={pipelineTone} />
    </ul>
  )
}

const SOURCE_TONE: Record<DevSource['status'], Tone> = {
  healthy: 'ok',
  degraded: 'warn',
  failing: 'error',
  unknown: 'muted',
}

function Sources() {
  const { data: sources, error } = usePolling(fetchSources, 15_000)
  if (error && !sources) return <Pill tone="error">Could not load sources</Pill>
  if (!sources) return <p className="muted">Loading…</p>

  return (
    <ul className="list">
      {sources.map((s) => (
        <li key={s.slug} className={`row source${s.enabled ? '' : ' disabled'}`}>
          <div className="source-main">
            <span className="source-name">
              {s.name}
              {s.is_priority && <span className="tag">priority</span>}
            </span>
            <span className="muted small">
              {s.enabled
                ? `every ${s.poll_interval_seconds}s · polled ${relativeTime(s.last_polled_at)} · last new item ${relativeTime(s.last_new_item_at)}`
                : (s.notes ?? 'disabled')}
            </span>
            {s.enabled && s.last_error && <span className="error-text small">{s.last_error}</span>}
          </div>
          {s.enabled ? <Pill tone={SOURCE_TONE[s.status]}>{s.status}</Pill> : <Pill tone="muted">disabled</Pill>}
        </li>
      ))}
    </ul>
  )
}

function LatestHeadlines() {
  const { data: articles, error } = usePolling(loadLatest, 15_000)
  if (error && !articles) return <Pill tone="error">Could not load headlines</Pill>
  if (!articles) return <p className="muted">Loading…</p>
  if (articles.length === 0) return <p className="muted">No articles yet. The first polls run within a minute.</p>

  return (
    <ul className="list">
      {articles.map((a) => (
        <li key={a.id} className="headline">
          <a href={a.url} target="_blank" rel="noopener noreferrer">
            {a.title}
          </a>
          <span className="muted small">
            {a.source} · {formatIst(a.published_at)}
            {a.published_at_estimated && ' (time estimated)'}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function SystemPage() {
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>System status</h1>
          <p className="muted">Pipeline health, sources, and unranked stories (development view).</p>
        </div>
      </div>

      <section className="card" aria-labelledby="status-heading">
        <h2 id="status-heading">System status</h2>
        <SystemStatus />
      </section>

      <section className="card" aria-labelledby="sources-heading">
        <h2 id="sources-heading">Sources</h2>
        <Sources />
      </section>

      <section className="card" aria-labelledby="stories-heading">
        <h2 id="stories-heading">Latest stories (grouped and tagged, not yet ranked)</h2>
        <Stories />
      </section>

      <section className="card" aria-labelledby="latest-heading">
        <h2 id="latest-heading">Raw headlines</h2>
        <LatestHeadlines />
      </section>

    </div>
  )
}
