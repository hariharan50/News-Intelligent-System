import { api, type MfaRequired, type TokenResponse, type User } from './client'
import type {
  AdminAi,
  AgentStatus,
  Brief,
  Billing,
  CheckoutOptions,
  Plan,
  PlanCode,
  AdminOverview,
  AdminSource,
  AdminUser,
  AlertsPage,
  DeviceSession,
  FeedbackResult,
  FeedTestResult,
  NotificationSettings,
  FeedPage,
  Preview,
  Profile,
  ProfileSummary,
  Rule,
  SavedPage,
  SourceInfo,
  Taxonomy,
  TemplateInfo,
  Tier,
} from './types'

// ---------------------------------------------------------------- auth

export const login = (email: string, password: string) =>
  api<TokenResponse | MfaRequired>('/api/auth/login', { method: 'POST', body: { email, password }, auth: false })
export const loginSecondStep = (mfa_token: string, code: string) =>
  api<TokenResponse>('/api/auth/login/2fa', { method: 'POST', body: { mfa_token, code }, auth: false })

// ---------------------------------------------------------------- two-step sign-in

export interface TwoFactorState {
  enabled: boolean
  enabled_at: string | null
  recovery_codes_left: number
  required: boolean
}
export const twoFactor = () => api<TwoFactorState>('/api/account/2fa')
export const twoFactorSetup = () => api<{ secret: string; otpauth_url: string }>('/api/account/2fa/setup', { method: 'POST' })
export const twoFactorEnable = (code: string) =>
  api<{ enabled: boolean; recovery_codes: string[] }>('/api/account/2fa/enable', { method: 'POST', body: { code } })
export const twoFactorDisable = (password: string, code: string) =>
  api<{ enabled: boolean }>('/api/account/2fa/disable', { method: 'POST', body: { password, code } })
export const twoFactorNewCodes = (code: string) =>
  api<{ recovery_codes: string[] }>('/api/account/2fa/recovery-codes', { method: 'POST', body: { code } })

export interface SignupBody {
  full_name: string
  phone: string
  email: string
  password: string
  confirm_password: string
  accept_terms: boolean
  accept_risk_notice: boolean
  marketing_opt_in: boolean
}

export const register = (body: SignupBody) =>
  api<TokenResponse>('/api/auth/register', { method: 'POST', body, auth: false })

// ---------------------------------------------------------------- my account

export const account = () => api<User>('/api/account')
export const updateAccount = (body: { full_name?: string; phone?: string; marketing_opt_in?: boolean }) =>
  api<User>('/api/account', { method: 'PATCH', body })
export const changePassword = (body: { current_password: string; new_password: string; confirm_password: string }) =>
  api<{ changed: boolean; other_devices_signed_out: number }>('/api/account/password', { method: 'POST', body })
export const deleteAccount = (password: string) =>
  api<void>('/api/account/delete', { method: 'POST', body: { password, confirm: 'DELETE' } })

export const logout = () => api<void>('/api/auth/logout', { method: 'POST' })
export const logoutAll = () => api<void>('/api/auth/logout-all', { method: 'POST' })
export const me = () => api<User>('/api/auth/me')
export const verifyEmail = (token: string) =>
  api<{ verified: boolean; email: string }>('/api/auth/verify-email', { method: 'POST', body: { token }, auth: false })
export const resendVerification = () => api<{ sent: boolean; already_verified: boolean }>('/api/auth/resend-verification', { method: 'POST' })
export const forgotPassword = (email: string) => api<{ sent: boolean }>('/api/auth/forgot-password', { method: 'POST', body: { email }, auth: false })
export const resetPassword = (body: { token: string; password: string; confirm_password: string }) =>
  api<{ reset: boolean }>('/api/auth/reset-password', { method: 'POST', body, auth: false })
export const devices = () => api<DeviceSession[]>('/api/auth/sessions')
export const revokeDevice = (id: string) => api<void>(`/api/auth/sessions/${id}`, { method: 'DELETE' })

