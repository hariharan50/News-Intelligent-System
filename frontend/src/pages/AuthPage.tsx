import { ArrowLeft, Check, Eye, EyeOff, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { GoogleButton, googleErrorMessage } from '../components/GoogleButton'
import { Logo } from '../components/Logo'
import { PasswordField } from '../components/PasswordField'
import { ScoreRing } from '../components/TierBadge'
import { passwordIsAcceptable, phoneProblem } from '../lib/password'
import '../styles/auth.css'

const BENEFITS = [
  'A ranked feed of RBI, SEBI, Fed and market news',
  'Every story explains why it is in front of you',
  'Alerts the moment something crosses your line',
]

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function useNext() {
  const location = useLocation()
  const raw = new URLSearchParams(location.search).get('next') || '/feed'
  return { next: raw.startsWith('/') && !raw.startsWith('//') ? raw : '/feed', search: location.search }
}

// --------------------------------------------------------------------------- sign in

function SignInForm() {
  const { signIn, completeSignIn } = useAuth()
  const navigate = useNavigate()
  const { next, search } = useNext()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [params] = useSearchParams()
  // After Google, a 2FA account comes back with its challenge in the URL fragment
  // (never sent to any server). Read it once, then clear it from the address bar.
  const [mfaToken, setMfaToken] = useState<string | null>(() => {
    const match = window.location.hash.match(/^#mfa=([\w-]+)$/)
    if (match) window.history.replaceState(null, '', window.location.pathname + window.location.search)
    return match ? match[1] : null
  })
  const [code, setCode] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  // Off by default: the refresh cookie then dies with the browser, so reopening it asks
  // for the password instead of walking straight into the app.
  const [remember, setRemember] = useState(false)
  const [error, setError] = useState<string | null>(() => googleErrorMessage(params.get('sso_error')))
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mfaToken) {
        await completeSignIn(mfaToken, code)
      } else {
        const { mfaToken: token } = await signIn(email, password, remember)
        if (token) {
          setMfaToken(token)
          return
        }
      }
      navigate(next, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.code === 'mfa_expired') {
        setMfaToken(null)
        setCode('')
      }
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  if (mfaToken) {
    return (
      <>
        <h1>Two-step sign-in</h1>
        <p className="muted">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
        <form onSubmit={submit} className="auth-form">
          <label className="field">
            <span>Code</span>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode="text"
              autoComplete="one-time-code"
              placeholder="123 456"
              maxLength={20}
              autoFocus
              className="code-input"
            />
          </label>
          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy || code.trim().length < 6}>
            {busy ? 'Checking…' : 'Sign in'}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-block"
            onClick={() => {
              setMfaToken(null)
              setCode('')
              setError(null)
            }}
          >
            Use a different account
          </button>
        </form>
      </>
    )
  }

  return (
    <>
      <h1>Welcome back</h1>
      <p className="muted">Sign in to see what moved while you were away.</p>
      <div className="sso-block">
        <GoogleButton next={next} />
      </div>
      <form onSubmit={submit} className="auth-form after-sso">
        <label className="field">
          <span>Email</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" placeholder="you@example.com" autoFocus />
        </label>
        <label className="field">
          <span className="field-label-row">
            Password
            <Link to="/forgot-password" className="field-link">
              Forgot password?
            </Link>
          </span>
          <span className="password-wrap">
            <input
              type={showPassword ? 'text' : 'password'}
              required
              maxLength={128}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              placeholder="Your password"
            />
            <button type="button" className="icon-btn password-toggle" onClick={() => setShowPassword((v) => !v)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </span>
        </label>
        <label className="field-check">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          <span>
            Keep me signed in
            <small>Leave this off on a shared or public computer.</small>
          </span>
        </label>
        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      <p className="auth-switch">
        New to News Intelligence? <Link to={`/signup${search}`}>Create a free account</Link>
      </p>
    </>
  )
}

// --------------------------------------------------------------------------- sign up

interface SignupForm {
  full_name: string
  phone: string
  email: string
  password: string
  confirm_password: string
  accept_terms: boolean
  accept_risk_notice: boolean
  marketing_opt_in: boolean
}

type Field = keyof SignupForm

function signupErrors(f: SignupForm): Partial<Record<Field, string>> {
  const errors: Partial<Record<Field, string>> = {}
  const name = f.full_name.trim()
  if (name.length < 2) errors.full_name = 'Enter your name.'
  else if (/[<>{}[\]\\/@]|https?:/i.test(name)) errors.full_name = "Names can't contain links or special characters."
  const phone = phoneProblem(f.phone)
  if (phone) errors.phone = phone
  if (!EMAIL_RE.test(f.email.trim())) errors.email = 'Enter a valid email address.'
  if (!passwordIsAcceptable(f.password, f.email, f.full_name)) errors.password = 'Your password doesn’t meet all the rules yet.'
  if (!f.confirm_password) errors.confirm_password = 'Type your password again.'
  else if (f.confirm_password !== f.password) errors.confirm_password = 'The passwords don’t match.'
  if (!f.accept_terms) errors.accept_terms = 'You need to accept the Terms and Privacy Policy.'
  if (!f.accept_risk_notice) errors.accept_risk_notice = 'Please confirm you understand this.'
  return errors
}

function SignUpForm() {
  const { signUp } = useAuth()
  const navigate = useNavigate()
  const { next, search } = useNext()
  const [form, setForm] = useState<SignupForm>({
    full_name: '',
    phone: '',
    email: '',
    password: '',
    confirm_password: '',
    accept_terms: false,
    accept_risk_notice: false,
    marketing_opt_in: false,
  })
  // Show a field's error only once the person has left it (or tried to submit).
  const [touched, setTouched] = useState<Partial<Record<Field, boolean>>>({})
  const [submitted, setSubmitted] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const [ssoNotice] = useState(() =>
    new URLSearchParams(window.location.search).get('sso') === 'needs_consent'
      ? 'To create your account with Google, tick the two agreements below, then press “Sign up with Google” again.'
      : null,
  )
  // Google needs only the agreements here: Google supplies the name and verified email.
  const googleReady = () => {
    if (form.accept_terms && form.accept_risk_notice) return true
    // Replace, not merge: the click blurred the name field, which isn't needed for Google.
    setTouched({ accept_terms: true, accept_risk_notice: true })
    document.querySelector('.consents')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    return false
  }
  const [busy, setBusy] = useState(false)

  const errors = signupErrors(form)
  const show = (field: Field) => (submitted || touched[field] ? errors[field] : undefined)
  const set = <K extends Field>(field: K, value: SignupForm[K]) => setForm((f) => ({ ...f, [field]: value }))
  const blur = (field: Field) => () => setTouched((t) => ({ ...t, [field]: true }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSubmitted(true)
    setServerError(null)
    if (Object.keys(errors).length > 0) {
      const first = document.querySelector<HTMLElement>('[aria-invalid="true"]')
      first?.focus()
      return
    }
    setBusy(true)
    try {
      await signUp({ ...form, full_name: form.full_name.trim(), email: form.email.trim() })
      navigate(next === '/feed' ? '/feed?welcome=1' : next, { replace: true })
    } catch (err) {
      setServerError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h1>Create your account</h1>
      <p className="muted">Your trader profile is ready the moment you sign up.</p>
      {ssoNotice && (
        <p className="auth-error" role="status">
          {ssoNotice}
        </p>
      )}
      <div className="sso-block">
        <GoogleButton next={next} consent={form.accept_terms && form.accept_risk_notice} onBeforeStart={googleReady} label="Sign up with Google" />
        <p className="sso-hint">With Google you only need to tick the two agreements at the bottom first.</p>
      </div>
      <form onSubmit={submit} className="auth-form" noValidate>
        <div className="field-row">
          <label className="field">
            <span>Full name</span>
            <input value={form.full_name} onChange={(e) => set('full_name', e.target.value)} onBlur={blur('full_name')} autoComplete="name" maxLength={120} placeholder="Asha Rao" aria-invalid={Boolean(show('full_name'))} autoFocus />
            {show('full_name') && <span className="field-error">{show('full_name')}</span>}
          </label>
          <label className="field">
            <span>Phone number</span>
            <span className={`phone-wrap${form.phone.trim().startsWith('+') ? ' no-prefix' : ''}`}>
              {!form.phone.trim().startsWith('+') && (
                <span className="phone-prefix" aria-hidden>
                  +91
                </span>
              )}
              <input
                type="tel"
                inputMode="tel"
                value={form.phone}
                onChange={(e) => set('phone', e.target.value)}
                onBlur={blur('phone')}
                autoComplete="tel"
                maxLength={32}
                placeholder="98765 43210"
                aria-invalid={Boolean(show('phone'))}
                aria-describedby="phone-hint"
              />
            </span>
            {show('phone') ? (
              <span className="field-error">{show('phone')}</span>
            ) : (
              <span id="phone-hint" className="field-hint">
                Outside India? Start with your country code, like +44.
              </span>
            )}
          </label>
        </div>

        <label className="field">
          <span>Email</span>
          <input type="email" value={form.email} onChange={(e) => set('email', e.target.value)} onBlur={blur('email')} autoComplete="email" placeholder="you@example.com" aria-invalid={Boolean(show('email'))} />
          {show('email') && <span className="field-error">{show('email')}</span>}
        </label>

        <PasswordField
          label="Password"
          value={form.password}
          onChange={(v) => set('password', v)}
          autoComplete="new-password"
          placeholder="Create a password"
          withChecklist
          email={form.email}
          name={form.full_name}
          error={submitted ? errors.password : null}
        />
        <PasswordField
          label="Confirm password"
          value={form.confirm_password}
          onChange={(v) => {
            set('confirm_password', v)
            setTouched((t) => ({ ...t, confirm_password: true }))
          }}
          autoComplete="new-password"
          placeholder="Type it again"
          error={show('confirm_password') && (submitted || form.confirm_password.length >= form.password.length) ? show('confirm_password') : null}
        />

        <fieldset className="consents">
          <legend className="visually-hidden">Agreements</legend>
          <label className={`consent${show('accept_terms') ? ' has-error' : ''}`}>
            <input type="checkbox" checked={form.accept_terms} onChange={(e) => set('accept_terms', e.target.checked)} aria-invalid={Boolean(show('accept_terms'))} />
            <span>
              I agree to the{' '}
              <Link to="/terms" target="_blank">
                Terms of Service
              </Link>{' '}
              and{' '}
              <Link to="/privacy" target="_blank">
                Privacy Policy
              </Link>
              . <em className="required">Required</em>
            </span>
          </label>
          <label className={`consent${show('accept_risk_notice') ? ' has-error' : ''}`}>
            <input type="checkbox" checked={form.accept_risk_notice} onChange={(e) => set('accept_risk_notice', e.target.checked)} aria-invalid={Boolean(show('accept_risk_notice'))} />
            <span>
              I understand News Intelligence ranks news and is <strong>not investment advice</strong>. <em className="required">Required</em>
            </span>
          </label>
          <label className="consent">
            <input type="checkbox" checked={form.marketing_opt_in} onChange={(e) => set('marketing_opt_in', e.target.checked)} />
            <span>Send me occasional product updates. (Optional, you can change this any time.)</span>
          </label>
        </fieldset>

        {serverError && (
          <p className="auth-error" role="alert">
            {serverError}
          </p>
        )}
        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy ? 'Creating your account…' : 'Create account'}
        </button>
        <p className="secure-note">
          <ShieldCheck size={14} /> Your password is stored only as a one-way hash. We never see it.
        </p>
      </form>
      <p className="auth-switch">
        Already have an account? <Link to={`/login${search}`}>Sign in</Link>
      </p>
    </>
  )
}

// --------------------------------------------------------------------------- page

export function AuthPage({ mode }: { mode: 'signin' | 'signup' }) {
  const { status, user } = useAuth()
  const { next } = useNext()
  // Asking to sign in always shows the form. A live session is offered, never taken: the
  // old behaviour redirected straight into the app, so a browser holding a month-old
  // refresh cookie walked in without anyone typing a password.
  const resume = status === 'signed-in' ? next : null

  return (
    <div className="auth-page">
      <aside className="auth-brand" aria-hidden="true">
        <div className="auth-brand-glow" />
        <Link to="/" className="auth-brand-logo" tabIndex={-1}>
          <Logo />
        </Link>
        <div className="auth-brand-copy">
          <h2>Signal, not noise.</h2>
          <p>The market-moving news for Indian traders, ranked and explained.</p>
          <ul>
            {BENEFITS.map((b) => (
              <li key={b}>
                <span className="auth-check">
                  <Check size={14} />
                </span>
                {b}
              </li>
            ))}
          </ul>
        </div>
        <div className="auth-sample">
          <ScoreRing score={95} tier="critical" size={48} />
          <div>
            <span className="tier tier-critical">
              <span className="tier-dot" /> Critical · Alert
            </span>
            <strong>RBI keeps repo rate at 5.50%, retains neutral stance</strong>
            <span className="auth-sample-why">Pinned: RBI rate decision · Keyword: repo rate</span>
          </div>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-top">
          <Link to="/" className="auth-back">
            <ArrowLeft size={16} /> Home
          </Link>
          <span className="auth-mobile-logo">
            <Logo size={24} />
          </span>
        </div>
        <div className={`auth-card${mode === 'signup' ? ' auth-card-wide' : ''}`}>
          {resume && (
            <p className="auth-resume" role="status">
              <ShieldCheck size={15} />
              <span>
                You still have a session open{user?.email ? ` as ${user.email}` : ''}.{' '}
                <Link to={resume} replace>
                  Continue to the app
                </Link>
                , or sign in below as someone else.
              </span>
            </p>
          )}
          {mode === 'signin' ? <SignInForm /> : <SignUpForm />}
        </div>
        <p className="auth-legal">Not investment advice. We show headlines and link to the original publishers.</p>
      </main>
    </div>
  )
}
