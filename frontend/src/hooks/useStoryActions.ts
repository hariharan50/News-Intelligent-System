import { useQueryClient, type InfiniteData, type QueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import * as endpoints from '../api/endpoints'
import type { FeedItem } from '../api/types'

type Page = { items: FeedItem[] }

/** Apply a change to one story in every cached feed and saved-list page. */
function patchCached(client: QueryClient, storyId: string, patch: Partial<FeedItem>) {
  for (const prefix of [['feed'], ['saved']]) {
    client.setQueriesData<InfiniteData<Page>>({ queryKey: prefix }, (data) =>
      // Some 'feed' entries are plain counts (stat cards), not paged lists: leave them alone.
      data && Array.isArray(data.pages)
        ? {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((i) => (i.story_id === storyId ? { ...i, ...patch } : i)),
            })),
          }
        : data,
    )
  }
}

/** Read/save toggles: update the screen immediately, roll back if the request fails. */
export function useStoryActions() {
  const client = useQueryClient()

  const run = useCallback(
    async (item: FeedItem, patch: Partial<FeedItem>, request: () => Promise<void>) => {
      const before: Partial<FeedItem> = Object.fromEntries(
        Object.keys(patch).map((k) => [k, item[k as keyof FeedItem]]),
      )
      patchCached(client, item.story_id, patch)
      try {
        await request()
      } catch {
        patchCached(client, item.story_id, before)
      }
    },
    [client],
  )

  return {
    markOpened: (item: FeedItem) => {
      if (!item.is_read) void run(item, { is_read: true }, () => endpoints.markRead(item.story_id, true))
    },
    toggleRead: (item: FeedItem) =>
      void run(item, { is_read: !item.is_read }, () => endpoints.markRead(item.story_id, !item.is_read)),
    toggleSaved: async (item: FeedItem) => {
      await run(item, { is_saved: !item.is_saved }, () => endpoints.markSaved(item.story_id, !item.is_saved))
      void client.invalidateQueries({ queryKey: ['saved'] })
    },
  }
}
