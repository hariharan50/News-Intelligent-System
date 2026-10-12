// "Refresh": asks the server to check every source now, shows progress while the worker
// fetches, then reloads the feed. Runs are shared on the server, so clicking again
// during (or just after) a run simply follows that run.
import { useQueryClient } from '@tanstack/react-query'
import { Check, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'

type State = { kind: 'idle' } | { kind: 'running'; done: number; total: number } | { kind: 'done' } | { kind: 'error'; message: string }

const POLL_MS = 1500
const SETTLE_MS = 4000 // after the last source: time for grouping and scoring to finish
const GIVE_UP_MS = 90_000

export function RefreshButton({ compact = false }: { compact?: boolean }) {
  const queryClient = useQueryClient()
  const [state, setState] = useState<State>({ kind: 'idle' })
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const reloadData = () => {
    void queryClient.invalidateQueries({ queryKey: ['feed'] })
    void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    void queryClient.invalidateQueries({ queryKey: ['saved'] })
  }

  const finish = () => {
    reloadData()
    setState({ kind: 'done' })
    timer.current = window.setTimeout(() => setState({ kind: 'idle' }), 3000)
  }

  const follow = (startedAt: number) => {
    timer.current = window.setTimeout(async () => {
      try {
        const run = await endpoints.refreshStatus()
        if (run.running && Date.now() - startedAt < GIVE_UP_MS) {
          setState({ kind: 'running', done: run.done, total: run.total })
          // Stories appear as sources land: refresh the list along the way too.
          if (run.done > 0) void queryClient.invalidateQueries({ queryKey: ['feed'] })
          follow(startedAt)
        } else {
          setState({ kind: 'running', done: run.total, total: run.total })
          timer.current = window.setTimeout(finish, SETTLE_MS)
        }
      } catch {
        finish()
      }
    }, POLL_MS)
  }

  const start = async () => {
    if (state.kind === 'running') return
    window.clearTimeout(timer.current)
    setState({ kind: 'running', done: 0, total: 0 })
    try {
      const run = await endpoints.startRefresh()
      reloadData() // show anything that arrived since the page loaded, right away
      if (run.running) {
        setState({ kind: 'running', done: run.done, total: run.total })
        follow(Date.now())
      } else {
        finish() // a run just finished (or nothing needed checking): the data is current
      }
    } catch (e) {
      setState({ kind: 'error', message: e instanceof ApiError ? e.message : 'Couldn’t refresh.' })
      timer.current = window.setTimeout(() => setState({ kind: 'idle' }), 4000)
    }
  }

  const running = state.kind === 'running'
  const label =
    state.kind === 'running'
      ? state.total
        ? `Fetching ${state.done}/${state.total}`
        : 'Fetching…'
      : state.kind === 'done'
        ? 'Up to date'
        : state.kind === 'error'
          ? state.message
          : 'Refresh'
  const title =
    state.kind === 'running' && state.total
      ? `Checking ${state.total} news sources for new articles: ${state.done} done`
      : 'Fetch the latest news from every source now'

  return (
    <button
      type="button"
      className={`refresh-btn${compact ? ' compact' : ''} is-${state.kind}`}
      onClick={start}
      disabled={running}
      title={title}
      aria-label={compact ? label : undefined}
      aria-live="polite"
    >
      {state.kind === 'done' ? <Check size={16} /> : <RefreshCw size={16} className={running ? 'spin' : undefined} />}
      {!compact && <span className="tabular">{label}</span>}
    </button>
  )
}
