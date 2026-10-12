import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LogOut, Monitor, Smartphone, Terminal } from 'lucide-react'
import { useNavigate } from 'react-router'
import { setAccessToken } from '../api/client'
import * as endpoints from '../api/endpoints'
import { relativeTime } from '../lib/time'

function describe(userAgent: string | null): { name: string; kind: 'desktop' | 'mobile' | 'script' } {
  if (!userAgent) return { name: 'Unknown device', kind: 'desktop' }
  if (/python-httpx|curl|python-requests|PostmanRuntime/i.test(userAgent)) return { name: 'API client', kind: 'script' }
  const browser = /Edg\//.test(userAgent)
    ? 'Edge'
    : /Chrome\//.test(userAgent)
      ? 'Chrome'
      : /Firefox\//.test(userAgent)
        ? 'Firefox'
        : /Safari\//.test(userAgent)
          ? 'Safari'
          : 'Browser'
  const os = /Windows/.test(userAgent)
    ? 'Windows'
    : /Android/.test(userAgent)
      ? 'Android'
      : /iPhone|iPad/.test(userAgent)
        ? 'iOS'
        : /Mac OS X/.test(userAgent)
          ? 'macOS'
          : /Linux/.test(userAgent)
            ? 'Linux'
            : ''
  const mobile = /Android|iPhone|iPad|Mobile/.test(userAgent)
  return { name: os ? `${browser} on ${os}` : browser, kind: mobile ? 'mobile' : 'desktop' }
}

const ICON = { desktop: Monitor, mobile: Smartphone, script: Terminal }

export function DevicesPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const devices = useQuery({ queryKey: ['devices'], queryFn: endpoints.devices })

  const revoke = useMutation({
    mutationFn: endpoints.revokeDevice,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['devices'] }),
  })
  const everywhere = useMutation({
    mutationFn: endpoints.logoutAll,
    onSuccess: () => {
      setAccessToken(null)
      navigate('/login', { replace: true })
    },
  })

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Security</p>
          <h1>Signed-in devices</h1>
          <p className="page-sub">Signing a device out takes effect immediately.</p>
        </div>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => window.confirm('Sign out of every device, including this one?') && everywhere.mutate()}
        >
          <LogOut size={16} /> Sign out everywhere
        </button>
      </header>
      <section className="card">
        {devices.isPending ? (
          <div className="skeleton" style={{ height: 120 }} />
        ) : (
          <ul className="list">
            {devices.data?.map((d) => {
              const device = describe(d.user_agent)
              const Icon = ICON[device.kind]
              return (
                <li key={d.id} className="row">
                  <span className="row-icon">
                    <Icon size={18} />
                  </span>
                  <div className="row-main">
                    <strong>
                      {device.name}
                      {d.current && <span className="tag">This device</span>}
                    </strong>
                    <span className="muted small">
                      Signed in {relativeTime(d.created_at)} · active {relativeTime(d.last_used_at)}
                      {d.ip_address && ` · ${d.ip_address}`}
                    </span>
                  </div>
                  {!d.current && (
                    <button type="button" className="btn btn-danger btn-sm" onClick={() => revoke.mutate(d.id)}>
                      Sign out
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
