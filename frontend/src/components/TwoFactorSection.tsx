// My account → Two-step sign-in: set up with an authenticator app, recovery codes, turn off.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, ShieldCheck, ShieldAlert } from 'lucide-react'
import QRCode from 'qrcode'
import { useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { PasswordField } from './PasswordField'

const errorText = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : 'Something went wrong.')

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(codes.join('\n'))
    setCopied(true)
  }
  return (
    <div className="recovery-box">
      <p>
        <strong>Save these recovery codes</strong> somewhere safe (a password manager is ideal). Each one signs you in once if you lose your phone. They won’t be shown again.
      </p>
      <ol className="recovery-codes">
        {codes.map((c) => (
          <li key={c}>
            <code>{c}</code>
          </li>
        ))}
      </ol>
      <div className="head-actions">
        <button type="button" className="btn btn-secondary" onClick={copy}>
          <Copy size={15} /> {copied ? 'Copied' : 'Copy codes'}
        </button>
        <button type="button" className="btn btn-primary" onClick={onDone}>
          I’ve saved them
        </button>
      </div>
    </div>
  )
}

function Setup({ onEnabled }: { onEnabled: (codes: string[]) => void }) {
  const [setup, setSetup] = useState<{ secret: string; otpauth_url: string } | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)

  const start = useMutation({
    mutationFn: endpoints.twoFactorSetup,
    onSuccess: setSetup,
    onError: (e) => setError(errorText(e)),
  })
  const enable = useMutation({
    mutationFn: () => endpoints.twoFactorEnable(code),
    onSuccess: (r) => onEnabled(r.recovery_codes),
    onError: (e) => setError(errorText(e)),
  })

  useEffect(() => {
    if (!setup) return
    let cancelled = false
    QRCode.toDataURL(setup.otpauth_url, { margin: 1, width: 180 }).then((url) => !cancelled && setQr(url), () => undefined)
    return () => {
      cancelled = true
    }
  }, [setup])

  if (!setup) {
    return (
      <>
        {error && <p className="auth-error">{error}</p>}
        <button type="button" className="btn btn-primary" onClick={() => start.mutate()} disabled={start.isPending}>
          <ShieldCheck size={16} /> {start.isPending ? 'Starting…' : 'Turn on two-step sign-in'}
        </button>
      </>
    )
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    enable.mutate()
  }
  return (
    <div className="tfa-setup">
      <ol className="tfa-steps">
        <li>Open an authenticator app on your phone: Google Authenticator, Microsoft Authenticator, Authy or 1Password.</li>
        <li>Scan this QR code, or type the key by hand.</li>
      </ol>
      <div className="tfa-qr">
        {qr ? <img src={qr} alt="QR code for your authenticator app" width={180} height={180} /> : <div className="skeleton" style={{ width: 180, height: 180 }} />}
        <div>
          <p className="muted small">Key</p>
          <code className="tfa-secret">{setup.secret.replace(/(.{4})/g, '$1 ').trim()}</code>
        </div>
      </div>
      <form className="account-form" onSubmit={submit}>
        <label className="field">
          <span>3. Enter the 6-digit code the app shows</span>
          <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} placeholder="123456" className="code-input" />
        </label>
        {error && <p className="auth-error">{error}</p>}
        <div className="head-actions">
          <button type="submit" className="btn btn-primary" disabled={enable.isPending || code.replace(/\D/g, '').length !== 6}>
            {enable.isPending ? 'Checking…' : 'Turn on'}
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => setSetup(null)}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  )
}

function Manage({ codesLeft, onCodes }: { codesLeft: number; onCodes: (codes: string[]) => void }) {
  const queryClient = useQueryClient()
  const { user, setUser } = useAuth()
  const [mode, setMode] = useState<'idle' | 'codes' | 'off'>('idle')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const newCodes = useMutation({
    mutationFn: () => endpoints.twoFactorNewCodes(code),
    onSuccess: (r) => {
      onCodes(r.recovery_codes)
      setMode('idle')
    },
    onError: (e) => setError(errorText(e)),
  })
  const disable = useMutation({
    mutationFn: () => endpoints.twoFactorDisable(password, code),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['2fa'] })
      if (user) setUser({ ...user, two_factor_enabled: false })
    },
    onError: (e) => setError(errorText(e)),
  })

  if (mode === 'idle') {
    return (
      <div className="head-actions">
        <button type="button" className="btn btn-secondary" onClick={() => setMode('codes')}>
          New recovery codes
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setMode('off')}>
          Turn off
        </button>
        <span className={`muted small${codesLeft <= 2 ? ' status-error' : ''}`}>{codesLeft} recovery codes left</span>
      </div>
    )
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (mode === 'codes') newCodes.mutate()
    else disable.mutate()
  }
  return (
    <form className="account-form" onSubmit={submit}>
      {mode === 'off' && <PasswordField label="Your password" value={password} onChange={setPassword} autoComplete="current-password" />}
      <label className="field">
        <span>Code from your app (or a recovery code)</span>
        <input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" maxLength={20} className="code-input" />
      </label>
      {error && <p className="auth-error">{error}</p>}
      <div className="head-actions">
        <button type="submit" className={`btn ${mode === 'off' ? 'btn-danger-solid' : 'btn-primary'}`} disabled={code.trim().length < 6 || (mode === 'off' && !password)}>
          {mode === 'off' ? 'Turn off two-step sign-in' : 'Create new codes'}
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setMode('idle')}>
          Cancel
        </button>
      </div>
    </form>
  )
}

export function TwoFactorSection() {
  const queryClient = useQueryClient()
  const { user, setUser } = useAuth()
  const state = useQuery({ queryKey: ['2fa'], queryFn: endpoints.twoFactor })
  const [codes, setCodes] = useState<string[] | null>(null)
  const s = state.data

  const enabled = (newCodes: string[]) => {
    setCodes(newCodes)
    void queryClient.invalidateQueries({ queryKey: ['2fa'] })
    if (user) setUser({ ...user, two_factor_enabled: true })
  }

  return (
    <section className="card">
      <div className="setting-head">
        <span className="row-icon">{s?.enabled ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}</span>
        <div>
          <h2>Two-step sign-in</h2>
          <p className="card-sub">
            {s?.enabled
              ? `On since ${new Date(s.enabled_at ?? '').toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}. Signing in needs your password and a code from your phone.`
              : 'Protect your account with a code from an authenticator app as well as your password.'}
            {s?.required && !s.enabled && <strong> Required for administrators.</strong>}
          </p>
        </div>
      </div>
      {codes ? (
        <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />
      ) : s?.enabled ? (
        <Manage codesLeft={s.recovery_codes_left} onCodes={setCodes} />
      ) : s ? (
        <Setup onEnabled={enabled} />
      ) : null}
    </section>
  )
}