// ---------------------------------------------------------------- feed

export interface FeedQuery {
  profile_id?: string
  sort?: 'top' | 'latest'
  since_hours?: number
  tier?: Tier
  topic?: string
  entity?: string
  source?: string
  outlet?: string
  unread_only?: boolean
  include_routine?: boolean
  q?: string
  limit?: number
  offset?: number
}

export const feed = (query: FeedQuery, signal?: AbortSignal) =>
  api<FeedPage>('/api/feed', { query: { ...query }, signal })

export const saved = (query: { profile_id?: string; q?: string; offset?: number }, signal?: AbortSignal) =>
  api<SavedPage>('/api/saved', { query: { ...query, limit: 30 }, signal })

export const markRead = (storyId: string, read: boolean) =>
  api<void>(`/api/stories/${storyId}/read`, { method: read ? 'PUT' : 'DELETE' })

export const markSaved = (storyId: string, save: boolean) =>
  api<void>(`/api/stories/${storyId}/saved`, { method: save ? 'PUT' : 'DELETE' })

export const markAllRead = (profile_id: string | undefined, since_hours: number) =>
  api<{ marked: number }>('/api/feed/mark-read', { method: 'POST', body: { profile_id, since_hours } })

// ---------------------------------------------------------------- profiles

export interface ProfileBody {
  name: string
  show_threshold: number
  alert_threshold: number
  rules: Omit<Rule, 'id' | 'base_weight'>[]
}

export const profiles = () => api<ProfileSummary[]>('/api/profiles')
export const profile = (id: string) => api<Profile>(`/api/profiles/${id}`)
export const createProfile = (body: { template?: string; name?: string; rules?: ProfileBody['rules'] }) =>
  api<Profile>('/api/profiles', { method: 'POST', body })
export const saveProfile = (id: string, body: ProfileBody) =>
  api<Profile>(`/api/profiles/${id}`, { method: 'PUT', body })
export const deleteProfile = (id: string) => api<void>(`/api/profiles/${id}`, { method: 'DELETE' })
export const preview = (
  body: Pick<ProfileBody, 'rules' | 'show_threshold' | 'alert_threshold'> & { hours?: number },
  signal?: AbortSignal,
) => api<Preview>('/api/profiles/preview', { method: 'POST', body, query: { limit: 8 }, signal })

// ---------------------------------------------------------------- reference data

export const taxonomy = () => api<Taxonomy>('/api/meta/taxonomy', { auth: false })
export const templates = () => api<TemplateInfo[]>('/api/meta/templates', { auth: false })
export const sources = () => api<SourceInfo[]>('/api/meta/sources', { auth: false })

// ---------------------------------------------------------------- alerts + notifications

export const alerts = (query: { unread_only?: boolean; offset?: number }, signal?: AbortSignal) =>
  api<AlertsPage>('/api/alerts', { query: { ...query, limit: 30 }, signal })
export const unreadAlerts = () => api<{ unread: number }>('/api/alerts/unread-count')
export const readAlert = (id: string) => api<void>(`/api/alerts/${id}/read`, { method: 'PUT' })
export const readAllAlerts = () => api<{ marked: number }>('/api/alerts/read-all', { method: 'POST' })

export const notificationSettings = () => api<NotificationSettings>('/api/notifications')
export const saveNotificationSettings = (body: Omit<NotificationSettings, 'telegram'>) =>
  api<NotificationSettings>('/api/notifications', { method: 'PUT', body })
export const telegramLink = () =>
  api<{ url: string; expires_in: number }>('/api/notifications/telegram/link', { method: 'POST' })
export const telegramUnlink = () => api<void>('/api/notifications/telegram', { method: 'DELETE' })
export const telegramTest = () => api<{ sent: boolean }>('/api/notifications/telegram/test', { method: 'POST' })

// ---------------------------------------------------------------- feedback

export const sendFeedback = (storyId: string, profileId: string, verdict: 'relevant' | 'not_relevant') =>
  api<FeedbackResult>(`/api/stories/${storyId}/feedback`, {
    method: 'POST',
    body: { profile_id: profileId, verdict },
  })
