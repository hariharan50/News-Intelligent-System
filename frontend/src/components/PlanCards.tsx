import { Minus } from 'lucide-react'
import type { ReactNode } from 'react'
import type { Plan } from '../api/types'
import { featureLines, formatInr } from '../lib/plans'

interface Props {
  plans: Plan[]
  /** The button (or label) for each plan. */
  action: (plan: Plan) => ReactNode
  /** Small label at the top right of a card, e.g. "Current plan". */
  badge?: (plan: Plan) => ReactNode
  featured?: string
}

export function PlanCards({ plans, action, badge, featured = 'pro' }: Props) {
  return (
    <div className="plan-grid">
      {plans.map((plan) => (
        <article key={plan.code} className={`plan-card${plan.code === featured ? ' plan-featured' : ''}`}>
          <div className="plan-head">
            <h3>{plan.name}</h3>
            {badge?.(plan)}
          </div>
          <p className="plan-price">
            {plan.price_paise ? formatInr(plan.price_paise) : '₹0'}
            {plan.price_paise > 0 && <span className="plan-per"> / {plan.interval}</span>}
          </p>
          {plan.tagline && <p className="plan-tagline">{plan.tagline}</p>}
          <ul>
            {featureLines(plan.features).map((line) => (
              <li key={line.text} className={line.included ? '' : 'is-missing'}>
                {line.included ? line.icon : <Minus size={16} />} {line.text}
              </li>
            ))}
          </ul>
          <div className="plan-action">{action(plan)}</div>
        </article>
      ))}
    </div>
  )
}
