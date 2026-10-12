import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellOff, ChevronLeft, Eye, Newspaper, Pin, Plus, RotateCcw, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useBlocker, useNavigate, useParams } from 'react-router'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import type { Profile, Rule, RuleKind, RuleMode } from '../api/types'
import { EmptyState } from '../components/Feedback'
import { ScoreRing } from '../components/TierBadge'
import { useReference } from '../hooks/useReference'
import { relativeTime } from '../lib/time'

// ase is the weight the user set; feedback learning may have moved weight from it.
type DraftRule = Omit<Rule, 'id' | 'base_weight'> & { key: string; base?: number | null }

interface Draft {
  name: string
  show_threshold: number
  alert_threshold: number
  rules: DraftRule[]
}

const KIND_LABEL: Record<RuleKind, string> = {
  entity: 'Company / institution / market',
  topic: 'Topic',
  event_type: 'Event type',
  keyword: 'Keyword',
  source: 'Source',
  region: 'Region',
}

const MUTE_DURATIONS = [
  { label: 'Until I unmute', days: 0 },
  { label: '1 day', days: 1 },
  { label: '1 week', days: 7 },
  { label: '30 days', days: 30 },
]

let keySeq = 0
const nextKey = () => `r${++keySeq}`

function toDraft(p: Profile): Draft {
  return {
    name: p.name,
    show_threshold: p.show_threshold,
    alert_threshold: p.alert_threshold,
    rules: p.rules.map((r) => ({
      key: nextKey(),
      kind: r.kind,
      value: r.value,
      mode: r.mode,
      weight: r.weight,
      entity: r.entity,
      min_score: r.min_score,
      expires_at: r.expires_at,
      base: r.base_weight ?? null,
    })),
  }
}

function toBody(d: Draft): endpoints.ProfileBody {
  return {
    name: d.name.trim(),
    show_threshold: d.show_threshold,
    alert_threshold: d.alert_threshold,
    rules: d.rules.map(({ key: _key, base: _base, ...rule }) => rule),
  }
}

function useDebounced<T>(value: T, ms: number) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return debounced
}

export function ProfileEditorPage() {
  const { profileId = '' } = useParams()
  const loaded = useQuery({ queryKey: ['profile', profileId], queryFn: () => endpoints.profile(profileId) })
  if (loaded.isPending)
    return (
      <div className="page-wide">
        <div className="skeleton" style={{ height: 32, width: 260, marginBottom: 24 }} />
        <div className="skeleton" style={{ height: 220, borderRadius: 16 }} />
      </div>
    )
  if (loaded.isError)
    return (
      <div className="page">
        <EmptyState icon={<X size={24} />} title="Couldn’t load this profile">
          It may have been deleted. <Link to="/profiles">Back to profiles</Link>
        </EmptyState>
      </div>
    )
  return <Editor key={`${loaded.data.id}:${loaded.data.version}`} profile={loaded.data} />
}

