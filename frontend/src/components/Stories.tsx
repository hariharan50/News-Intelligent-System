import { useCallback, useState } from 'react'
import { fetchStories, fetchTaxonomy, type DevStory, type StoryFilters, type Taxonomy } from '../api/dev'
import { usePolling } from '../hooks/usePolling'
import { formatIst } from '../lib/time'

const MAX_ENTITY_CHIPS = 5
// Countries are on almost every story; show them only if nothing more specific is there.
const COUNTRY_SLUGS = new Set(['india', 'united_states', 'china', 'russia', 'ukraine', 'israel', 'iran', 'pakistan', 'middle_east', 'european_union'])

function label(map: Record<string, string> | undefined, slug: string) {
  return map?.[slug] ?? slug.replace(/_/g, ' ')
}

function StoryCard({ story, taxonomy }: { story: DevStory; taxonomy: Taxonomy | null }) {
  const specific = story.entities.filter((e) => !COUNTRY_SLUGS.has(e))
  const entityChips = (specific.length ? specific : story.entities).slice(0, MAX_ENTITY_CHIPS)

  return (
    <li className="story">
      <a className="story-title" href={story.url} target="_blank" rel="noopener noreferrer">
        {story.title}
      </a>
      <div className="muted small">
        {story.lead_source} · {formatIst(story.first_published_at)}
        {story.has_official_source && <span className="tag tag-official">official source</span>}
      </div>

      <div className="chips">
        {story.event_type && (
          <span className={`chip chip-event${story.is_routine ? ' chip-routine' : ''}`}>
            {label(taxonomy?.event_types, story.event_type)}
            {story.is_routine && ' · routine'}
          </span>
        )}
        {entityChips.map((e) => (
          <span key={e} className="chip chip-entity">
            {label(taxonomy?.entities, e)}
          </span>
        ))}
        {story.topics.map((t) => (
          <span key={t} className="chip">
            {label(taxonomy?.topics, t)}
          </span>
        ))}
      </div>

      {story.article_count > 1 && (
        <details className="coverage">
          <summary>
            {story.source_count} source{story.source_count > 1 ? 's' : ''} · {story.article_count} articles
          </summary>
          <ul>
            {story.articles.map((a) => (
              <li key={a.url}>
                <a href={a.url} target="_blank" rel="noopener noreferrer">
                  {a.title}
                </a>
                <span className="muted small">
                  {' '}
                  — {a.source_name}, {formatIst(a.published_at)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  )
}

export function Stories() {
  const [filters, setFilters] = useState<StoryFilters>({ includeRoutine: false, multiSourceOnly: false })
  const load = useCallback((signal: AbortSignal) => fetchStories(filters, signal), [filters])
  const { data: stories, error } = usePolling(load, 15_000)
  const { data: taxonomy } = usePolling(fetchTaxonomy, 300_000)

  const toggle = (key: keyof StoryFilters) => setFilters((f) => ({ ...f, [key]: !f[key] }))

  return (
    <>
      <div className="filters">
        <label>
          <input type="checkbox" checked={filters.includeRoutine} onChange={() => toggle('includeRoutine')} />
          Show routine items
        </label>
        <label>
          <input type="checkbox" checked={filters.multiSourceOnly} onChange={() => toggle('multiSourceOnly')} />
          Only stories from several sources
        </label>
      </div>
      {error && !stories ? (
        <span className="pill pill-error">Could not load stories</span>
      ) : !stories ? (
        <p className="muted">Loading…</p>
      ) : stories.length === 0 ? (
        <p className="muted">No stories match.</p>
      ) : (
        <ul className="list">
          {stories.map((s) => (
            <StoryCard key={s.id} story={s} taxonomy={taxonomy} />
          ))}
        </ul>
      )}
    </>
  )
}
