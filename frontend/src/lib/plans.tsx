// Plan display helpers shared by the landing page, billing page and admin.
import { BellRing, Bookmark, Bot, Clock, Layers, Newspaper, Send, Sparkles } from 'lucide-react'
import type { ReactNode } from 'react'
import type { PlanFeatures } from '../api/types'

export function formatInr(paise: number) {
  const rupees = paise / 100
  return `₹${rupees.toLocaleString('en-IN', { maximumFractionDigits: rupees % 1 ? 2 : 0 })}`
}

export interface Line {
  icon: ReactNode
  text: string
  included: boolean
}

/** What a plan includes, as short lines, from its feature data. */
export function featureLines(f: PlanFeatures): Line[] {
  return [
    {
      icon: f.feed_delay_minutes ? <Clock size={16} /> : <Newspaper size={16} />,
      text: f.feed_delay_minutes ? `Ranked feed, ${f.feed_delay_minutes} minutes delayed` : 'Real-time ranked feed',
      included: true,
    },
    { icon: <BellRing size={16} />, text: 'In-app alerts', included: true },
    { icon: <Layers size={16} />, text: `${f.max_profiles} profile${f.max_profiles === 1 ? '' : 's'}`, included: true },
    { icon: <Send size={16} />, text: 'Telegram alerts', included: f.telegram },
    { icon: <Sparkles size={16} />, text: 'AI summary and key facts', included: f.ai_summaries },
    {
      icon: <Bot size={16} />,
      text: f.agent === 'full' ? 'Ask the news + daily brief (soon)' : f.agent === 'limited' ? 'Ask the news, limited (soon)' : 'Ask the news',
      included: f.agent !== 'none',
    },
    { icon: <Bookmark size={16} />, text: f.max_saved === null ? 'Unlimited saved stories' : `Up to ${f.max_saved} saved stories`, included: true },
  ]
}

/** Whole days until `iso` (0 once passed). */
export function daysUntil(iso: string | null | undefined): number {
  if (!iso) return 0
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000))
}
