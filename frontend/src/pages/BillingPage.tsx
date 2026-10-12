import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarClock, CreditCard, FileText, Gift, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import * as endpoints from '../api/endpoints'
import type { Billing, Plan, PlanCode } from '../api/types'
import { PlanCards } from '../components/PlanCards'
import { daysUntil, formatInr } from '../lib/plans'
import { openCheckout } from '../lib/razorpay'
import '../styles/billing.css'
import { useFeatures } from '../hooks/useFeatures'
import { Navigate } from 'react-router'

const day = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }) : '—'


const STATUS_TEXT: Record<string, string> = {
  active: 'Active',
  authenticated: 'Starting',
  pending: 'Payment retrying',
  halted: 'Payment failed',
  cancelled: 'Cancelled',
  completed: 'Ended',
}

function StatusCard({ b, plans }: { b: Billing; plans: Plan[] }) {
  const name = (code: PlanCode | null | undefined) => plans.find((p) => p.code === code)?.name ?? code
  const sub = b.subscription
  const left = daysUntil(b.current.trial_ends_at)

  return (
    <section className="card billing-status">
      <div className="billing-status-main">
        <span className="row-icon">{b.current.source === 'trial' ? <Gift size={18} /> : <CreditCard size={18} />}</span>
        <div>
          <p className="muted small">Your plan</p>
          <h2>
            {b.current.plan_name}
            {b.current.source === 'trial' && <span className="chip chip-event">Free trial</span>}
            {sub && <span className={`chip${sub.in_grace ? ' chip-warn' : ''}`}>{STATUS_TEXT[sub.status] ?? sub.status}</span>}
          </h2>
          {b.current.source === 'trial' && (
            <p className="card-sub">
              {left} day{left === 1 ? '' : 's'} left, until {day(b.current.trial_ends_at)}. Choose a plan below to keep Pro features; otherwise you move to Free and keep all your
              profiles and saved stories.
            </p>
          )}
          {b.current.source === 'free' && <p className="card-sub">Your feed shows stories {b.current.features.feed_delay_minutes} minutes after they’re published. Upgrade for real time, Telegram alerts and AI summaries.</p>}
          {sub && !sub.cancel_at_period_end && !sub.in_grace && sub.current_end && (
            <p className="card-sub">
              <CalendarClock size={14} className="inline-icon" /> Renews on {day(sub.current_end)}.
              {sub.scheduled_plan && ` Switches to ${name(sub.scheduled_plan)} then.`}
            </p>
          )}
          {sub?.cancel_at_period_end && <p className="card-sub">Cancelled. You keep {name(sub.plan)} until {day(sub.current_end)}, then move to Free.</p>}
        </div>
      </div>
      {sub?.in_grace && (
        <p className="billing-alert" role="alert">
          <AlertTriangle size={16} /> Your last renewal didn’t go through. Razorpay is retrying, and you keep {name(sub.plan)} for a few days meanwhile. Check your UPI app or card, or
          cancel and subscribe again with another payment method.
        </p>
      )}
    </section>
  )
}

