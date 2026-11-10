// The last line of defence in the browser.
//
// Without this, one thrown render error unmounts the whole React tree and the person is
// left on a blank white page with no way back except guessing at the URL. React has no
// recovery of its own: an error boundary is the only way to catch it.
//
// There are two entry points because they catch different things:
//   * RouteErrorBoundary  - given to the router as errorElement. React Router catches
//     render errors inside routes and hands them here.
//   * AppErrorBoundary    - a class component wrapping the router itself, for anything
//     thrown above or outside the route tree (a provider, the router's own setup).
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { isRouteErrorResponse, useRouteError } from 'react-router'
import '../styles/error.css'

function reload() {
  window.location.reload()
}

function goHome() {
  window.location.href = '/'
}

/** What the person sees. Deliberately plain: it must not depend on app state to render. */
function Fallback({ detail }: { detail?: string }) {
  return (
    <div className="crash" role="alert">
      <div className="crash-card">
        <span className="crash-icon">
          <AlertTriangle size={22} />
        </span>
        <h1>Something broke on this page</h1>
        <p>
          The error has been reported. Reloading usually fixes it — your account and saved stories
          are not affected.
        </p>
        {detail && <pre className="crash-detail">{detail}</pre>}
        <div className="crash-actions">
          <button type="button" className="btn btn-primary" onClick={reload}>
            <RefreshCw size={15} /> Reload the page
          </button>
          <button type="button" className="btn btn-ghost" onClick={goHome}>
            Go to the start
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * Only shown in development. In production a stack trace tells the person nothing and can
 * name internal paths, so the fallback stays generic and the detail goes to Sentry instead.
 */
function detailOf(error: unknown): string | undefined {
  if (!import.meta.env.DEV) return undefined
  if (isRouteErrorResponse(error)) return `${error.status} ${error.statusText}`
  if (error instanceof Error) return error.stack ?? error.message
  return String(error)
}

/** Given to the router as `errorElement`: catches render errors inside the route tree. */
export function RouteErrorBoundary() {
  const error = useRouteError()
  // A 404 from the router is not a crash; the catch-all route already handles those.
  return <Fallback detail={detailOf(error)} />
}

interface State {
  error: Error | null
}

/** Catches anything thrown outside the route tree, including in the providers. */
export class AppErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Reaches Sentry through the global handler when a DSN is configured; the console
    // keeps it visible in development and in a browser's own error reporting.
    console.error('Unhandled render error:', error, info.componentStack)
  }

  render() {
    if (this.state.error) return <Fallback detail={detailOf(this.state.error)} />
    return this.props.children
  }
}
