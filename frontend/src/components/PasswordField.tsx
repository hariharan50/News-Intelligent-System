import { Check, Circle, Eye, EyeOff } from 'lucide-react'
import { useId, useState } from 'react'
import { passwordChecks, passwordStrength } from '../lib/password'

const STRENGTH = ['', 'Weak', 'Weak', 'Good', 'Strong'] as const

interface Props {
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete: string
  placeholder?: string
  /** Show the live rules checklist and strength bar (new passwords). */
  withChecklist?: boolean
  email?: string
  name?: string
  error?: string | null
}

export function PasswordField({ label, value, onChange, autoComplete, placeholder, withChecklist, email = '', name = '', error }: Props) {
  const [visible, setVisible] = useState(false)
  const id = useId()
  const checks = passwordChecks(value, email, name)
  const strength = passwordStrength(value, checks)

  return (
    <div className="field">
      <label htmlFor={id}>
        <span>{label}</span>
      </label>
      <span className="password-wrap">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          placeholder={placeholder}
          maxLength={128}
          aria-invalid={Boolean(error)}
          aria-describedby={withChecklist ? `${id}-rules` : undefined}
        />
        <button type="button" className="icon-btn password-toggle" onClick={() => setVisible((v) => !v)} aria-label={visible ? 'Hide password' : 'Show password'}>
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </span>
      {withChecklist && (
        <div id={`${id}-rules`} className="password-rules">
          <div className={`strength strength-${strength}`} aria-label={`Password strength: ${STRENGTH[strength] || 'empty'}`}>
            <span />
            <span />
            <span />
            <span />
            <em>{STRENGTH[strength]}</em>
          </div>
          <ul>
            {checks.map((c) => (
              <li key={c.label} className={c.ok ? 'ok' : ''}>
                {c.ok ? <Check size={13} /> : <Circle size={11} />} {c.label}
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <span className="field-error">{error}</span>}
    </div>
  )
}
