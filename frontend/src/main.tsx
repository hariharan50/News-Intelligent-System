// Global styles first: page stylesheets imported by components must come after them.
import '@fontsource-variable/inter'
import '@fontsource-variable/space-grotesk'
import './index.css'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { lazy, StrictMode, Suspense, type ComponentType } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate, Outlet, RouterProvider } from 'react-router'
import { ApiError } from './api/client'
import { AuthProvider } from './auth/AuthContext'
import { RequireAuth, Splash } from './auth/RequireAuth'
import { AppErrorBoundary, RouteErrorBoundary } from './components/ErrorBoundary'
import { LiveProvider } from './live/LiveContext'
import { AuthPage } from './pages/AuthPage'
import { LandingPage } from './pages/LandingPage'

// Landing and auth stay in the main chunk: they are the first paint for a signed-out
// visitor, so a second round trip there would be felt. Everything behind the sign-in wall
// is split out -- without this the public landing page also downloads the admin panel,
// billing, the agent and the profile editor before it can render.
const named = <M, K extends keyof M>(load: () => Promise<M>, key: K) => lazy(
  async () => ({ default: (await load())[key] as ComponentType }),
)

const Layout = named(() => import('./components/Layout'), 'Layout')
const AccountPage = named(() => import('./pages/AccountPage'), 'AccountPage')
const AdminPage = named(() => import('./pages/AdminPage'), 'AdminPage')
const AlertsPage = named(() => import('./pages/AlertsPage'), 'AlertsPage')
const AskPage = named(() => import('./pages/AskPage'), 'AskPage')
const BillingPage = named(() => import('./pages/BillingPage'), 'BillingPage')
const DevicesPage = named(() => import('./pages/DevicesPage'), 'DevicesPage')
const FeedPage = named(() => import('./pages/FeedPage'), 'FeedPage')
const NotificationsPage = named(() => import('./pages/NotificationsPage'), 'NotificationsPage')
const ProfileEditorPage = named(() => import('./pages/ProfileEditorPage'), 'ProfileEditorPage')
const ProfilesPage = named(() => import('./pages/ProfilesPage'), 'ProfilesPage')
const SavedPage = named(() => import('./pages/SavedPage'), 'SavedPage')
const SystemPage = named(() => import('./pages/SystemPage'), 'SystemPage')
const ForgotPasswordPage = named(() => import('./pages/EmailLinkPages'), 'ForgotPasswordPage')
const ResetPasswordPage = named(() => import('./pages/EmailLinkPages'), 'ResetPasswordPage')
const VerifyEmailPage = named(() => import('./pages/EmailLinkPages'), 'VerifyEmailPage')
const PrivacyPage = named(() => import('./pages/LegalPage'), 'PrivacyPage')
const TermsPage = named(() => import('./pages/LegalPage'), 'TermsPage')

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      // Don't retry what won't change: auth and validation errors.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
})

function Providers() {
  return (
    <AuthProvider>
      <LiveProvider>
        {/* One fallback for every lazy route; Splash is what RequireAuth already shows. */}
        <Suspense fallback={<Splash />}>
          <Outlet />
        </Suspense>
      </LiveProvider>
    </AuthProvider>
  )
}

const router = createBrowserRouter([
  {
    element: <Providers />,
    // React Router hands render errors inside the route tree to this instead of
    // unmounting everything and leaving a blank page.
    errorElement: <RouteErrorBoundary />,
    children: [
      // Public
      { path: '/', element: <LandingPage /> },
      { path: '/login', element: <AuthPage mode="signin" /> },
      { path: '/signup', element: <AuthPage mode="signup" /> },
      { path: '/terms', element: <TermsPage /> },
      { path: '/privacy', element: <PrivacyPage /> },
      { path: '/verify-email', element: <VerifyEmailPage /> },
      { path: '/forgot-password', element: <ForgotPasswordPage /> },
      { path: '/reset-password', element: <ResetPasswordPage /> },
      // Signed in
      {
        element: (
          <RequireAuth>
            <Layout />
          </RequireAuth>
        ),
        children: [
          { path: '/feed', element: <FeedPage /> },
          { path: '/alerts', element: <AlertsPage /> },
          { path: '/ask', element: <AskPage /> },
          { path: '/saved', element: <SavedPage /> },
          { path: '/profiles', element: <ProfilesPage /> },
          { path: '/profiles/:profileId', element: <ProfileEditorPage /> },
          { path: '/account', element: <AccountPage /> },
          { path: '/billing', element: <BillingPage /> },
          { path: '/settings/notifications', element: <NotificationsPage /> },
          { path: '/settings/devices', element: <DevicesPage /> },
          { path: '/admin', element: <AdminPage /> },
          { path: '/system', element: <SystemPage /> },
        ],
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </AppErrorBoundary>
  </StrictMode>,
)
