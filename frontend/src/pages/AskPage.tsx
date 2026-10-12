import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUp, Bot, Loader2, MessageSquarePlus, Search, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import type { AgentAnswer } from '../api/types'
import { RichAnswer, Sources } from '../components/RichAnswer'
import { askStream } from '../lib/askStream'
import '../styles/ask.css'
import { useFeatures } from '../hooks/useFeatures'
import { Navigate } from 'react-router'

const EXAMPLES = [
  'What happened in the markets today?',
  'What moved crude oil this week?',
  'Anything new from the RBI?',
  'Any news on HDFC Bank?',
]

interface Turn {
  question: string
  answer: AgentAnswer | null
  draft: string
  status: string | null
  error: string | null
}

export function AskPage() {
  const features = useFeatures()
  const queryClient = useQueryClient()
  const state = useQuery({ queryKey: ['agent'], queryFn: endpoints.agentStatus })
  const [turns, setTurns] = useState<Turn[]>([])
  const [conversation, setConversation] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => () => abort.current?.abort(), [])
  const lastAnswer = turns.at(-1)?.answer
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, lastAnswer])

  const update = (patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((all) => {
      const last = all.at(-1)
      if (!last) return all
      return [...all.slice(0, -1), { ...last, ...(typeof patch === 'function' ? patch(last) : patch) }]
    })

  const ask = async (text: string) => {
    const q = text.trim()
    if (q.length < 3 || busy) return
    setQuestion('')
    setBusy(true)
    setTurns((all) => [...all, { question: q, answer: null, draft: '', status: 'Thinking', error: null }])
    abort.current = new AbortController()
    try {
      const answer = await askStream(
        { question: q, conversation_id: conversation },
        {
          onStatus: (s) => update({ status: s }),
          onDelta: (d) => update((t) => ({ draft: t.draft + d, status: null })),
          onReset: () => update({ draft: '' }),
        },
        abort.current.signal,
      )
      setConversation(answer.conversation_id)
      update({ answer, draft: '', status: null })
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      update({ error: e instanceof ApiError || e instanceof Error ? e.message : 'Something went wrong.', status: null })
    } finally {
      setBusy(false)
      void queryClient.invalidateQueries({ queryKey: ['agent'] })
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void ask(question)
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void ask(question)
    }
  }
  const newConversation = () => {
    abort.current?.abort()
    setTurns([])
    setConversation(null)
  }

  if (features.loaded && !features.ai) return <Navigate to="/feed" replace />
  const s = state.data
  const locked = s && !s.plan_allows

  return (
    <div className="page ask-page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">
            <Sparkles size={13} className="inline-icon" /> AI agent
          </p>
          <h1>Ask the news</h1>
          <p className="page-sub">Answers come only from stories we’ve collected, with a link to each source. News, not investment advice.</p>
        </div>
        {turns.length > 0 && (
          <button type="button" className="btn btn-secondary" onClick={newConversation}>
            <MessageSquarePlus size={16} /> New question
          </button>
        )}
      </header>

      {locked && (
        <section className="card ask-locked">
          <Bot size={22} />
          <div>
            <h2>Ask the news is part of Pro</h2>
            <p className="card-sub">Ask about anything in the news, like “What moved crude this week?”, and get a short answer with links to every source.</p>
          </div>
          <Link to="/billing" className="btn btn-primary">
            See plans
          </Link>
        </section>
      )}
      {s && !locked && !s.available && (
        <p className="draft-banner">{s.reason === 'busy' ? 'The AI has reached today’s limit. Try again tomorrow.' : 'The AI isn’t switched on yet. Ask your administrator to add the Claude API key.'}</p>
      )}

      {!locked && (
        <>
          <div className="ask-thread">
            {turns.length === 0 && (
              <div className="ask-empty">
                <p className="muted">Try one of these:</p>
                <div className="ask-examples">
                  {EXAMPLES.map((ex) => (
                    <button key={ex} type="button" className="chip" onClick={() => ask(ex)} disabled={busy || !s?.available}>
                      <Search size={12} /> {ex}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((t, i) => (
              <div key={i} className="ask-turn">
                <p className="ask-question">{t.question}</p>
                <div className="ask-answer">
                  <span className="ask-avatar">
                    <Bot size={16} />
                  </span>
                  <div>
                    {t.answer ? (
                      <>
                        <RichAnswer text={t.answer.answer} citations={t.answer.citations} />
                        <Sources citations={t.answer.citations} />
                      </>
                    ) : t.error ? (
                      <p className="auth-error">{t.error}</p>
                    ) : (
                      <>
                        {t.draft && <RichAnswer text={t.draft.replace(/\[S\d+(?:,\s*S\d+)*\]/g, '')} />}
                        {t.status && (
                          <p className="ask-status">
                            <Loader2 size={14} className="spin" /> {t.status}…
                          </p>
                        )}
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>

          <form className="ask-box" onSubmit={submit}>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKey}
              placeholder={turns.length ? 'Ask a follow-up…' : 'Ask about the news…'}
              maxLength={500}
              rows={2}
              disabled={busy || !s?.available}
              aria-label="Your question"
            />
            <button type="submit" className="btn btn-primary ask-send" disabled={busy || question.trim().length < 3 || !s?.available} aria-label="Ask">
              {busy ? <Loader2 size={18} className="spin" /> : <ArrowUp size={18} />}
            </button>
          </form>
          {s && (
            <p className="ask-quota muted small">
              {s.remaining} of {s.limit} questions left today · resets at midnight IST
            </p>
          )}

          {turns.length === 0 && s && s.recent.length > 0 && (
            <section className="ask-recent">
              <h2>Recent questions</h2>
              {s.recent.slice(0, 8).map((r) => (
                <details key={r.id} className="card ask-recent-item">
                  <summary>
                    <span>{r.question}</span>
                    <span className="muted small">{new Date(r.created_at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</span>
                  </summary>
                  <RichAnswer text={r.answer} citations={r.citations} />
                  <Sources citations={r.citations} />
                </details>
              ))}
            </section>
          )}
        </>
      )}
    </div>
  )
}
