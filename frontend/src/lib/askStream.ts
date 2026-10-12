// POST /api/agent/ask and read its Server-Sent Events as they arrive.
import { errorFrom, getAccessToken, refreshAccessToken } from '../api/client'
import type { AgentAnswer } from '../api/types'

export interface AskHandlers {
  onStatus: (text: string) => void
  onDelta: (text: string) => void
  onReset: () => void
}

/** Resolves with the saved answer; rejects with an ApiError or Error. */
export async function askStream(
  body: { question: string; conversation_id?: string | null },
  handlers: AskHandlers,
  signal?: AbortSignal,
): Promise<AgentAnswer> {
  const send = (token: string | null) =>
    fetch('/api/agent/ask', {
      method: 'POST',
      signal,
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    })

  let res = await send(getAccessToken())
  if (res.status === 401) {
    const token = await refreshAccessToken()
    if (token) res = await send(token)
  }
  if (!res.ok) throw await errorFrom(res)
  if (!res.body) throw new Error('Your browser can’t read streamed answers.')

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      let event = 'message'
      let data = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      const payload = data ? JSON.parse(data) : {}
      if (event === 'status') handlers.onStatus(payload.text)
      else if (event === 'delta') handlers.onDelta(payload.text)
      else if (event === 'reset') handlers.onReset()
      else if (event === 'done') return payload as AgentAnswer
      else if (event === 'error') throw new Error(payload.message || 'Something went wrong.')
    }
  }
  throw new Error('The answer was cut off. Please ask again.')
}
