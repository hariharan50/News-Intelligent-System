// Renders the agent's short answers safely (no HTML): paragraphs, "- " bullets, **bold**,
// and [n] citations as links to the cited story.
import type { ReactNode } from 'react'
import type { Citation } from '../api/types'

function inline(text: string, citations: Map<number, Citation>, key: string): ReactNode[] {
  const out: ReactNode[] = []
  const pattern = /(\*\*[^*]+\*\*|\[\d+\])/g
  let last = 0
  let i = 0
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) out.push(text.slice(last, match.index))
    const token = match[0]
    if (token.startsWith('**')) {
      out.push(<strong key={`${key}-b${i}`}>{token.slice(2, -2)}</strong>)
    } else {
      const n = Number(token.slice(1, -1))
      const c = citations.get(n)
      out.push(
        c ? (
          <a key={`${key}-c${i}`} className="cite" href={c.url} target="_blank" rel="noopener noreferrer" title={`${c.title} · ${c.source}`}>
            {n}
          </a>
        ) : (
          ''
        ),
      )
    }
    last = match.index + token.length
    i += 1
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function RichAnswer({ text, citations = [] }: { text: string; citations?: Citation[] }) {
  const byNumber = new Map(citations.map((c) => [c.n, c]))
  const blocks: ReactNode[] = []
  let bullets: string[] = []
  const flush = (key: string) => {
    if (bullets.length) {
      blocks.push(
        <ul key={key}>
          {bullets.map((b, i) => (
            <li key={i}>{inline(b, byNumber, `${key}-${i}`)}</li>
          ))}
        </ul>,
      )
      bullets = []
    }
  }
  text.split('\n').forEach((raw, index) => {
    const line = raw.trim()
    if (/^[-•*]\s+/.test(line)) {
      bullets.push(line.replace(/^[-•*]\s+/, ''))
      return
    }
    flush(`ul${index}`)
    if (!line) return
    if (/^(Overnight|Watch today)$/.test(line)) blocks.push(<h4 key={index}>{line}</h4>)
    else blocks.push(<p key={index}>{inline(line, byNumber, `p${index}`)}</p>)
  })
  flush('ul-end')
  return <div className="rich-answer">{blocks}</div>
}

export function Sources({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null
  return (
    <ol className="answer-sources">
      {citations.map((c) => (
        <li key={c.n}>
          <span className="cite">{c.n}</span>
          <a href={c.url} target="_blank" rel="noopener noreferrer">
            {c.title}
          </a>
          <span className="muted small">
            {c.source} · {new Date(c.published_at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
          </span>
        </li>
      ))}
    </ol>
  )
}
