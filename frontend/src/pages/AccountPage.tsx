import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, BadgeCheck, Link2, CalendarDays, Clock, KeyRound, Mail, MonitorSmartphone, Phone, Sparkles, Trash2, UserRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError, setAccessToken } from '../api/client'
import * as endpoints from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { PasswordField } from '../components/PasswordField'
import { TwoFactorSection } from '../components/TwoFactorSection'
import { passwordIsAcceptable, phoneProblem } from '../lib/password'
import { relativeTime } from '../lib/time'
import { useFeatures } from '../hooks/useFeatures'

function initials(name: string | null | undefined, email: string | undefined) {
  const parts = (name || email || '?').trim().split(/[\s@._-]+/).filter(Boolean)
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase()
}

const longDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }) : '—'

function Details() {
  const { setUser } = useAuth()
  const queryClient = useQueryClient()
  const account = useQuery({ queryKey: ['account'], queryFn: endpoints.account })
  const features = useFeatures()
  const billing = useQuery({ queryKey: ['billing'], queryFn: endpoints.billing, enabled: features.billing })
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => endpoints.updateAccount({ full_name: name.trim(), phone }),
    onSuccess: (user) => {
      queryClient.setQueryData(['account'], user)
      setUser(user)
      setEditing(false)
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not save.'),
  })
  const marketing = useMutation({
    mutationFn: (value: boolean) => endpoints.updateAccount({ marketing_opt_in: value }),
    onSuccess: (user) => {
      queryClient.setQueryData(['account'], user)
      setUser(user)
    },
  })

  const user = account.data
  if (!user) return <div className="skeleton" style={{ height: 260, borderRadius: 16 }} />

  const startEdit = () => {
    setName(user.full_name ?? '')
    setPhone(user.phone ?? '')
    setError(null)
    setEditing(true)
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const problem = name.trim().length < 2 ? 'Enter your name.' : phoneProblem(phone)
    if (problem) return setError(problem)
    save.mutate()
  }

  return (
    <>
      <section className="account-hero">
        <span className="account-avatar">{initials(user.full_name, user.email)}</span>
        <div>
          <h1>{user.full_name || user.email.split('@')[0]}</h1>
          <p className="muted">{user.email}</p>
          <div className="profile-meta" style={{ marginTop: 10 }}>
            {features.billing && (
              <Link to="/billing" className="chip chip-event">
                {billing.data ? `${billing.data.current.plan_name}${billing.data.current.source === 'trial' ? ' trial' : ' plan'}` : 'Plan'}
              </Link>
            )}
            {user.is_admin && <span className="chip">Administrator</span>}
            <span className="chip">
              <CalendarDays size={12} /> Member since {longDate(user.created_at)}
            </span>
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card-title-row">
          <h2>Personal details</h2>
          {!editing && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={startEdit}>
              Edit
            </button>
          )}
        </div>
        <p className="card-sub">Used to identify your account and to reach you about it.</p>

        {editing ? (
          <form className="account-form" onSubmit={submit}>
            <label className="field">
              <span>Full name</span>
              <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} autoComplete="name" autoFocus />
            </label>
            <label className="field">
              <span>Phone number</span>
              <input type="tel" value={phone} maxLength={32} onChange={(e) => setPhone(e.target.value)} autoComplete="tel" placeholder="98765 43210" />
              <span className="field-hint">Indian numbers: just the 10 digits. Others: start with + and the country code.</span>
            </label>
            {error && <p className="auth-error">{error}</p>}
            <div className="head-actions">
              <button type="submit" className="btn btn-primary" disabled={save.isPending}>
                {save.isPending ? 'Saving…' : 'Save changes'}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <dl className="detail-list">
            <div>
              <dt>
                <UserRound size={15} /> Name
              </dt>
              <dd>{user.full_name || <span className="muted">Not set</span>}</dd>
            </div>
            <div>
              <dt>
                <Mail size={15} /> Email
              </dt>
              <dd>
                {user.email}{' '}
                {user.email_verified ? (
                  <span className="verify ok">
                    <BadgeCheck size={13} /> Verified
                  </span>
                ) : (
                  <span className="verify">Not verified yet</span>
                )}
              </dd>
            </div>
            <div>
              <dt>
                <Phone size={15} /> Phone
              </dt>
              <dd>
                {user.phone || <span className="muted">Not set</span>}{' '}
                {user.phone && !user.phone_verified && <span className="verify">Not verified yet</span>}
              </dd>
            </div>
            <div>
              <dt>
                <Clock size={15} /> Last sign-in
              </dt>
              <dd>{relativeTime(user.last_login_at)}</dd>
            </div>
          </dl>
        )}
        <label className="switch-row" style={{ marginTop: 16 }}>
          <input type="checkbox" checked={user.marketing_opt_in} onChange={(e) => marketing.mutate(e.target.checked)} />
          <span>
            Send me occasional product updates <span className="muted small">(optional)</span>
          </span>
        </label>
      </section>
    </>
  )
}

