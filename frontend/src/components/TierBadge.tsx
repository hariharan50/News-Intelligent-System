import type { Tier } from '../api/types'

const LABEL: Record<Tier, string> = { critical: 'Critical', high: 'High', normal: 'Normal', low: 'Low' }

export function TierBadge({ tier, score }: { tier: Tier | null; score?: number | null }) {
  if (score === null) return <span className="tier tier-none">Saved</span>
  return (
    <span className={`tier tier-${tier ?? 'none'}`}>
      <span className="tier-dot" aria-hidden />
      {tier ? LABEL[tier] : 'Below low'}
      {score !== undefined && <strong className="tabular">{score}</strong>}
    </span>
  )
}

/** Circular score: the ring fills to the score, coloured by tier. */
export function ScoreRing({ score, tier, size = 52 }: { score: number | null; tier: Tier | null; size?: number }) {
  if (score === null) {
    return (
      <div className="score-ring score-ring-empty" style={{ width: size, height: size }} aria-label="Not scored">
        <span>—</span>
      </div>
    )
  }
  return (
    <div
      className={`score-ring ring-${tier ?? 'none'}`}
      style={{ width: size, height: size, ['--score' as string]: `${score}` }}
      role="img"
      aria-label={`Importance ${score} out of 100${tier ? `, ${tier}` : ''}`}
      title={`Importance score ${score}/100`}
    >
      <span className="tabular">{score}</span>
    </div>
  )
}
