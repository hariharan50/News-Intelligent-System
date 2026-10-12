import { useQueryClient } from '@tanstack/react-query'
import { BellOff, Check, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import * as endpoints from '../api/endpoints'
import type { FeedbackResult, Rule } from '../api/types'

export interface FeedbackNoticeState {
  id: number
  profileId: string
  result: FeedbackResult
}

/** Add a mute rule for an entity to a profile (keeps every other rule as it is). */
async function muteEntity(profileId: string, entity: string) {
  const profile = await endpoints.profile(profileId)
  const keep = profile.rules.map(({ id: _id, base_weight: _base, ...rule }: Rule) => rule)
  await endpoints.saveProfile(profileId, {
    name: profile.name,
    show_threshold: profile.show_threshold,
    alert_threshold: profile.alert_threshold,
    rules: [...keep, { kind: 'entity', value: entity, mode: 'exclude', weight: 5, entity: null, min_score: null, expires_at: null }],
  })
}

/** What a relevance verdict changed, with a one-click mute when we suggest one. */
export function FeedbackNotice({ notice, onClose }: { notice: FeedbackNoticeState; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [muted, setMuted] = useState(false)
  const [busy, setBusy] = useState(false)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })
  useEffect(() => {
    const id = window.setTimeout(() => close.current(), notice.result.suggestion ? 15_000 : 6_000)
    return () => window.clearTimeout(id)
  }, [notice.id, notice.result.suggestion])

  const { result } = notice
  const headline = result.verdict === 'relevant' ? 'More like this' : 'Less like this'
  const changes = result.adjustments.slice(0, 3).map((a) => `${a.label.replace(/^\w+: /, '')} ${a.from}→${a.to}`)

  const mute = async () => {
    if (!result.suggestion) return
    setBusy(true)
    try {
      await muteEntity(notice.profileId, result.suggestion.value)
      setMuted(true)
      await Promise.all(
        [['feed'], ['profiles'], ['profile', notice.profileId]].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="feedback-notice" role="status">
      <span className="feedback-icon">
        <Check size={16} />
      </span>
      <div className="feedback-body">
        <strong>{headline}</strong>
        <span className="muted small">
          {changes.length ? `Adjusted: ${changes.join(' · ')}` : 'Noted. Your weights were already at their limit.'}
        </span>
        {result.suggestion && !muted && (
          <span className="feedback-suggestion">
            You’ve marked {result.suggestion.count} {result.suggestion.label} stories as not relevant.
            <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={mute}>
              <BellOff size={14} /> Mute {result.suggestion.label}
            </button>
          </span>
        )}
        {muted && <span className="status-ok small">Muted {result.suggestion?.label}. Undo it in your profile.</span>}
      </div>
      <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
        <X size={16} />
      </button>
    </div>
  )
}