export function BillingPage() {
  const features = useFeatures()
  const queryClient = useQueryClient()
  const plans = useQuery({ queryKey: ['plans'], queryFn: endpoints.plans, staleTime: 10 * 60_000 })
  const billing = useQuery({ queryKey: ['billing'], queryFn: endpoints.billing })
  const [busyPlan, setBusyPlan] = useState<PlanCode | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const refresh = (data?: Billing) => {
    if (data) queryClient.setQueryData(['billing'], data)
    void queryClient.invalidateQueries({ queryKey: ['billing'] })
    void queryClient.invalidateQueries({ queryKey: ['feed'] })
  }
  const fail = (e: unknown) => setMessage({ ok: false, text: e instanceof Error ? e.message : 'Something went wrong.' })

  const subscribe = async (plan: Plan) => {
    setMessage(null)
    setBusyPlan(plan.code)
    try {
      const options = await endpoints.startCheckout(plan.code)
      const result = await openCheckout(options)
      if (!result) return // closed without paying
      const data = await endpoints.verifyCheckout(result)
      refresh(data)
      setMessage({ ok: true, text: `Welcome to ${plan.name}! Your payment went through and every ${plan.name} feature is on.` })
    } catch (e) {
      fail(e)
    } finally {
      setBusyPlan(null)
    }
  }

  const change = useMutation({
    mutationFn: endpoints.changePlan,
    onMutate: (code) => {
      setMessage(null)
      setBusyPlan(code)
    },
    onSuccess: (data, code) => {
      refresh(data)
      const target = plans.data?.find((p) => p.code === code)?.name
      setMessage({ ok: true, text: data.current.plan === code ? `You’re now on ${target}.` : `You’ll switch to ${target} at the end of this billing period.` })
    },
    onError: fail,
    onSettled: () => setBusyPlan(null),
  })
  const cancel = useMutation({
    mutationFn: endpoints.cancelSubscription,
    onSuccess: (data) => {
      refresh(data)
      setMessage({ ok: true, text: 'Your subscription is cancelled. Nothing more will be charged.' })
    },
    onError: fail,
  })
  const openInvoice = async (id: string) => {
    try {
      const { url } = await endpoints.invoiceUrl(id)
      window.open(url, '_blank', 'noopener,noreferrer')
    } catch (e) {
      fail(e)
    }
  }

  const b = billing.data
  const list = plans.data
  if (features.loaded && !features.billing) return <Navigate to="/feed" replace />
  if (!b || !list) return <div className="page"><div className="skeleton" style={{ height: 300, borderRadius: 16 }} /></div>

  const sub = b.subscription
  const live = sub && !sub.cancel_at_period_end && ['active', 'authenticated', 'pending', 'halted'].includes(sub.status)
  const rank = (code: PlanCode) => list.findIndex((p) => p.code === code)

  const action = (plan: Plan) => {
    const isCurrent = plan.code === b.current.plan && b.current.source !== 'trial'
    if (isCurrent) return <button type="button" className="btn btn-secondary btn-block" disabled>Current plan</button>
    if (plan.code === 'free') {
      if (!live) return <button type="button" className="btn btn-ghost btn-block" disabled>{b.current.plan === 'free' ? 'Current plan' : 'After your trial or subscription'}</button>
      return (
        <button
          type="button"
          className="btn btn-ghost btn-block"
          disabled={cancel.isPending}
          onClick={() => window.confirm(`Cancel your subscription? You keep ${b.current.plan_name} until ${day(sub?.current_end)}, then move to Free.`) && cancel.mutate()}
        >
          {cancel.isPending ? 'Cancelling…' : 'Cancel subscription'}
        </button>
      )
    }
    if (!plan.purchasable) return <button type="button" className="btn btn-secondary btn-block" disabled>Not available yet</button>
    if (live) {
      const up = rank(plan.code) > rank(sub!.plan)
      if (sub!.scheduled_plan === plan.code) return <button type="button" className="btn btn-secondary btn-block" disabled>Starts {day(sub!.current_end)}</button>
      return (
        <>
          <button type="button" className={`btn btn-block ${up ? 'btn-primary' : 'btn-secondary'}`} disabled={busyPlan !== null} onClick={() => change.mutate(plan.code)}>
            {busyPlan === plan.code ? 'Switching…' : up ? `Upgrade to ${plan.name}` : `Switch to ${plan.name}`}
          </button>
          <span className="plan-hint">{up ? 'Starts now; the difference is prorated.' : 'From your next billing date.'}</span>
        </>
      )
    }
    return (
      <>
        <button type="button" className={`btn btn-block ${plan.code === 'pro' ? 'btn-primary' : 'btn-secondary'}`} disabled={busyPlan !== null} onClick={() => subscribe(plan)}>
          {busyPlan === plan.code ? 'Opening Razorpay…' : `Choose ${plan.name}`}
        </button>
        <span className="plan-hint">{formatInr(plan.price_paise)} every month. Cancel any time.</span>
      </>
    )
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Account</p>
          <h1>Plan & billing</h1>
        </div>
      </header>

      {!b.configured && (
        <p className="draft-banner">Payments aren’t switched on yet. Until they are, you stay on your trial or the Free plan.</p>
      )}
      <StatusCard b={b} plans={list} />

      {message && (
        <p className={message.ok ? 'status-ok billing-message' : 'auth-error billing-message'} role="status">
          {message.text}
        </p>
      )}

      <section className="billing-plans">
        <PlanCards
          plans={list}
          action={action}
          badge={(plan) =>
            plan.code === b.current.plan ? <span className="plan-badge">{b.current.source === 'trial' ? 'On trial' : 'Current'}</span> : null
          }
          featured={b.current.plan === 'free' || b.current.source === 'trial' ? 'pro' : b.current.plan}
        />
        <p className="secure-note">
          <ShieldCheck size={14} /> Payments are handled by Razorpay. We never see your card or UPI details.
        </p>
      </section>

      <section className="card">
        <h2>Payments</h2>
        {b.payments.length === 0 ? (
          <p className="muted">No payments yet.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th className="num">Amount</th>
                <th>Method</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {b.payments.map((p) => (
                <tr key={p.id}>
                  <td>{day(p.created_at)}</td>
                  <td className="num tabular">{formatInr(p.amount_paise)}</td>
                  <td className="muted">{p.method?.toUpperCase() ?? '—'}</td>
                  <td>
                    <span className={`pill ${p.status === 'captured' ? 'pill-ok' : p.status === 'failed' ? 'pill-error' : 'pill-muted'}`}>{p.status === 'captured' ? 'Paid' : p.status}</span>
                  </td>
                  <td className="row-actions">
                    {p.has_invoice && (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => openInvoice(p.id)}>
                        <FileText size={14} /> Invoice
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
