import { useEffect, useState } from 'react'

interface PollingState<T> {
  data: T | null
  error: boolean
}

/** Calls `load` now and every `intervalMs`, aborting in-flight requests on unmount. */
export function usePolling<T>(load: (signal: AbortSignal) => Promise<T>, intervalMs: number) {
  const [state, setState] = useState<PollingState<T>>({ data: null, error: false })

  useEffect(() => {
    const controller = new AbortController()
    const run = () =>
      load(controller.signal)
        .then((data) => setState({ data, error: false }))
        .catch((err: unknown) => {
          if (controller.signal.aborted) return
          console.error(err)
          setState((s) => ({ ...s, error: true }))
        })
    run()
    const id = window.setInterval(run, intervalMs)
    return () => {
      controller.abort()
      window.clearInterval(id)
    }
  }, [load, intervalMs])

  return state
}
