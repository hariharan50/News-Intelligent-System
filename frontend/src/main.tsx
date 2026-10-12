// Global styles first: page stylesheets imported by components must come after them.
import '@fontsource-variable/inter'
import '@fontsource-variable/space-grotesk'
import './index.css'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate, Outlet, RouterProvider } from 'react-router'
import { ApiError } from './api/client'
import { AuthProvider } from './auth/AuthContext'
import { RequireAuth } from './auth/RequireAuth'
import { Layout } from './components/Layout'
import { LiveProvider } from './live/LiveContext'
import { AccountPage } from './pages/AccountPage'
import { AdminPage } from './pages/AdminPage'
import { AlertsPage } from './pages/AlertsPage'
import { AskPage } from './pages/AskPage'
import { AuthPage } from './pages/AuthPage'
import { BillingPage } from './pages/BillingPage'
import { DevicesPage } from './pages/DevicesPage'
import { ForgotPasswordPage, ResetPasswordPage, VerifyEmailPage } from './pages/EmailLinkPages'
import { FeedPage } from './pages/FeedPage'
import { LandingPage } from './pages/LandingPage'
import { PrivacyPage, TermsPage } from './pages/LegalPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { ProfileEditorPage } from './pages/ProfileEditorPage'
import { ProfilesPage } from './pages/ProfilesPage'
import { SavedPage } from './pages/SavedPage'
import { SystemPage } from './pages/SystemPage'

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
        <Outlet />
      </LiveProvider>
    </AuthProvider>
  )
}

const router = createBrowserRouter([
  {
    element: <Providers />,
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
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
