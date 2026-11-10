// Razorpay Checkout: load the script on demand and open it for a subscription.
// The payment result is only a claim until the server verifies its signature.
import type { CheckoutOptions } from '../api/types'

const SCRIPT_URL = 'https://checkout.razorpay.com/v1/checkout.js'

interface RazorpayResponse {
  razorpay_payment_id: string
  razorpay_subscription_id: string
  razorpay_signature: string
}

interface RazorpayInstance {
  open: () => void
  on: (event: string, handler: (response: { error?: { description?: string } }) => void) => void
}

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => RazorpayInstance
  }
}

let loading: Promise<void> | null = null

function loadScript(): Promise<void> {
  if (window.Razorpay) return Promise.resolve()
  loading ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = SCRIPT_URL
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => {
      loading = null
      reject(new Error('Could not load Razorpay. Check your connection or ad blocker and try again.'))
    }
    document.body.appendChild(script)
  })
  return loading
}

/** Opens Checkout. Resolves with Razorpay's response, or null if the person closed it. */
export async function openCheckout(options: CheckoutOptions): Promise<RazorpayResponse | null> {
  await loadScript()
  const Razorpay = window.Razorpay
  if (!Razorpay) throw new Error('Razorpay did not load.')
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#2e6bf0'

  return new Promise((resolve, reject) => {
    const checkout = new Razorpay({
      key: options.key_id,
      subscription_id: options.subscription_id,
      name: 'News Intelligence',
      description: `${options.name} plan, monthly`,
      prefill: options.prefill,
      theme: { color: accent },
      handler: (response: RazorpayResponse) => resolve(response),
      modal: { ondismiss: () => resolve(null), confirm_close: true },
    })
    checkout.on('payment.failed', (response) => {
      reject(new Error(response.error?.description || 'The payment failed. No money was taken; you can try again.'))
    })
    checkout.open()
  })
}
