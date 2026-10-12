// HTTP client for the API.
//
// The access token lives only in memory (never localStorage), so a script injected into
// the page can't read it from storage. The refresh token is an httpOnly cookie the browser
// sends to /api/auth/* by itself. On a 401 we refresh once and retry; parallel 401s share
// one refresh request.

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

/** Password accepted, but the account uses two-step sign-in: send the code next. */
export interface MfaRequired {
  mfa_required: true
  mfa_token: string
  expires_in: number
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user?: User | null
}

export interface User {
  id: string
  email: string
  full_name: string | null
  phone: string | null
  is_admin: boolean
  email_verified: boolean
  phone_verified: boolean
  marketing_opt_in: boolean
  created_at: string
  last_login_at: string | null
  password_changed_at: string | null
  two_factor_enabled: boolean
  has_password: boolean
}

type Listener = (token: string | null) => void

let accessToken: string | null = null
let refreshing: Promise<string | null> | null = null
const listeners = new Set<Listener>()

export function getAccessToken() {
  return accessToken
}

export function setAccessToken(token: string | null) {
  accessToken = token
  listeners.forEach((l) => l(token))
}

/** Called with the new token after every refresh, or null when the session is gone. */
export function onTokenChange(listener: Listener) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export async function errorFrom(res: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = `Request failed (${res.status})`
  try {
    const body = await res.json()
    const detail = body?.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      code = detail.code ?? code
      message = detail.message ?? message
    } else if (Array.isArray(detail) && detail[0]?.msg) {
      code = 'validation_error'
      // Pydantic prefixes rule failures with "Value error, "; people don't need to see that.
      message = detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, '')).join(' ')
    }
  } catch {
    // not JSON: keep the generic message
  }
  return new ApiError(res.status, code, message)
}

/** Exchange the refresh cookie for a new access token. Resolves to null if signed out. */
export function refreshAccessToken(): Promise<string | null> {
  refreshing ??= fetch('/api/auth/refresh', { method: 'POST', credentials: 'same-origin' })
    .then(async (res) => {
      if (!res.ok) {
        setAccessToken(null)
        return null
      }
      const body = (await res.json()) as TokenResponse
      setAccessToken(body.access_token)
      return body.access_token
    })
    .catch(() => null) // network trouble: keep the current state, caller decides
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

interface RequestOptions {
  method?: string
  body?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
  auth?: boolean
}

function buildUrl(path: string, query?: RequestOptions['query']) {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal, auth = true } = options
  const url = buildUrl(path, query)

  const send = (token: string | null) =>
    fetch(url, {
      method,
      signal,
      credentials: 'same-origin',
      headers: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(auth && token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })

  let res = await send(accessToken)
  if (res.status === 401 && auth) {
    const token = await refreshAccessToken()
    if (token) res = await send(token)
  }
  if (!res.ok) throw await errorFrom(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
