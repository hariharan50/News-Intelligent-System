export type Tier = 'critical' | 'high' | 'normal' | 'low'
export type RuleKind = 'topic' | 'entity' | 'event_type' | 'keyword' | 'source' | 'region'
export type RuleMode = 'include' | 'exclude' | 'pin'

export interface Reason {
  kind: string
  label: string
  weight: number | null
}

export interface FeedArticle {
  source: string
  title: string
  url: string
  published_at: string
}

export interface AiSummary {
  summary: string
  key_facts: string[]
  /** Markets or sectors this may move: AI opinion, shown labelled as such. */
  affected: string[]
  outdated: boolean
  updated_at: string
}

export interface FeedItem {
  story_id: string
  title: string
  url: string
  lead_source: string
  event_type: string | null
  is_routine: boolean
  topics: string[]
  entities: string[]
  regions: string[]
  /** Every source that carried the story (slugs). */
  source_slugs: string[]
  /** Every outlet that reported it (real publisher for Google News items). */
  outlets: string[]
  source_count: number
  article_count: number
  first_published_at: string
  last_published_at: string
  score: number | null
  tier: Tier | null
  pinned: boolean
  alert: boolean
  reasons: Reason[]
  is_read: boolean
  is_saved: boolean
  articles: FeedArticle[]
  ai: AiSummary | null
  /** A summary exists but the user's plan doesn't include AI summaries. */
  ai_locked: boolean
}

export interface FeedProfile {
  id: string
  name: string
  show_threshold: number
  alert_threshold: number
}

export interface FeedPage {
  profile: FeedProfile
  total: number
  unread: number
  next_offset: number | null
  items: FeedItem[]
  /** Free plan: stories appear this many minutes after publication. */
  delay_minutes: number
}

export interface SavedPage {
  total: number
  next_offset: number | null
  items: FeedItem[]
}

export interface Rule {
  id?: string
  kind: RuleKind
  value: string
  mode: RuleMode
  weight: number
  entity: string | null
  min_score: number | null
  expires_at: string | null
  base_weight?: number | null
}

export interface ProfileSummary {
  id: string
  name: string
  template: string | null
  show_threshold: number
  alert_threshold: number
  rule_count: number
  updated_at: string
}

export interface Profile {
  id: string
  name: string
  template: string | null
  show_threshold: number
  alert_threshold: number
  version: number
  created_at: string
  updated_at: string
  rules: Rule[]
}

export interface PreviewStory {
  story_id: string
  title: string
  url: string
  published_at: string
  score: number
  tier: Tier | null
  pinned: boolean
  reasons: Reason[]
}

export interface Preview {
  hours: number
  shown: number
  alerts: number
  by_tier: Partial<Record<Tier, number>>
  stories: PreviewStory[]
}

export interface Named {
  slug: string
  name: string
}

export interface Taxonomy {
  version: number
  topics: Named[]
  entities: (Named & { kind: string; ticker: string | null; region: string | null })[]
  event_types: (Named & { routine: boolean })[]
  regions: string[]
}

export interface TemplateInfo {
  slug: string
  name: string
  description: string
  show_threshold: number
  alert_threshold: number
  rule_count: number
}

export interface SourceInfo extends Named {
  category: string
  region: string
  enabled: boolean
}

export interface DeviceSession {
  id: string
  user_agent: string | null
  ip_address: string | null
  created_at: string
  last_used_at: string
  current: boolean
}

export interface StoryEvent {
  profile_id: string
  story_id: string
  title: string
  score: number
  tier: Tier | null
  alert: boolean
  new: boolean
}

export interface AlertItem {
  id: string
  story_id: string
  profile_id: string
  title: string
  url: string
  source: string
  score: number
  tier: Tier | null
  pinned: boolean
  reasons: Reason[]
  created_at: string
  read: boolean
  telegram_status: 'pending' | 'sent' | 'failed' | 'skipped' | 'quiet'
}

export interface AlertsPage {
  total: number
  unread: number
  next_offset: number | null
  items: AlertItem[]
}

export interface AlertEvent {
  alert_id: string
  profile_id: string
  story_id: string
  title: string
  url: string
  source: string
  score: number
  pinned: boolean
}

export interface TelegramStatus {
  configured: boolean
  bot_username: string | null
  connected: boolean
  username: string | null
  first_name: string | null
  linked_at: string | null
}

