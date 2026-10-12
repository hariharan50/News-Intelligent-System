const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
  ['second', 1],
]

const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto', style: 'short' })

/** "3 min. ago", "in 25 sec." */
export function relativeTime(iso: string | null, now = Date.now()): string {
  if (!iso) return '—'
  const diff = Math.round((new Date(iso).getTime() - now) / 1000)
  for (const [unit, seconds] of UNITS) {
    if (Math.abs(diff) >= seconds || unit === 'second') {
      return rtf.format(Math.round(diff / seconds), unit)
    }
  }
  return '—'
}

const IST = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export const formatIst = (iso: string) => `${IST.format(new Date(iso))} IST`

/** Today's date in India as YYYY-MM-DD (the brief's date). */
export function todayIst(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
}

/** The current hour (0-23) in India. */
export function hourIst(): number {
  return Number(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata', hour: 'numeric', hour12: false })) % 24
}
