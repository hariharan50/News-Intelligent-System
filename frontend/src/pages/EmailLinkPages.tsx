// Pages reached from links in our emails: confirm email, forgot password, reset password.
import { ArrowLeft, CheckCircle2, MailCheck, XCircle } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { Logo } from '../components/Logo'
import { PasswordField } from '../components/PasswordField'
import { passwordIsAcceptable } from '../lib/password'
import '../styles/auth.css'

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="legal-page">
      <header className="legal-top">
        <Link to="/" aria-label="News Intelligence home">
          <Logo size={24} />
        </Link>
        <Link to="/login" className="auth-back">
          <ArrowLeft size={16} /> Sign in
        </Link>
      </header>
      <main className="auth-card link-card">{children}</main>
    </div>
  )
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : 'Something went wrong. Please try again.')

// --------------------------------------------------------------------------- verify email

export function VerifyEmailPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const { status, setUser } = useAuth()
  const [state, setState] = useState<{ kind: 'working' } | { kind: 'ok'; email: string } | { kind: 'error'; text: string }>(() =>
    token ? { kind: 'working' } : { kind: 'error', text: 'This link is incomplete. Open the link from the email again.' },
  )
  const started = useRef(false)

  useEffect(() => {
    if (started.current || !token) return // strict mode runs effects twice; the link is single-use
    started.current = true
    endpoints
      .verifyEmail(token)
      .then((r) => setState({ kind: 'ok', email: r.email }))
      .catch((e) => setState({ kind: 'error', text: message(e) }))
  }, [token])

  useEffect(() => {
    if (state.kind === 'ok' && status === 'signed-in') endpoints.me().then(setUser, () => undefined)
  }, [state.kind, status, setUser])

  return (
    <Shell>
      {state.kind === 'working' && <p className="muted">Confirming your email…</p>}
      {state.kind === 'ok' && (
        <>
          <CheckCircle2 size={36} className="status-ok" />
          <h1>Email confirmed</h1>
          <p className="muted">Thanks. {state.email} is confirmed for your account.</p>
          <Link to={status === 'signed-in' ? '/feed' : '/login'} className="btn btn-primary btn-lg btn-block">
            {status === 'signed-in' ? 'Open your feed' : 'Sign in'}
          </Link>
        </>
      )}
      {state.kind === 'error' && (
        <>
          <XCircle size={36} className="status-error" />
          <h1>That link didn’t work</h1>
          <p className="muted">{state.text}</p>
          <p className="muted small">Signed in? Use “Resend email” in the banner at the top of the app to get a fresh link.</p>
          <Link to={status === 'signed-in' ? '/feed' : '/login'} className="btn btn-secondary btn-block">
            {status === 'signed-in' ? 'Back to the app' : 'Sign in'}
          </Link>
        </>
      )}
    </Shell>
  )
}

// --------------------------------------------------------------------------- forgot password

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await endpoints.forgotPassword(email.trim())
      setSent(true)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell>
      {sent ? (
        <>
          <MailCheck size={36} className="status-ok" />
          <h1>Check your email</h1>
          <p className="muted">
            If an account exists for <strong>{email.trim()}</strong>, we’ve sent a link to reset the password. It works for 60 minutes. Check your spam folder if it hasn’t arrived in a
            couple of minutes.
          </p>
          <Link to="/login" className="btn btn-secondary btn-block">
            Back to sign in
          </Link>
        </>
      ) : (
        <>
          <h1>Forgot your password?</h1>
          <p className="muted">Enter the email you signed up with and we’ll send you a link to choose a new one.</p>
          <form onSubmit={submit} className="auth-form">
            <label className="field">
              <span>Email</span>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" placeholder="you@example.com" autoFocus />
            </label>
            {error && (
              <p className="auth-error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
              {busy ? 'Sending…' : 'Send reset link'}
            </button>
          </form>
        </>
      )}
    </Shell>
  )
}

// --------------------------------------------------------------------------- reset password

export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!passwordIsAcceptable(password, '', '')) return setError('Your new password doesn’t meet all the rules yet.')
    if (password !== confirm) return setError('The passwords don’t match.')
    setBusy(true)
    try {
      await endpoints.resetPassword({ token, password, confirm_password: confirm })
      setDone(true)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  if (!token) {
    return (
      <Shell>
        <h1>That link is incomplete</h1>
        <p className="muted">Open the link from the email again, or ask for a new one.</p>
        <Link to="/forgot-password" className="btn btn-primary btn-block">
          Send a new link
        </Link>
      </Shell>
    )
  }

  return (
    <Shell>
      {done ? (
        <>
          <CheckCircle2 size={36} className="status-ok" />
          <h1>Password changed</h1>
          <p className="muted">You’ve been signed out everywhere. Sign in with your new password.</p>
          <Link to="/login" className="btn btn-primary btn-lg btn-block">
            Sign in
          </Link>
        </>
      ) : (
        <>
          <h1>Choose a new password</h1>
          <p className="muted">This signs you out on every device.</p>
          <form onSubmit={submit} className="auth-form">
            <PasswordField label="New password" value={password} onChange={setPassword} autoComplete="new-password" withChecklist />
            <PasswordField
              label="Confirm new password"
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
              error={confirm && confirm !== password && confirm.length >= password.length ? 'The passwords don’t match.' : null}
            />
            {error && (
              <p className="auth-error" role="alert">
                {error}
                {/invalid|expired/i.test(error) && (
                  <>
                    {' '}
                    <Link to="/forgot-password">Send a new link</Link>
                  </>
                )}
              </p>
            )}
            <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
              {busy ? 'Saving…' : 'Set new password'}
            </button>
          </form>
        </>
      )}
    </Shell>
  )
}