function Editor({ profile }: { profile: Profile }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { label, ready } = useReference()
  const [draft, setDraft] = useState<Draft>(() => toDraft(profile))
  const original = useMemo(() => JSON.stringify(toBody(toDraft(profile))), [profile])
  const dirty = JSON.stringify(toBody(draft)) !== original

  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (window.confirm('You have unsaved changes. Leave without saving?')) blocker.proceed()
      else blocker.reset()
    }
  }, [blocker])

  const save = useMutation({
    mutationFn: () => endpoints.saveProfile(profile.id, toBody(draft)),
    onSuccess: async (saved) => {
      queryClient.setQueryData(['profile', profile.id], saved)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['profiles'] }),
        queryClient.invalidateQueries({ queryKey: ['feed'] }),
      ])
    },
  })

  const remove = useMutation({
    mutationFn: () => endpoints.deleteProfile(profile.id),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: ['profile', profile.id] })
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
      navigate('/profiles', { replace: true })
    },
  })

  const learned = draft.rules.filter((r) => r.mode === 'include' && r.base != null && r.base !== r.weight).length
  const resetLearning = useMutation({
    mutationFn: () => endpoints.resetLearning(profile.id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['profile', profile.id] }),
        queryClient.invalidateQueries({ queryKey: ['feed'] }),
      ])
    },
  })

  const update = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }))
  const updateRule = (key: string, patch: Partial<DraftRule>) =>
    setDraft((d) => ({ ...d, rules: d.rules.map((r) => (r.key === key ? { ...r, ...patch } : r)) }))
  const removeRule = (key: string) => setDraft((d) => ({ ...d, rules: d.rules.filter((r) => r.key !== key) }))

  const pins = draft.rules.filter((r) => r.mode === 'pin')
  const follows = draft.rules.filter((r) => r.mode === 'include')
  const mutes = draft.rules.filter((r) => r.mode === 'exclude')
  const groups: [string, DraftRule[]][] = [
    ['Companies, institutions and markets', follows.filter((r) => r.kind === 'entity')],
    ['Events', follows.filter((r) => r.kind === 'event_type')],
    ['Topics', follows.filter((r) => r.kind === 'topic')],
    ['Keywords', follows.filter((r) => r.kind === 'keyword')],
    ['Sources and regions', follows.filter((r) => r.kind === 'source' || r.kind === 'region')],
  ]

  const ruleName = (r: DraftRule) => {
    const base = label(r.kind, r.value)
    return r.kind === 'event_type' && r.entity ? `${label('entity', r.entity)}: ${base}` : base
  }

  return (
    <div className="page-wide">
      <header className="page-header">
        <div>
          <Link to="/profiles" className="breadcrumb">
            <ChevronLeft size={15} /> Profiles
          </Link>
          <h1>{draft.name || 'Untitled profile'}</h1>
          <p className="page-sub">
            {draft.rules.length} rules · last saved {relativeTime(profile.updated_at)}
          </p>
        </div>
        <div className="head-actions">
          <Link to={`/feed?profile=${profile.id}`} className="btn btn-secondary">
            <Newspaper size={16} /> View feed
          </Link>
          <button
            type="button"
            className="btn btn-danger"
            disabled={remove.isPending}
            onClick={() => window.confirm(`Delete “${profile.name}”? This can’t be undone.`) && remove.mutate()}
          >
            <Trash2 size={16} /> Delete
          </button>
        </div>
      </header>
      {(save.error || remove.error) && (
        <p className="auth-error" role="alert" style={{ marginBottom: 16 }}>
          {((save.error ?? remove.error) as ApiError).message}
        </p>
      )}

      <div className="editor-grid">
        <div className="editor-main">
          <section className="card">
            <h2>Settings</h2>
            <p className="card-sub">The name of this feed, and the scores that decide what you see and when you’re alerted.</p>
            <label className="field">
              <span>Name</span>
              <input value={draft.name} maxLength={80} onChange={(e) => update({ name: e.target.value })} />
            </label>
            <div className="threshold-row">
              <label className="threshold field">
                <span>Show stories scoring</span>
                <span className="threshold-value tabular">{draft.show_threshold}+</span>
                <input
                  type="range"
                  min={20}
                  max={100}
                  value={draft.show_threshold}
                  onChange={(e) => {
                    const show = Number(e.target.value)
                    update({ show_threshold: show, alert_threshold: Math.max(show, draft.alert_threshold) })
                  }}
                />
              </label>
              <label className="threshold field">
                <span>Alert me at</span>
                <span className="threshold-value tabular" style={{ color: 'var(--critical)' }}>
                  {draft.alert_threshold}+
                </span>
                <input
                  type="range"
                  min={draft.show_threshold}
                  max={100}
                  value={draft.alert_threshold}
                  onChange={(e) => update({ alert_threshold: Number(e.target.value) })}
                />
              </label>
            </div>
            <div className="tier-scale" aria-label="Score tiers">
              <span className="s-low">Low 20–39</span>
              <span className="s-normal">Normal 40–59</span>
              <span className="s-high">High 60–79</span>
              <span className="s-critical">Critical 80+</span>
            </div>
          </section>

          <section className="card">
            <h2>Always alert</h2>
            <p className="card-sub">Pinned events always reach at least their minimum score, however they’re worded.</p>
            {pins.length === 0 ? (
              <p className="rule-empty">No pinned events. Add one below with “Always be alerted about”.</p>
            ) : (
              <ul className="rule-list">
                {pins.map((r) => (
                  <li key={r.key} className="rule-row">
                    <Pin size={15} className="muted" />
                    <span className="rule-name">{ruleName(r)}</span>
                    <label className="rule-control">
                      <span className="muted small">at least</span>
                      <input
                        type="number"
                        min={20}
                        max={100}
                        value={r.min_score ?? 90}
                        onChange={(e) => updateRule(r.key, { min_score: Math.min(100, Math.max(20, Number(e.target.value))) })}
                      />
                    </label>
                    <button type="button" className="icon-btn" aria-label={`Remove ${ruleName(r)}`} onClick={() => removeRule(r.key)}>
                      <X size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card">
            <div className="card-title-row">
              <h2>Following</h2>
              {learned > 0 && (
                <button type="button" className="btn btn-ghost btn-sm" disabled={resetLearning.isPending || dirty} title={dirty ? 'Save or discard your changes first' : undefined} onClick={() => resetLearning.mutate()}>
                  <RotateCcw size={14} /> Reset {learned} learned weight{learned > 1 ? 's' : ''}
                </button>
              )}
            </div>
            <p className="card-sub">
              Weight 1–10 is how strongly a match pulls a story up. One strong match is enough to show a story.
              {learned > 0 && ' Weights marked “learned” were adjusted by your 👍/👎 feedback.'}
            </p>
            {groups.map(([title, rules]) =>
              rules.length === 0 ? null : (
                <details key={title} className="rule-group" open={rules.length <= 15}>
                  <summary>
                    <span>
                      {title}
                      <span className="count-badge">{rules.length}</span>
                    </span>
                  </summary>
                  <ul className="rule-list">
                    {rules.map((r) => (
                      <li key={r.key} className="rule-row">
                        <span className="rule-name">
                          {ruleName(r)}
                          <span className="rule-kind">{KIND_LABEL[r.kind].split(' /')[0].toLowerCase()}</span>
                        </span>
                        <label className="rule-control">
                          <input
                            type="range"
                            min={1}
                            max={10}
                            value={r.weight}
                            aria-label={`Weight for ${ruleName(r)}`}
                            onChange={(e) => updateRule(r.key, { weight: Number(e.target.value) })}
                          />
                          <span className="weight">{r.weight}</span>
                        </label>
                        {r.base != null && r.base !== r.weight && (
                          <span className={`learned ${r.weight < r.base ? 'down' : 'up'}`} title={`You set ${r.base}; feedback moved it to ${r.weight}`}>
                            learned · was {r.base}
                          </span>
                        )}
                        <button type="button" className="icon-btn" aria-label={`Remove ${ruleName(r)}`} onClick={() => removeRule(r.key)}>
                          <X size={16} />
                        </button>
                      </li>
                    ))}
                  </ul>
                </details>
              ),
            )}
            {follows.length === 0 && <p className="rule-empty">Nothing followed yet. Add a company, topic or keyword below.</p>}
          </section>

          <section className="card">
            <h2>Muted</h2>
            <p className="card-sub">Stories matching a mute are hidden, unless a pinned event also matches.</p>
            {mutes.length === 0 ? (
              <p className="rule-empty">Nothing muted.</p>
            ) : (
              <ul className="rule-list">
                {mutes.map((r) => (
                  <li key={r.key} className="rule-row">
                    <BellOff size={15} className="muted" />
                    <span className="rule-name">{ruleName(r)}</span>
                    <span className="chip">{r.expires_at ? `until ${new Date(r.expires_at).toLocaleDateString()}` : 'until you unmute'}</span>
                    <button type="button" className="icon-btn" aria-label={`Unmute ${ruleName(r)}`} onClick={() => removeRule(r.key)}>
                      <X size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {ready && <AddRule existing={draft.rules} onAdd={(rule) => update({ rules: [...draft.rules, rule] })} />}
        </div>

        <aside className="editor-side">
          <PreviewPanel draft={draft} />
        </aside>
      </div>

      {dirty && (
        <div className="save-bar" role="region" aria-label="Unsaved changes">
          <span>You have unsaved changes</span>
          <div className="head-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setDraft(toDraft(profile))}>
              Discard
            </button>
            <button type="button" className="btn btn-primary" disabled={save.isPending || !draft.name.trim()} onClick={() => save.mutate()}>
              {save.isPending ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function AddRule({ existing, onAdd }: { existing: DraftRule[]; onAdd: (rule: DraftRule) => void }) {
  const { taxonomy, sources } = useReference()
  const [mode, setMode] = useState<RuleMode>('include')
  const [kind, setKind] = useState<RuleKind>('entity')
  const [value, setValue] = useState('')
  const [weight, setWeight] = useState(6)
  const [minScore, setMinScore] = useState(90)
  const [pinEntity, setPinEntity] = useState('')
  const [muteDays, setMuteDays] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const options = useMemo(() => {
    const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name)
    switch (kind) {
      case 'entity':
        return [...(taxonomy?.entities ?? [])].sort(byName).map((e) => ({ value: e.slug, label: e.ticker ? `${e.name} (${e.ticker})` : e.name }))
      case 'topic':
        return [...(taxonomy?.topics ?? [])].sort(byName).map((t) => ({ value: t.slug, label: t.name }))
      case 'event_type':
        return [...(taxonomy?.event_types ?? [])].sort(byName).map((t) => ({ value: t.slug, label: t.name }))
      case 'source':
        return [...sources].sort(byName).map((s) => ({ value: s.slug, label: s.enabled ? s.name : `${s.name} (not collected yet)` }))
      case 'region':
        return (taxonomy?.regions ?? []).map((r) => ({ value: r, label: r }))
      default:
        return []
    }
  }, [kind, taxonomy, sources])

  const add = () => {
    const v = value.trim()
    if (!v) return setError(kind === 'keyword' ? 'Type a keyword.' : 'Choose a value.')
    if (kind === 'keyword' && (v.length < 2 || v.length > 60)) return setError('Keywords must be 2–60 characters.')
    const entity = mode === 'pin' && kind === 'event_type' && pinEntity ? pinEntity : null
    const duplicate = existing.some((r) => r.kind === kind && r.value.toLowerCase() === v.toLowerCase() && r.mode === mode && r.entity === entity)
    if (duplicate) return setError('That rule is already in the profile.')
    onAdd({
      key: nextKey(),
      kind,
      value: v,
      mode,
      weight: mode === 'include' ? weight : 10,
      entity,
      min_score: mode === 'pin' ? minScore : null,
      expires_at: mode === 'exclude' && muteDays > 0 ? new Date(Date.now() + muteDays * 86_400_000).toISOString() : null,
    })
    setValue('')
    setError(null)
  }

  return (
    <section className="card">
      <h2>Add a rule</h2>
      <p className="card-sub">Follow what you trade, pin what you can never miss, mute what you don’t care about.</p>
      <div className="add-rule">
        <label className="field">
          <span>I want to</span>
          <select value={mode} onChange={(e) => setMode(e.target.value as RuleMode)}>
            <option value="include">Follow</option>
            <option value="pin">Always be alerted about</option>
            <option value="exclude">Mute</option>
          </select>
        </label>
        <label className="field">
          <span>Kind</span>
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as RuleKind)
              setValue('')
            }}
          >
            {(Object.keys(KIND_LABEL) as RuleKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="field grow">
          <span>{kind === 'keyword' ? 'Keyword or phrase' : 'Which one'}</span>
          {kind === 'keyword' ? (
            <input value={value} maxLength={60} placeholder="e.g. repo rate" onChange={(e) => setValue(e.target.value)} />
          ) : (
            <select value={value} onChange={(e) => setValue(e.target.value)}>
              <option value="">Choose…</option>
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          )}
        </label>
        {mode === 'include' && (
          <label className="field">
            <span>Weight: {weight}</span>
            <input type="range" min={1} max={10} value={weight} onChange={(e) => setWeight(Number(e.target.value))} />
          </label>
        )}
        {mode === 'pin' && (
          <>
            <label className="field">
              <span>Minimum score</span>
              <input type="number" min={20} max={100} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
            </label>
            {kind === 'event_type' && (
              <label className="field">
                <span>Only when about</span>
                <select value={pinEntity} onChange={(e) => setPinEntity(e.target.value)}>
                  <option value="">Anyone</option>
                  {[...(taxonomy?.entities ?? [])]
                    .filter((e) => e.kind !== 'country')
                    .sort((a, b) => a.name.localeCompare(b.name))
                    .map((e) => (
                      <option key={e.slug} value={e.slug}>
                        {e.name}
                      </option>
                    ))}
                </select>
              </label>
            )}
          </>
        )}
        {mode === 'exclude' && (
          <label className="field">
            <span>For</span>
            <select value={muteDays} onChange={(e) => setMuteDays(Number(e.target.value))}>
              {MUTE_DURATIONS.map((d) => (
                <option key={d.days} value={d.days}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className="btn btn-primary" onClick={add}>
          <Plus size={16} /> Add rule
        </button>
      </div>
      {error && (
        <p className="error-text small" style={{ marginTop: 10 }}>
          {error}
        </p>
      )}
    </section>
  )
}

function PreviewPanel({ draft }: { draft: Draft }) {
  const body = useDebounced(toBody(draft), 600)
  const key = JSON.stringify(body)
  const preview = useQuery({
    queryKey: ['preview', key],
    queryFn: ({ signal }) =>
      endpoints.preview(
        { rules: body.rules, show_threshold: body.show_threshold, alert_threshold: body.alert_threshold, hours: 24 },
        signal,
      ),
    placeholderData: keepPreviousData, // keep showing the last result while recalculating
    staleTime: 60_000,
  })
  const data = preview.data

  return (
    <section className="card preview-card">
      <h2>
        <Eye size={18} className="muted" /> Live preview
      </h2>
      <p className="card-sub">Your last 24 hours with these settings. Updates as you edit, nothing is saved.</p>
      {preview.isError ? (
        <p className="error-text small">{(preview.error as ApiError).message}</p>
      ) : !data ? (
        <div className="preview-stats">
          <div className="skeleton" style={{ height: 64 }} />
          <div className="skeleton" style={{ height: 64 }} />
        </div>
      ) : (
        <>
          <div className="preview-stats">
            <div>
              <strong className="tabular">{data.shown}</strong>
              <span className="muted small">stories shown</span>
            </div>
            <div className="alerts">
              <strong className="tabular">{data.alerts}</strong>
              <span className="muted small">would alert</span>
            </div>
          </div>
          {data.stories.length === 0 ? (
            <p className="rule-empty">No stories would show. Lower the threshold or follow more.</p>
          ) : (
            <ol className="preview-list">
              {data.stories.map((s) => (
                <li key={s.story_id}>
                  <ScoreRing score={s.score} tier={s.tier} size={34} />
                  <a href={s.url} target="_blank" rel="noopener noreferrer">
                    {s.title}
                  </a>
                </li>
              ))}
            </ol>
          )}
          {preview.isFetching && <p className="updating">Updating…</p>}
        </>
      )}
    </section>
  )
}
