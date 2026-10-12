"""Import every model here so Alembic autogenerate and relationship resolution see them."""

from app.models.agent import AgentQuestion, DailyBrief
from app.models.ai import AiUsageDaily, StorySummary, SummaryStatus
from app.models.alerts import (
    Alert,
    DeliveryStatus,
    NotificationSettings,
    StoryFeedback,
    TelegramLink,
    Verdict,
)
from app.models.article import Article
from app.models.billing import BillingEvent, Payment, Plan, Subscription
from app.models.email import EmailOutbox, EmailToken
from app.models.profile import Profile, ProfileRule, RuleKind, RuleMode, StoryScore
from app.models.security import AdminAuditLog, RecoveryCode, UserIdentity
from app.models.source import (
    FetchOutcome,
    LicenceClass,
    Source,
    SourceFetch,
    SourceKind,
    SourceStatus,
)
from app.models.story import Story
from app.models.story_state import StoryUserState
from app.models.user import User, UserSession

__all__ = [
    "UserIdentity",
    "AdminAuditLog",
    "RecoveryCode",
    "AgentQuestion",
    "DailyBrief",
    "EmailOutbox",
    "EmailToken",
    "BillingEvent",
    "Payment",
    "Plan",
    "Subscription",
    "AiUsageDaily",
    "StorySummary",
    "SummaryStatus",
    "Alert",
    "Article",
    "DeliveryStatus",
    "NotificationSettings",
    "StoryFeedback",
    "TelegramLink",
    "Verdict",
    "FetchOutcome",
    "LicenceClass",
    "Profile",
    "ProfileRule",
    "RuleKind",
    "RuleMode",
    "Source",
    "SourceFetch",
    "SourceKind",
    "SourceStatus",
    "Story",
    "StoryScore",
    "StoryUserState",
    "User",
    "UserSession",
]