export interface NotificationSettings {
  telegram_enabled: boolean
  quiet_start: number | null
  quiet_end: number | null
  timezone: string
  telegram: TelegramStatus
}

export interface FeedbackResult {
  verdict: 'relevant' | 'not_relevant'
  adjustments: { label: string; from: number; to: number }[]
  suggestion: { kind: 'entity'; value: string; label: string; count: number } | null
}

export interface AdminOverview {
  users: { total: number; active_7d: number; telegram_connected: number }
  news_24h: { articles: number; stories: number }
  alerts_24h: { total: number; telegram: Record<string, number> }
  sources: { enabled: number; disabled: number; by_status: Record<string, number> }
  pipeline_heartbeat_age_seconds: number | null
  telegram_configured: boolean
  ai: { configured: boolean; enabled: boolean; spent_today_usd: number; budget_usd: number }
  billing: { configured: boolean; paying: number; by_plan: Record<string, number>; trials: number; mrr_paise: number; collected_30d_paise: number }
}

export interface Citation {
  n: number
  story_id: string
  title: string
  url: string
  source: string
  published_at: string
}

export interface AgentAnswer {
  id: string
  conversation_id: string
  question?: string
  answer: string
  citations: Citation[]
  created_at: string
}

export interface AgentStatus {
  available: boolean
  reason: 'busy' | 'not_configured' | null
  plan_allows: boolean
  plan: string
  limit: number
  used_today: number
  remaining: number
  recent: (AgentAnswer & { question: string })[]
}

export interface Brief {
  id: string
  date: string
  content: string
  citations: Citation[]
  story_count: number
  created_at: string
}

export type PlanCode = 'free' | 'pro' | 'pro_plus'

export interface PlanFeatures {
  feed_delay_minutes: number
  max_profiles: number
  telegram: boolean
  ai_summaries: boolean
  agent: 'none' | 'limited' | 'full'
  max_saved: number | null
}

export interface Plan {
  code: PlanCode
  name: string
  tagline: string | null
  price_paise: number
  interval: string
  features: PlanFeatures
  purchasable: boolean
}

export interface Billing {
  configured: boolean
  current: {
    plan: PlanCode
    plan_name: string
    source: 'subscription' | 'trial' | 'free'
    trial_ends_at: string | null
    features: PlanFeatures
  }
  subscription: {
    plan: PlanCode
    status: string
    current_end: string | null
    cancel_at_period_end: boolean
    scheduled_plan: PlanCode | null
    in_grace: boolean
  } | null
  payments: { id: string; amount_paise: number; currency: string; status: string; method: string | null; created_at: string; has_invoice: boolean }[]
}

export interface CheckoutOptions {
  key_id: string
  subscription_id: string
  plan: PlanCode
  name: string
  amount_paise: number
  prefill: { name: string; email: string; contact: string }
}

export interface AdminAi {
  configured: boolean
  enabled: boolean
  model: string
  effort: string
  min_score: number
  budget_usd: number
  spent_today_usd: number
  over_budget: boolean
  summaries_24h: Record<string, number>
  days: { day: string; requests: number; failures: number; input_tokens: number; output_tokens: number; cost_usd: number }[]
  recent_errors: { title: string; error: string; at: string }[]
}

export interface AdminSource {
  slug: string
  name: string
  url: string
  homepage: string | null
  category: string
  region: string
  licence_class: string
  authority: number
  is_priority: boolean
  enabled: boolean
  poll_interval_seconds: number
  status: 'unknown' | 'healthy' | 'degraded' | 'failing'
  consecutive_failures: number
  last_polled_at: string | null
  last_success_at: string | null
  last_new_item_at: string | null
  next_poll_at: string
  last_error: string | null
  notes: string | null
  fetches_24h: number
  errors_24h: number
  new_items_24h: number
  avg_ms_24h: number | null
}

export interface FeedTestResult {
  ok: boolean
  error?: string
  http_status?: number | null
  duration_ms?: number
  item_count?: number
  items: { title: string; url: string; published_at: string | null; excerpt: string | null }[]
}

export interface AdminUser {
  id: string
  email: string
  full_name: string | null
  is_active: boolean
  is_admin: boolean
  created_at: string
  last_login_at: string | null
  profiles: number
  telegram_connected: boolean
  alerts_7d: number
  plan: string
  plan_source: 'subscription' | 'trial' | 'free'
}
