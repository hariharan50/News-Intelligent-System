import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, CheckCircle2, Moon, Send, Unlink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import type { NotificationSettings } from '../api/types'
import { relativeTime } from '../lib/time'

const TIMEZONES = ['Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Europe/London', 'America/New_York', 'UTC']

const toTime = (minutes: number | null) =>
  minutes === null ? '' : `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
const toMinutes = (value: string) => {
  const [h, m] = value.split(':').map(Number)
  return h * 60 + m
}

function BrowserNotifications() {
  const supported = typeof Notification !== 'undefined'
  const [permission, setPermission] = useState(supported ? Notification.permission : 'denied')

  return (
    <section className="card">
      <div className="setting-head">
        <span className="row-icon">
          <BellRing size={18} />
        </span>
        <div>
          <h2>Browser notifications</h2>
          <p className="card-sub">A system notification when an alert arrives while this tab is in the background.</p>
        </div>
      </div>
      {!supported ? (
        <p className="muted small">This browser doesn’t support notifications.</p>
      ) : permission === 'granted' ? (
        <p className="status-ok">
          <CheckCircle2 size={16} /> On for this browser
        </p>
      ) : permission === 'denied' ? (
        <p className="muted small">Blocked. Allow notifications for this site in your browser’s settings, then reload.</p>
      ) : (
        <button type="button" className="btn btn-primary" onClick={async () => setPermission(await Notification.requestPermission())}>
          Turn on browser notifications
        </button>
      )}
    </section>
  )
}

function TelegramCard({ settings }: { settings: NotificationSettings }) {
  const queryClient = useQueryClient()
  const telegram = settings.telegram
  const [waiting, setWaiting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  // After opening the t.me link, watch for the bot confirming the connection.
  useEffect(() => {
    if (!waiting || telegram.connected) return
    const id = window.setInterval(() => queryClient.invalidateQueries({ queryKey: ['notifications'] }), 3000)
    const stop = window.setTimeout(() => setWaiting(false), 15 * 60_000)
    return () => {
      window.clearInterval(id)
      window.clearTimeout(stop)
    }
  }, [waiting, telegram.connected, queryClient])

  const connect = useMutation({
    mutationFn: endpoints.telegramLink,
    onSuccess: ({ url }) => {
      window.open(url, '_blank', 'noopener')
      setWaiting(true)
    },
  })
  const disconnect = useMutation({
    mutationFn: endpoints.telegramUnlink,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notifications'] }),
  })
  const test = useMutation({
    mutationFn: endpoints.telegramTest,
    onSuccess: () => setMessage('Test message sent. Check Telegram.'),
    onError: (e) => setMessage(e instanceof ApiError ? e.message : 'Could not send.'),
  })
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      endpoints.saveNotificationSettings({ ...settings, telegram_enabled: enabled }),
    onSuccess: (data) => queryClient.setQueryData(['notifications'], data),
  })

  return (
    <section className="card">
      <div className="setting-head">
        <span className="row-icon telegram-icon">
          <Send size={18} />
        </span>
        <div>
          <h2>Telegram</h2>
          <p className="card-sub">Alerts on your phone, the moment they happen. Pinned events always come through.</p>
        </div>
      </div>

      {!telegram.configured ? (
        <div className="notice">
          Telegram isn’t set up on this server yet. An administrator needs to add a bot token (see the README, “Telegram alerts”).
        </div>
      ) : telegram.connected ? (
        <>
          <p className="status-ok">
            <CheckCircle2 size={16} /> Connected
            {telegram.username ? ` as @${telegram.username}` : telegram.first_name ? ` as ${telegram.first_name}` : ''}
            {telegram.linked_at && <span className="muted small"> · {relativeTime(telegram.linked_at)}</span>}
          </p>
          <label className="switch-row">
            <input type="checkbox" checked={settings.telegram_enabled} onChange={(e) => toggle.mutate(e.target.checked)} />
            Send my alerts to Telegram
          </label>
          <div className="head-actions" style={{ marginTop: 14 }}>
            <button type="button" className="btn btn-secondary" onClick={() => test.mutate()} disabled={test.isPending}>
              <Send size={15} /> Send a test message
            </button>
            <button type="button" className="btn btn-danger" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
              <Unlink size={15} /> Disconnect
            </button>
          </div>
          {message && <p className="muted small" style={{ marginTop: 10 }}>{message}</p>}
        </>
      ) : (
        <>
          <ol className="steps-list">
            <li>Click the button: Telegram opens a chat with @{telegram.bot_username}.</li>
            <li>Press <strong>Start</strong> in Telegram.</li>
            <li>This page shows “Connected” within a few seconds.</li>
          </ol>
          <button type="button" className="btn btn-primary" onClick={() => connect.mutate()} disabled={connect.isPending}>
            <Send size={16} /> {waiting ? 'Open Telegram again' : 'Connect Telegram'}
          </button>
          {waiting && <p className="muted small" style={{ marginTop: 10 }}>Waiting for you to press Start in Telegram… The link works for 15 minutes.</p>}
          {connect.error && <p className="error-text small">{(connect.error as ApiError).message}</p>}
        </>
      )}
    </section>
  )
}

function QuietHours({ settings }: { settings: NotificationSettings }) {
  const queryClient = useQueryClient()
  const [enabled, setEnabled] = useState(settings.quiet_start !== null)
  const [start, setStart] = useState(toTime(settings.quiet_start) || '22:00')
  const [end, setEnd] = useState(toTime(settings.quiet_end) || '07:00')
  const [timezone, setTimezone] = useState(settings.timezone)
  const [saved, setSaved] = useState(false)

  const save = useMutation({
    mutationFn: () =>
      endpoints.saveNotificationSettings({
        telegram_enabled: settings.telegram_enabled,
        quiet_start: enabled ? toMinutes(start) : null,
        quiet_end: enabled ? toMinutes(end) : null,
        timezone,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(['notifications'], data)
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2500)
    },
  })

  return (
    <section className="card">
      <div className="setting-head">
        <span className="row-icon">
          <Moon size={18} />
        </span>
        <div>
          <h2>Quiet hours</h2>
          <p className="card-sub">No Telegram alerts at night, except pinned events (like an RBI rate decision). Alerts still appear in the app.</p>
        </div>
      </div>
      <label className="switch-row">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Hold back Telegram alerts during quiet hours
      </label>
      <div className="quiet-grid" aria-disabled={!enabled}>
        <label className="field">
          <span>From</span>
          <input type="time" value={start} disabled={!enabled} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          <span>Until</span>
          <input type="time" value={end} disabled={!enabled} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label className="field">
          <span>Time zone</span>
          <select value={timezone} onChange={(e) => setTimezone(e.target.value)}>
            {TIMEZONES.map((tz) => (
              <option key={tz} value={tz}>
                {tz.replace('_', ' ')}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="head-actions" style={{ marginTop: 14 }}>
        <button type="button" className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
          Save quiet hours
        </button>
        {saved && (
          <span className="status-ok small">
            <CheckCircle2 size={14} /> Saved
          </span>
        )}
      </div>
    </section>
  )
}

export function NotificationsPage() {
  const settings = useQuery({ queryKey: ['notifications'], queryFn: endpoints.notificationSettings })

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Settings</p>
          <h1>Notifications</h1>
          <p className="page-sub">Where your alerts reach you. Every alert is also kept in Alerts.</p>
        </div>
      </header>
      <BrowserNotifications />
      {settings.data ? (
        <>
          <TelegramCard settings={settings.data} />
          <QuietHours settings={settings.data} />
        </>
      ) : (
        <div className="skeleton" style={{ height: 180, borderRadius: 16 }} />
      )}
    </div>
  )
}
