import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { LogoMark } from '../components/Logo'
import { useAuth } from './AuthContext'

export function Splash() {
  return (
    <div className="splash" role="status" aria-label="Loading">
      <LogoMark size={40} />
    </div>
  )
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return <Splash />
  if (status === 'signed-out') {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }
  return <>{children}</>
}
