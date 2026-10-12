import { useEffect, useState } from 'react'

export type ThemeChoice = 'light' | 'dark' | 'system'
const KEY = 'nis-theme'

function read(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

export function applyTheme(choice: ThemeChoice) {
  const root = document.documentElement
  if (choice === 'system') delete root.dataset.theme
  else root.dataset.theme = choice
}

/** Theme preference for this browser only (a convenience, not account data). */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(read)

  useEffect(() => {
    applyTheme(choice)
    try {
      if (choice === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, choice)
    } catch {
      // storage unavailable (private mode): the choice lasts for this page only
    }
  }, [choice])

  const resolved =
    choice === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : choice

  return { choice, resolved, setChoice }
}
