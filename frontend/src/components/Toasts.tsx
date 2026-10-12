import { BellRing, ExternalLink, Pin, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { tierOf } from '../lib/tiers'
import { useLive, type Toast } from '../live/LiveContext'
import { TierBadge } from './TierBadge'

const AUTO_DISMISS_MS = 25_000

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  // Keep the timer stable across re-renders (a new toast arriving must not reset it).
  const dismiss = useRef(onDismiss)
  useEffect(() => {
    dismiss.current = onDismiss
  })
  useEffect(() => {
    const id = window.setTimeout(() => dismiss.current(), AUTO_DISMISS_MS)
    return () => window.clearTimeout(id)
  }, [toast.id])

  const alert = toast.event
  return (
    <div className="toast" role="status">
      <span className="toast-icon">
        <BellRing size={17} />
      </span>
      <div className="toast-body">
        <span className="toast-label">
          Alert <TierBadge tier={tierOf(alert.score)} score={alert.score} />
          {alert.pinned && <Pin size={12} aria-label="Pinned" />}
        </span>
        <a className="toast-title" href={alert.url} target="_blank" rel="noopener noreferrer" onClick={onDismiss}>
          {alert.title} <ExternalLink size={12} style={{ display: 'inline', verticalAlign: '-1px' }} />
        </a>
        <span className="toast-source">{alert.source}</span>
      </div>
      <button type="button" className="icon-btn" aria-label="Dismiss alert" onClick={onDismiss}>
        <X size={16} />
      </button>
    </div>
  )
}

/** In-app alerts for stories that crossed the alert line while the app was open. */
export function Toasts() {
  const { toasts, dismissToast } = useLive()
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => dismissToast(t.id)} />
      ))}
    </div>
  )
}
