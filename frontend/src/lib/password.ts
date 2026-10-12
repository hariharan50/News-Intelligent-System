// Mirrors backend/app/auth/validation.py password_problems(). The server re-checks; this
// is only so people see what's missing while they type.

export const PASSWORD_MIN = 10

export interface PasswordCheck {
  label: string
  ok: boolean
}

const COMMON = new Set([
  'password123', 'password1234', 'qwerty12345', 'qwertyuiop1', '1234567890a', 'a1234567890',
  'iloveyou123', 'welcome1234', 'admin123456', 'india123456', 'letmein12345', 'passw0rd123',
  'abc123456789', 'monkey123456', 'football123', 'nifty50nifty', 'sensex12345', 'trading12345',
  'stockmarket1',
])

export function passwordChecks(password: string, email = '', name = ''): PasswordCheck[] {
  const lowered = password.toLowerCase()
  const personal = [email.split('@')[0].toLowerCase(), ...name.toLowerCase().split(/\s+/)].filter((p) => p.length >= 4)
  return [
    { label: `At least ${PASSWORD_MIN} characters`, ok: password.length >= PASSWORD_MIN },
    { label: 'A letter and a number', ok: /[a-z]/i.test(password) && /\d/.test(password) },
    {
      label: 'Not common or repetitive',
      ok: password.length > 0 && !COMMON.has(lowered) && new Set(lowered).size > 3,
    },
    { label: "Doesn't contain your name or email", ok: password.length > 0 && !personal.some((p) => lowered.includes(p)) },
  ]
}

/** 0 (empty) to 4 (strong), for the strength bar. */
export function passwordStrength(password: string, checks: PasswordCheck[]): number {
  if (!password) return 0
  const passed = checks.filter((c) => c.ok).length
  if (passed < checks.length) return Math.min(passed, 2)
  return password.length >= 16 || /[^a-z0-9]/i.test(password) ? 4 : 3
}

export function passwordIsAcceptable(password: string, email: string, name: string) {
  return passwordChecks(password, email, name).every((c) => c.ok)
}

/** Light client-side check; the server validates properly (including other countries). */
export function phoneProblem(raw: string): string | null {
  const value = raw.trim()
  if (!value) return 'Enter your phone number.'
  if (/[^\d\s()+.-]/.test(value)) return 'Use digits only, e.g. 98765 43210.'
  const digits = value.replace(/\D/g, '')
  if (value.startsWith('+')) return digits.length >= 8 && digits.length <= 15 ? null : 'Check the number and country code.'
  const local = digits.replace(/^(91|0)(?=\d{10}$)/, '')
  return /^[6-9]\d{9}$/.test(local) ? null : 'Enter a 10-digit Indian mobile number, or start with + for other countries.'
}
