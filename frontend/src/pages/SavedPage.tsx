import { useInfiniteQuery } from '@tanstack/react-query'
import { Bookmark } from 'lucide-react'
import { Link } from 'react-router'
import * as endpoints from '../api/endpoints'
import { StoryList, StoryListSkeleton } from '../components/feed/StoryList'
import { EmptyState } from '../components/Feedback'
import { useStoryActions } from '../hooks/useStoryActions'
import '../styles/feed.css'

export function SavedPage() {
  const actions = useStoryActions()
  const saved = useInfiniteQuery({
    queryKey: ['saved'],
    queryFn: ({ pageParam, signal }) => endpoints.saved({ offset: pageParam }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
  })
  const items = saved.data?.pages.flatMap((p) => p.items) ?? []
  const total = saved.data?.pages[0]?.total

  return (
    <div className="list-page">
      <header className="feed-header">
        <div className="feed-heading">
          <p className="feed-greeting">Your library</p>
          <h1>Saved</h1>
        </div>
        <p className="list-page-sub">
          {total !== undefined && <strong>{total} {total === 1 ? 'story' : 'stories'}</strong>} kept for as long as you have an account, even after they leave your feed.
        </p>
      </header>

      {saved.isPending ? (
        <StoryListSkeleton count={4} />
      ) : items.length === 0 ? (
        <div className="feed-empty">
        <EmptyState
          icon={<Bookmark size={24} />}
          title="Nothing saved yet"
          action={
            <Link to="/feed" className="btn btn-primary">
              Go to your feed
            </Link>
          }
        >
          Press <kbd>s</kbd> or the bookmark on any story to keep it here.
        </EmptyState>
        </div>
      ) : (
        <StoryList
          items={items}
          onMarkOpened={actions.markOpened}
          onToggleRead={actions.toggleRead}
          onToggleSaved={actions.toggleSaved}
          footer={
            saved.hasNextPage && (
              <button type="button" className="feed-more" onClick={() => saved.fetchNextPage()} disabled={saved.isFetchingNextPage}>
                {saved.isFetchingNextPage ? 'Loading…' : 'Load more'}
              </button>
            )
          }
        />
      )}
    </div>
  )
}
