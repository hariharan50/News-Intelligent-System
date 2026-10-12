import { useEffect, useRef, useState } from 'react'
import { getAccessToken, refreshAccessToken } from '../api/client'
import type { AlertEvent, StoryEvent } from '../api/types'

export type LiveEvent =
  | { type: 'story'; data: StoryEvent }
  | { type: 'alert'; data: AlertEvent }
  | { type: 'billing'; data: { status: string } }

export type LiveStatus = 'connecting' | 'live' | 'offline'

const MAX_BACKOFF_MS = 30_000

interface SseFrame {
  event: string
  data: string
}

/** Split a text buffer into complete SSE frames; returns the frames and the leftover text. */
export function parseSse(buffer: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = []
  const parts = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = parts.pop() ?? ''
  for (const part of parts) {
    let event = 'message'
    const data: string[] = []
    for (const line of part.split('\n')) {
      if (line.startsWith(':')) continue // comment / keep-alive ping
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length) frames.push({ event, data: data.join('\n') })
  }
  return { frames, rest }
}

/**
 * Keeps a live connection to /api/stream while `enabled`, calling `onEvent` for each event.
 *
 * Uses fetch() rather than EventSource so the access token goes in the Authorization header
 * instead of the URL. Reconnects with backoff; on "reauth" (token expired) it refreshes the
 * token and reconnects straight away.
 */
export function useLiveUpdates(enabled: boolean, onEvent: (event: LiveEvent) => void): LiveStatus {
  const [status, setStatus] = useState<LiveStatus>('connecting')
  const handler = useRef(onEvent)
  useEffect(() => {
    handler.current = onEvent
  })

  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    let backoff = 1000

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        const id = window.setTimeout(resolve, ms)
        controller.signal.addEventListener('abort', () => {
          window.clearTimeout(id)
          resolve()
        })
      })

    async function connectOnce(): Promise<'reauth' | 'closed'> {
      let token = getAccessToken() ?? (await refreshAccessToken())
      if (!token) return 'closed'
      let res = await fetch('/api/stream', {
        headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
        signal: controller.signal,
      })
      if (res.status === 401) {
        token = await refreshAccessToken()
        if (!token) return 'closed'
        res = await fetch('/api/stream', {
          headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
          signal: controller.signal,
        })
      }
      if (!res.ok || !res.body) throw new Error(`stream: HTTP ${res.status}`)

      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) return 'closed'
        const { frames, rest } = parseSse(buffer + value)
        buffer = rest
        for (const frame of frames) {
          if (frame.event === 'ready') {
            setStatus('live')
            backoff = 1000
          } else if (frame.event === 'reauth') {
            return 'reauth'
          } else if (frame.event === 'story' || frame.event === 'alert' || frame.event === 'billing') {
            try {
              handler.current({ type: frame.event, data: JSON.parse(frame.data) } as LiveEvent)
            } catch {
              // ignore a malformed event rather than dropping the connection
            }
          }
        }
      }
    }

    void (async () => {
      while (!controller.signal.aborted) {
        try {
          const outcome = await connectOnce()
          if (controller.signal.aborted) return
          if (outcome === 'reauth') {
            await refreshAccessToken()
            continue
          }
        } catch {
          if (controller.signal.aborted) return
        }
        setStatus('offline')
        await sleep(backoff)
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
        setStatus('connecting')
      }
    })()

    return () => controller.abort()
  }, [enabled])

  return enabled ? status : 'offline'
}
