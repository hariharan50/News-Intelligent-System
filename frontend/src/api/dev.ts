// Development-only endpoints (mounted when ENVIRONMENT=local). Replaced by the admin and
// feed APIs in later phases.

export interface DevSource {
  slug: string
  name: string
  category: string
  licence_class: string
  enabled: boolean
  is_priority: boolean
  poll_interval_seconds: number
  status: 'unknown' | 'healthy' | 'degraded' | 'failing'
  consecutive_failures: number
  last_polled_at: string | null
  last_success_at: string | null
  last_new_item_at: string | null
  next_poll_at: string
  last_error: string | null
  notes: string | null
}

export interface DevArticle {
  id: string
  source: string
  title: string
  url: string
  excerpt: string | null
  published_at: string
  published_at_estimated: boolean
  fetched_at: string
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal })
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`)
  return (await res.json()) as T
}

export const fetchSources = (signal?: AbortSignal) => getJson<DevSource[]>('/api/dev/sources', signal)

export const fetchLatestArticles = (limit = 30, signal?: AbortSignal) =>
  getJson<DevArticle[]>(`/api/dev/articles?limit=${limit}`, signal)

export interface DevStoryArticle {
  source: string
  source_name: string
  title: string
  url: string
  published_at: string
}

export interface DevStory {
  id: string
  title: string
  url: string
  lead_source: string
  event_type: string | null
  is_routine: boolean
  topics: string[]
  entities: string[]
  regions: string[]
  article_count: number
  source_count: number
  max_authority: number
  has_official_source: boolean
  first_published_at: string
  last_published_at: string
  articles: DevStoryArticle[]
}

export interface StoryFilters {
  includeRoutine: boolean
  multiSourceOnly: boolean
}

export const fetchStories = (filters: StoryFilters, signal?: AbortSignal) => {
  const params = new URLSearchParams({
    limit: '40',
    include_routine: String(filters.includeRoutine),
    multi_source_only: String(filters.multiSourceOnly),
  })
  return getJson<DevStory[]>(`/api/dev/stories?${params}`, signal)
}

export interface Taxonomy {
  version: number
  topics: Record<string, string>
  entities: Record<string, string>
  event_types: Record<string, string>
}

export const fetchTaxonomy = (signal?: AbortSignal) => getJson<Taxonomy>('/api/dev/taxonomy', signal)
