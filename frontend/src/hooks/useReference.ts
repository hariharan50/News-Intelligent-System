import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import * as endpoints from '../api/endpoints'
import type { RuleKind } from '../api/types'

/** Taxonomy and sources: names for slugs, and the options for profile rule pickers. */
export function useReference() {
  const taxonomy = useQuery({ queryKey: ['taxonomy'], queryFn: endpoints.taxonomy, staleTime: Infinity })
  const sources = useQuery({ queryKey: ['sources'], queryFn: endpoints.sources, staleTime: 10 * 60_000 })

  return useMemo(() => {
    const t = taxonomy.data
    const names: Record<RuleKind, Map<string, string>> = {
      topic: new Map(t?.topics.map((x) => [x.slug, x.name])),
      entity: new Map(t?.entities.map((x) => [x.slug, x.name])),
      event_type: new Map(t?.event_types.map((x) => [x.slug, x.name])),
      region: new Map(t?.regions.map((r) => [r, r])),
      source: new Map(sources.data?.map((s) => [s.slug, s.name])),
      keyword: new Map(),
    }
    const label = (kind: RuleKind, slug: string) =>
      names[kind].get(slug) ?? (kind === 'keyword' ? `“${slug}”` : slug.replace(/_/g, ' '))
    return { ready: Boolean(t && sources.data), taxonomy: t, sources: sources.data ?? [], names, label }
  }, [taxonomy.data, sources.data])
}
