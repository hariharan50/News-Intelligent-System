import type { Tier } from '../api/types'

/** Same bands as the backend (scoring/engine.py TIERS). */
export function tierOf(score: number): Tier | null {
  if (score >= 80) return 'critical'
  if (score >= 60) return 'high'
  if (score >= 40) return 'normal'
  if (score >= 20) return 'low'
  return null
}
