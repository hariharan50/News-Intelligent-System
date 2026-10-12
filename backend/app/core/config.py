from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Only for local development and tests; staging/production refuse to start with it.
_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"


class Settings(BaseSettings):
    """Application settings, read from environment variables (and `.env` in local dev)."""

    # Empty variables count as unset (docker compose passes "" for optional settings).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    app_name: str = "News Intelligence System"

    # Feature switches. Off = the app runs as a personal tool: no plans or payments (every
    # feature unlocked for every account) and no Claude calls. The code stays in place;
    # turn them on (FEATURE_BILLING=true, FEATURE_AI=true) to run it as the SaaS.
    feature_billing: bool = False
    feature_ai: bool = False
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+asyncpg://nis:nis@localhost:5432/nis",
        description="SQLAlchemy async URL (asyncpg driver).",
    )
    redis_url: str = "redis://localhost:6379/0"

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # A pipeline heartbeat older than this is reported as stale on /health/ready.
    heartbeat_max_age_seconds: int = 150

    # Ingestion
    ingest_user_agent: str = "NewsIntelligenceBot/0.1 (news aggregator; headline and link only)"
    ingest_timeout_seconds: float = 15.0
    ingest_max_bytes: int = 5 * 1024 * 1024
    # Minimum gap between two requests to the same host, across all workers.
    ingest_host_min_gap_ms: int = 1000
    # How often the dispatcher looks for sources that are due. A source is polled at most
    # this many seconds after it falls due, so it bounds alert latency for priority sources.
    ingest_dispatch_interval_seconds: int = 5
    # A claimed source is re-claimable after this long if its poll task never finished.
    ingest_claim_lease_seconds: int = 120
    ingest_max_backoff_seconds: int = 3600
    # After this many consecutive failures a source is marked "failing" rather than "degraded".
    ingest_failing_after: int = 10

    # Auth: short-lived JWT access tokens + one revocable server-side session per device.
    jwt_secret: str = Field(default=_DEV_JWT_SECRET, min_length=32)
    jwt_issuer: str = "nis"
    access_token_ttl_seconds: int = 15 * 60
    session_ttl_days: int = 30  # absolute lifetime of a login
    # A rotated-out refresh token is still honoured this long (two tabs refreshing at once).
    refresh_reuse_grace_seconds: int = 20
    refresh_cookie_name: str = "nis_refresh"
    # None -> secure cookies everywhere except local/test (plain http on localhost).
    cookie_secure: bool | None = None
    login_max_attempts: int = 10  # per email+IP within the window below
    login_window_seconds: int = 15 * 60

    # Profiles and scoring
    max_profiles_per_user: int = 5
    # Scores below this are not stored: nothing can be shown below the lowest tier.
    score_floor: int = 20
    # How far back a new or edited profile re-scores stories.
    rescore_window_hours: int = 72

    # Alerts
    # A second alert-worthy story about the same event (same event type, same company or
    # institution) within this window does not alert again.
    alert_group_window_hours: int = 6

    # Telegram (optional: without a token the feature shows as "not configured")
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None  # without the @, e.g. NewsIntelBot
    telegram_api_base: str = "https://api.telegram.org"
    telegram_link_ttl_seconds: int = 15 * 60
    telegram_max_attempts: int = 5

    # Feedback learning
    feedback_step: int = 1  # weight change per verdict
    feedback_max_drift: int = 3  # how far learning may move a weight from the user's setting
    feedback_suggest_mute_after: int = 3  # "not relevant" verdicts on one entity, 30 days

    # AI (Claude). Optional: without a key, or over budget, everything else works unchanged.
    anthropic_api_key: str | None = None
    ai_summary_model: str = "claude-opus-5-5"
    ai_summary_effort: Literal["low", "medium", "high"] = "low"
    # Hard stop for the day (India time) once estimated spend reaches this.
    ai_daily_budget_usd: float = 3.0
    # Summarise stories that reach this score for at least one profile (60 = High tier).
    ai_summary_min_score: int = 60
    ai_summary_max_age_hours: int = 24
    ai_summary_batch: int = 8  # stories per run (runs every 30 s)
    ai_summary_max_revisions: int = 3  # rewrites as more reports join a story
    ai_summary_max_attempts: int = 3
    # AI importance check: the same call rates market impact 1-5, which moves a story's
    # score by at most this many points. Never affects alerts.
    ai_importance_enabled: bool = True
    ai_max_adjustment: int = 15

    # Billing (Razorpay Subscriptions). Without keys, the app runs and billing shows as
    # "not configured"; everyone is on their trial or the free plan.
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None
    razorpay_api_base: str = "https://api.razorpay.com/v1"
    # Razorpay plan ids (plan_...), created in the Razorpay dashboard. Test and live differ.
    razorpay_plan_pro: str | None = None
    razorpay_plan_pro_plus: str | None = None
    trial_plan: str = "pro"
    trial_days: int = 7  # 0 = no trial for new accounts
    # After a failed renewal, keep paid features this long while Razorpay retries.
    billing_grace_days: int = 3

    # Paying needs a verified email (receipts, renewal problems and account recovery go there).
    billing_require_verified_email: bool = True

    # Email. "console" logs emails instead of sending them (development); "smtp" sends through
    # any SMTP service: Zoho, Amazon SES, Resend, Gmail...
    app_base_url: str = "http://127.0.0.1:8081"  # links in emails point here
    email_provider: Literal["console", "smtp"] = "console"
    email_from: str = "News Intelligence <no-reply@example.com>"
    email_reply_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    email_verify_ttl_hours: int = 48
    password_reset_ttl_minutes: int = 60
    email_max_attempts: int = 6

    # Data retention (nightly). Stories someone saved are always kept.
    retention_news_days: int = 90  # stories and articles
    retention_log_days: int = 30  # fetch logs, sent emails, ended sessions
    retention_agent_days: int = 365  # Ask-the-news history

    # Error monitoring (optional): Sentry DSN for the API, worker and scheduler.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0

    # Sign in with Google (OpenID Connect). Create an OAuth client ("Web application") in
    # Google Cloud Console; its authorised redirect URI must be exactly GOOGLE_REDIRECT_URI
    # (default: APP_BASE_URL + /api/auth/google/callback).
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    # Security
    # Encrypts 2FA secrets: 32 random bytes, urlsafe base64 (see .env.example for a command).
    # Required in production; derived from JWT_SECRET in development.
    data_encryption_key: str | None = None
    # Admins must use two-factor sign-in. None = required outside local/test.
    admin_require_2fa: bool | None = None

    @property
    def admin_needs_2fa(self) -> bool:
        if self.admin_require_2fa is not None:
            return self.admin_require_2fa
        return self.environment not in ("local", "test")

    def production_problems(self) -> list[str]:
        """Settings that are unsafe for real users. Production refuses to start with any."""
        problems = []
        if not self.app_base_url.startswith("https://"):
            problems.append("APP_BASE_URL must be https:// (links in emails, cookies)")
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            problems.append("CORS_ORIGINS must not include localhost")
        if self.cookie_secure is False:
            problems.append("COOKIE_SECURE must not be false")
        if not self.data_encryption_key:
            problems.append("DATA_ENCRYPTION_KEY must be set")
        if self.feature_billing and self.email_provider != "smtp":
            problems.append("EMAIL_PROVIDER must be smtp (people need reset and verify emails)")
        if self.admin_require_2fa is False:
            problems.append("ADMIN_REQUIRE_2FA must not be false")
        return problems

    @property
    def billing_configured(self) -> bool:
        return self.feature_billing and bool(self.razorpay_key_id and self.razorpay_key_secret)

    def razorpay_plan_ids(self) -> dict[str, str]:
        ids = {"pro": self.razorpay_plan_pro, "pro_plus": self.razorpay_plan_pro_plus}
        return {code: plan_id for code, plan_id in ids.items() if plan_id}

    # AI agent ("Ask the news") and the pre-market brief. Separate daily budget (India time).
    ai_agent_model: str = "claude-opus-5-5"
    ai_agent_effort: Literal["low", "medium", "high"] = "medium"
    ai_agent_daily_budget_usd: float = 5.0
    ai_agent_max_tool_rounds: int = 6
    # Questions per user per day (India time), by the plan's "agent" level.
    ai_agent_questions_limited: int = 10  # Pro
    ai_agent_questions_full: int = 50  # Pro+

    @property
    def ai_configured(self) -> bool:
        return self.feature_ai and bool(self.anthropic_api_key)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_bot_username)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment not in ("local", "test")

    @model_validator(mode="after")
    def _require_real_secret_outside_dev(self) -> "Settings":
        if self.environment in ("staging", "production") and self.jwt_secret == _DEV_JWT_SECRET:
            raise ValueError("JWT_SECRET must be set to a random value outside local/test")
        if self.environment == "production" and (problems := self.production_problems()):
            raise ValueError("Unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
