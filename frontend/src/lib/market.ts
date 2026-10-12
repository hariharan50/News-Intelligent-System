// NSE regular session: Monday to Friday, 09:15 to 15:30 India time.
// Exchange holidays aren't known here, so a holiday shows as "open" during hours.

const OPEN = 9 * 60 + 15
const CLOSE = 15 * 60 + 30
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function ist(now: Date) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(now)
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  const hour = Number(get('hour')) % 24
  const minute = Number(get('minute'))
  return { day: DAYS.indexOf(get('weekday')), minutes: hour * 60 + minute, time: `${get('hour')}:${get('minute')}` }
}

export interface MarketStatus {
  open: boolean
  /** "closes 15:30" or "opens Mon 09:15" / "opens 09:15" */
  detail: string
  /** India time, HH:MM */
  time: string
}

export function marketStatus(now: Date): MarketStatus {
  const { day, minutes, time } = ist(now)
  const weekday = day >= 1 && day <= 5
  if (weekday && minutes >= OPEN && minutes < CLOSE) return { open: true, detail: 'closes 15:30', time }
  // Next opening: later today, the next weekday, or Monday.
  if (weekday && minutes < OPEN) return { open: false, detail: 'opens 09:15', time }
  let next = (day + 1) % 7
  while (next === 0 || next === 6) next = (next + 1) % 7
  const tomorrow = next === (day + 1) % 7
  return { open: false, detail: tomorrow ? 'opens tomorrow 09:15' : `opens ${DAYS[next]} 09:15`, time }
}
