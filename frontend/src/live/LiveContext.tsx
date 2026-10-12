import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import type { AlertEvent } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useLiveUpdates, type LiveEvent, type LiveStatus } from '../hooks/useLiveUpdates'
import { tierOf } from '../lib/tiers'

export interface Toast {
  id: string
  event: AlertEvent
}

interface LiveState {
  status: LiveStatus
  /** Story ids announced for a profile since its feed was last refreshed. */
  pending: (profileId: string) => string[]
  clearPending: (profileId: string) => void
  toasts: Toast[]
  dismissToast: (id: string) => void
}

const LiveContext = createContext<LiveState | null>(null)
const MAX_TOASTS = 4

/** A system notification when the tab is in the background and the user allowed it. */
function notifyInBackground(alert: AlertEvent) {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted' || !document.hidden) return
  const tier = tierOf(alert.score)
  try {
    const n = new Notification(`${tier ? tier[0].toUpperCase() + tier.slice(1) : 'Alert'} ${alert.score} · ${alert.source}`, {
      body: alert.title,
      tag: alert.alert_id, // the same alert never stacks twice
      icon: '/favicon.svg',
    })
    n.onclick = () => {
      window.focus()
      window.open(alert.url, '_blank', 'noopener')
      n.close()
    }
  } catch {
    // some browsers only allow notifications from a service worker; the in-app toast still shows
  }
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const { status: authStatus } = useAuth()
  const queryClient = useQueryClient()
  const [pendingByProfile, setPending] = useState<Record<string, string[]>>({})
  const [toasts, setToasts] = useState<Toast[]>([])

  const onEvent = useCallback(
    (event: LiveEvent) => {
      if (event.type === 'billing') {
        // A payment, renewal or cancellation landed: refresh everything plan-dependent.
        void queryClient.invalidateQueries({ queryKey: ['billing'] })
        void queryClient.invalidateQueries({ queryKey: ['feed'] })
        return
      }
      if (event.type === 'story') {
        const story = event.data
        if (!story.new) return
        setPending((p) => {
          const current = p[story.profile_id] ?? []
          return current.includes(story.story_id) ? p : { ...p, [story.profile_id]: [story.story_id, ...current] }
        })
        return
      }
      const alert = event.data
      setToasts((t) =>
        t.some((x) => x.event.alert_id === alert.alert_id)
          ? t
          : [{ id: alert.alert_id, event: alert }, ...t].slice(0, MAX_TOASTS),
      )
      notifyInBackground(alert)
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
    [queryClient],
  )

  const status = useLiveUpdates(authStatus === 'signed-in', onEvent)

  const value = useMemo<LiveState>(
    () => ({
      status,
      pending: (profileId) => pendingByProfile[profileId] ?? [],
      clearPending: (profileId) => setPending((p) => ({ ...p, [profileId]: [] })),
      toasts,
      dismissToast: (id) => setToasts((t) => t.filter((x) => x.id !== id)),
    }),
    [status, pendingByProfile, toasts],
  )

  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>
}

export function useLive() {
  const ctx = useContext(LiveContext)
  if (!ctx) throw new Error('useLive must be used inside <LiveProvider>')
  return ctx
}