function ChangePassword() {
  const { user } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const change = useMutation({
    mutationFn: () => endpoints.changePassword({ current_password: current, new_password: next, confirm_password: confirm }),
    onSuccess: ({ other_devices_signed_out: n }) => {
      setCurrent('')
      setNext('')
      setConfirm('')
      setMessage({ ok: true, text: `Password changed.${n ? ` ${n} other device${n > 1 ? 's were' : ' was'} signed out.` : ''}` })
    },
    onError: (e) => setMessage({ ok: false, text: e instanceof ApiError ? e.message : 'Could not change the password.' }),
  })

  const strongEnough = passwordIsAcceptable(next, user?.email ?? '', user?.full_name ?? '')
  if (user && !user.has_password) {
    return (
      <section className="card">
        <div className="setting-head">
          <span className="row-icon">
            <KeyRound size={18} />
          </span>
          <div>
            <h2>Password</h2>
            <p className="card-sub">
              You sign in with Google, so this account has no password yet. To add one, sign out and use
              “Forgot password” with {user.email}. We’ll email you a link.
            </p>
          </div>
        </div>
      </section>
    )
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setMessage(null)
    if (!strongEnough) return setMessage({ ok: false, text: 'Your new password doesn’t meet all the rules yet.' })
    if (next !== confirm) return setMessage({ ok: false, text: 'The new passwords don’t match.' })
    change.mutate()
  }

  return (
    <section className="card">
      <div className="setting-head">
        <span className="row-icon">
          <KeyRound size={18} />
        </span>
        <div>
          <h2>Password</h2>
          <p className="card-sub">
            Last changed {relativeTime(user?.password_changed_at ?? null)}. Changing it signs out your other devices.
          </p>
        </div>
      </div>
      <form className="account-form" onSubmit={submit}>
        <PasswordField label="Current password" value={current} onChange={setCurrent} autoComplete="current-password" />
        <PasswordField label="New password" value={next} onChange={setNext} autoComplete="new-password" withChecklist email={user?.email} name={user?.full_name ?? ''} />
        <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password" error={confirm && confirm !== next && confirm.length >= next.length ? 'The passwords don’t match.' : null} />
        {message && <p className={message.ok ? 'status-ok' : 'auth-error'}>{message.text}</p>}
        <div className="head-actions">
          <button type="submit" className="btn btn-primary" disabled={change.isPending || !current || !next || !confirm}>
            {change.isPending ? 'Changing…' : 'Change password'}
          </button>
          <Link to="/settings/devices" className="btn btn-ghost">
            <MonitorSmartphone size={16} /> Signed-in devices
          </Link>
        </div>
      </form>
    </section>
  )
}

