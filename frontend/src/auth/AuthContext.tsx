import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { onTokenChange, refreshAccessToken, setAccessToken, type TokenResponse, type User } from '../api/client'
import * as endpoints from '../api/endpoints'

type Status = 'loading' | 'signed-out' | 'signed-in'

interface AuthState {
  status: Status
  user: User | null
  /** Resolves with an mfaToken when the account needs its authenticator code next. */
  signIn: (email: string, password: string, remember?: boolean) => Promise<{ mfaToken?: string }>
  completeSignIn: (mfaToken: string, code: string) => Promise<void>
  signUp: (body: endpoints.SignupBody) => Promise<void>
  /** Replace the cached user after the account was edited. */
  setUser: (user: User) => void
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

// Refresh a minute before the access token expires.
const REFRESH_EARLY_MS = 60_000
// Only used if a response somehow arrives without expires_in.
const FALLBACK_TTL_SECONDS = 15 * 60

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<Status>('loading')
  const [user, setUser] = useState<User | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const scheduleRefresh = useCallback((expiresInSeconds: number) => {
    window.clearTimeout(timer.current)
    const delay = Math.max(expiresInSeconds * 1000 - REFRESH_EARLY_MS, 10_000)
    timer.current = window.setTimeout(() => {
      void refreshAccessToken()
    }, delay)
  }, [])

  const acceptTokens = useCallback(
    (tokens: TokenResponse) => {
      setAccessToken(tokens.access_token)
      scheduleRefresh(tokens.expires_in)
      if (tokens.user) setUser(tokens.user)
      setStatus('signed-in')
    },
    [scheduleRefresh],
  )

  // Any refresh (scheduled, or after a 401) re-arms the timer; losing the session signs out.
  useEffect(
    () =>
      onTokenChange((token, expiresIn) => {
        if (token) {
          scheduleRefresh(expiresIn || FALLBACK_TTL_SECONDS)
        } else {
          window.clearTimeout(timer.current)
          setUser(null)
          setStatus('signed-out')
          queryClient.clear()
        }
      }),
    [queryClient, scheduleRefresh],
  )

  // On load, the refresh cookie (if any) tells us whether we're still signed in.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const token = await refreshAccessToken()
      if (cancelled) return
      if (!token) {
        setStatus('signed-out')
        return
      }
      try {
        setUser(await endpoints.me())
        setStatus('signed-in')
      } catch {
        setStatus('signed-out')
      }
    })()
    return () => {
      cancelled = true
      window.clearTimeout(timer.current)
    }
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      status,
      user,
      signIn: async (email, password, remember = false) => {
        const result = await endpoints.login(email, password, remember)
        if ('mfa_required' in result) return { mfaToken: result.mfa_token }
        acceptTokens(result)
        return {}
      },
      completeSignIn: async (mfaToken, code) => acceptTokens(await endpoints.loginSecondStep(mfaToken, code)),
      signUp: async (body) => acceptTokens(await endpoints.register(body)),
      setUser,
      signOut: async () => {
        try {
          await endpoints.logout()
        } finally {
          setAccessToken(null)
        }
      },
    }),
    [status, user, acceptTokens],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