export const resetLearning = (profileId: string) =>
  api<{ reset: number }>(`/api/profiles/${profileId}/reset-learning`, { method: 'POST' })

// ---------------------------------------------------------------- admin

export const adminOverview = () => api<AdminOverview>('/api/admin/overview')
export const adminSources = () => api<AdminSource[]>('/api/admin/sources')
export const adminUpdateSource = (slug: string, body: Partial<AdminSource>) =>
  api<AdminSource>(`/api/admin/sources/${slug}`, { method: 'PATCH', body })
export const adminPollNow = (slug: string) => api<{ queued: boolean }>(`/api/admin/sources/${slug}/poll-now`, { method: 'POST' })
export const adminTestFeed = (url: string, region: string) =>
  api<FeedTestResult>('/api/admin/sources/test', { method: 'POST', body: { url, region } })
export const adminCreateSource = (body: {
  name: string
  url: string
  homepage?: string | null
  category: string
  region: string
  licence_class: string
  authority: number
  poll_interval_seconds: number
  is_priority: boolean
}) => api<AdminSource>('/api/admin/sources', { method: 'POST', body })
// ---------------------------------------------------------------- AI agent

export const features = () => api<{ billing: boolean; ai: boolean; google: boolean }>('/api/meta/features', { auth: false })
export interface SignInMethods {
  password: boolean
  google: { email: string; connected_at: string; last_used_at: string | null } | null
}
export const signInMethods = () => api<SignInMethods>('/api/account/sign-in-methods')
export const disconnectGoogle = () => api<void>('/api/account/sign-in-methods/google', { method: 'DELETE' })
export const agentStatus = () => api<AgentStatus>('/api/agent')
export const latestBrief = () => api<Brief | null>('/api/briefs/latest')

// ---------------------------------------------------------------- billing

export const plans = () => api<Plan[]>('/api/billing/plans', { auth: false })
export const billing = () => api<Billing>('/api/billing')
export const startCheckout = (plan: PlanCode) => api<CheckoutOptions>('/api/billing/checkout', { method: 'POST', body: { plan } })
export const verifyCheckout = (body: { razorpay_payment_id: string; razorpay_subscription_id: string; razorpay_signature: string }) =>
  api<Billing>('/api/billing/verify', { method: 'POST', body })
export const cancelSubscription = () => api<Billing>('/api/billing/cancel', { method: 'POST' })
export const changePlan = (plan: PlanCode) => api<Billing>('/api/billing/change-plan', { method: 'POST', body: { plan } })
export const invoiceUrl = (paymentId: string) => api<{ url: string }>(`/api/billing/payments/${paymentId}/invoice`)

export interface AuditEntry {
  id: string
  actor: string
  action: string
  target_type: string | null
  target_id: string | null
  details: Record<string, unknown>
  ip_address: string | null
  created_at: string
}
export const adminAudit = (action?: string) =>
  api<{ total: number; items: AuditEntry[] }>('/api/admin/audit', { query: { action, limit: 200 } })

export const adminAi = () => api<AdminAi>('/api/admin/ai')
export const adminSetAi = (enabled: boolean) => api<AdminAi>('/api/admin/ai', { method: 'PUT', body: { enabled } })
export const adminUsers = (q: string) => api<{ total: number; items: AdminUser[] }>('/api/admin/users', { query: { q, limit: 100 } })
export const adminUpdateUser = (id: string, body: { is_active?: boolean; is_admin?: boolean }) =>
  api<AdminUser>(`/api/admin/users/${id}`, { method: 'PATCH', body })

export interface RefreshRun {
  running: boolean
  total: number
  done: number
  started_at: string | null
  next_allowed_in: number
}
export const startRefresh = () => api<RefreshRun>('/api/feed/refresh', { method: 'POST' })
export const refreshStatus = () => api<RefreshRun>('/api/feed/refresh')