function SignInMethods() {
  const { user } = useAuth()
  const features = useFeatures()
  const queryClient = useQueryClient()
  const methods = useQuery({ queryKey: ['sign-in-methods'], queryFn: endpoints.signInMethods })
  const disconnect = useMutation({
    mutationFn: endpoints.disconnectGoogle,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sign-in-methods'] }),
  })
  const google = methods.data?.google
  if (!methods.data || (!google && !features.google)) return null

  return (
    <section className="card">
      <div className="setting-head">
        <span className="row-icon">
          <Link2 size={18} />
        </span>
        <div>
          <h2>Sign-in methods</h2>
          <p className="card-sub">Ways you can get into this account.</p>
        </div>
      </div>
      <ul className="method-list">
        <li>
          <KeyRound size={16} />
          <div>
            <strong>Email and password</strong>
            <span className="muted">{methods.data.password ? user?.email : 'Not set up'}</span>
          </div>
        </li>
        <li>
          <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
            <path fill="#4285F4" d="M44 24c0-1.3-.1-2.4-.4-3.5H24v8h11.3c-.5 2.6-2 4.8-4.1 6.2l6.2 5.2C41.5 36.2 44 30.6 44 24z" />
            <path fill="#34A853" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
            <path fill="#FBBC05" d="M12.7 28.1A12 12 0 0 1 12 24c0-1.4.3-2.8.7-4.1l-6.4-5C4.8 17.6 4 20.7 4 24s.8 6.4 2.3 9.1l6.4-5z" />
            <path fill="#EA4335" d="M24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.9l6.4 5C14.4 15.3 18.8 12 24 12z" />
          </svg>
          <div>
            <strong>Google</strong>
            <span className="muted">
              {google
                ? `${google.email} · connected ${relativeTime(google.connected_at)}${google.last_used_at ? ` · last used ${relativeTime(google.last_used_at)}` : ''}`
                : 'Not connected'}
            </span>
          </div>
          {google ? (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!methods.data.password || disconnect.isPending}
              title={methods.data.password ? undefined : 'Set a password first, or you couldn’t sign in at all'}
              onClick={() => disconnect.mutate()}
            >
              {disconnect.isPending ? 'Disconnecting…' : 'Disconnect'}
            </button>
          ) : null}
        </li>
      </ul>
      {google === null && features.google && (
        <p className="card-sub">
          To connect Google, sign out and press “Continue with Google”. A Google account with the same verified
          email ({user?.email}) is linked automatically.
        </p>
      )}
      {disconnect.error && <p className="auth-error">{(disconnect.error as ApiError).message}</p>}
    </section>
  )
}

function DeleteAccount() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [typed, setTyped] = useState('')
  const remove = useMutation({
    mutationFn: () => endpoints.deleteAccount(password),
    onSuccess: () => {
      // Leave the protected page first; clearing the session there would redirect to sign-in.
      navigate('/', { replace: true })
      setAccessToken(null)
    },
  })

  return (
    <section className="card danger-card">
      <div className="setting-head">
        <span className="row-icon danger-icon">
          <AlertTriangle size={18} />
        </span>
        <div>
          <h2>Delete account</h2>
          <p className="card-sub">
            Permanently deletes your account, profiles, alerts, saved stories and Telegram connection. This can’t be undone.
          </p>
        </div>
      </div>
      {user && !user.has_password ? (
        <p className="card-sub">To delete it, first set a password (sign out and use “Forgot password”), so we can confirm it’s you.</p>
      ) : !open ? (
        <button type="button" className="btn btn-danger" onClick={() => setOpen(true)}>
          <Trash2 size={16} /> Delete my account…
        </button>
      ) : (
        <form
          className="account-form"
          onSubmit={(e) => {
            e.preventDefault()
            remove.mutate()
          }}
        >
          <PasswordField label="Your password" value={password} onChange={setPassword} autoComplete="current-password" />
          <label className="field">
            <span>
              Type <strong>DELETE</strong> to confirm
            </span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </label>
          {remove.error && <p className="auth-error">{(remove.error as ApiError).message}</p>}
          <div className="head-actions">
            <button type="submit" className="btn btn-danger-solid" disabled={typed !== 'DELETE' || !password || remove.isPending}>
              {remove.isPending ? 'Deleting…' : 'Permanently delete'}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  )
}

export function AccountPage() {
  return (
    <div className="page">
      <p className="page-eyebrow">
        <Sparkles size={13} style={{ display: 'inline', verticalAlign: '-2px' }} /> My account
      </p>
      <Details />
      <SignInMethods />
      <ChangePassword />
      <TwoFactorSection />
      <DeleteAccount />
    </div>
  )
}
