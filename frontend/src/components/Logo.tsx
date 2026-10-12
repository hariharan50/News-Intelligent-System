/** Brand mark: a signal rising out of noise, in a rounded square. */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="nis-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#0d9488" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#nis-mark)" />
      <path
        d="M6 19.5h4l2.5-5 3.5 9 3.5-14 2.5 10H26"
        fill="none"
        stroke="#042f2e"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function Logo({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <span className={`logo ${className}`}>
      <LogoMark size={size} />
      <span className="logo-text">
        News <span className="logo-accent">Intelligence</span>
      </span>
    </span>
  )
}
