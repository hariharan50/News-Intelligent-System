"""Which plan a user is on right now, and what it allows.

Order of precedence: a paid subscription that is in good standing (or within its grace
period), then a running free trial, then the free plan. Limits are always checked on the
server with these entitlements; the browser only uses them to explain and to upsell.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Plan, Subscription, User

FREE = "free"

# Defaults, also seeded by the migration. The `plans` table is what counts at runtime.
DEFAULT_FEATURES: dict[str, dict] = {
    "free": {"feed_delay_minutes": 15, "max_profiles": 1, "telegram": False,
             "ai_summaries": False, "agent": "none", "max_saved": 50},
    "pro": {"feed_delay_minutes": 0, "max_profiles": 3, "telegram": True,
            "ai_summaries": True, "agent": "limited", "max_saved": None},
    "pro_plus": {"feed_delay_minutes": 0, "max_profiles": 5, "telegram": True,
                 "ai_summaries": True, "agent": "full", "max_saved": None},
}  # fmt: skip

# Razorpay statuses that keep paid features (with a grace period after the paid period, so a
# failed UPI Autopay renewal that Razorpay is still retrying doesn't cut anyone off).
GOOD_STANDING = frozenset({"authenticated", "active", "pending", "halted"})
# Cancelled or finished: paid features last until the end of what was paid for.
PAID_UNTIL_END = frozenset({"cancelled", "completed", "expired"})


@dataclass(frozen=True, slots=True)
class Entitlements:
    plan: str
    plan_name: str
    source: Literal["subscription", "trial", "free", "unlimited"]
    feed_delay_minutes: int
    max_profiles: int
    telegram: bool
    ai_summaries: bool
    agent: str
    max_saved: int | None
    trial_ends_at: datetime | None = None
    subscription: Subscription | None = None

    def as_dict(self) -> dict:
        return {
            "plan": self.plan,
            "plan_name": self.plan_name,
            "source": self.source,
            "trial_ends_at": self.trial_ends_at,
            "features": {
                "feed_delay_minutes": self.feed_delay_minutes,
                "max_profiles": self.max_profiles,
                "telegram": self.telegram,
                "ai_summaries": self.ai_summaries,
                "agent": self.agent,
                "max_saved": self.max_saved,
            },
        }


def subscription_grants(sub: Subscription, now: datetime) -> bool:
    if sub.status in GOOD_STANDING:
        if sub.current_end is None:  # authenticated, first charge not yet due/reported
            return sub.status in ("authenticated", "active")
        grace = timedelta(days=get_settings().billing_grace_days)
        return sub.current_end + grace > now
    if sub.status in PAID_UNTIL_END:
        return sub.current_end is not None and sub.current_end > now
    return False


async def all_plans(db: AsyncSession) -> dict[str, Plan]:
    return {p.code: p for p in await db.scalars(select(Plan).order_by(Plan.rank))}


def _make(
    plan: Plan | None, code: str, source, *, trial_ends_at=None, subscription=None
) -> Entitlements:
    features = {
        **DEFAULT_FEATURES.get(code, DEFAULT_FEATURES[FREE]),
        **(plan.features if plan else {}),
    }
    return Entitlements(
        plan=code,
        plan_name=plan.name if plan else code.replace("_", " ").title(),
        source=source,
        feed_delay_minutes=int(features["feed_delay_minutes"]),
        max_profiles=int(features["max_profiles"]),
        telegram=bool(features["telegram"]),
        ai_summaries=bool(features["ai_summaries"]),
        agent=str(features["agent"]),
        max_saved=features["max_saved"],
        trial_ends_at=trial_ends_at,
        subscription=subscription,
    )


async def entitlements_for(
    db: AsyncSession, user_ids: list[uuid.UUID], now: datetime | None = None
) -> dict[uuid.UUID, Entitlements]:
    """Entitlements for many users in two queries (used when fanning out alerts)."""
    now = now or datetime.now(UTC)
    if not user_ids:
        return {}
    plans = await all_plans(db)
    if not get_settings().feature_billing:
        # Personal use: no plans, everything unlocked (the most generous plan's limits).
        top = max(plans.values(), key=lambda p: p.rank, default=None)
        code = top.code if top else "pro_plus"
        return {uid: _make(top, code, "unlimited") for uid in user_ids}
    trials = dict(
        (await db.execute(select(User.id, User.trial_ends_at).where(User.id.in_(user_ids)))).all()
    )
    best: dict[uuid.UUID, Subscription] = {}
    subs = await db.scalars(
        select(Subscription)
        .where(Subscription.user_id.in_(user_ids))
        .order_by(Subscription.created_at.desc())
    )
    for sub in subs:
        if not subscription_grants(sub, now) or sub.plan_code not in plans:
            continue
        current = best.get(sub.user_id)
        if current is None or plans[sub.plan_code].rank > plans[current.plan_code].rank:
            best[sub.user_id] = sub

    s = get_settings()
    result = {}
    for user_id in user_ids:
        trial_end = trials.get(user_id)
        if (sub := best.get(user_id)) is not None:
            result[user_id] = _make(plans.get(sub.plan_code), sub.plan_code, "subscription",
                                    subscription=sub)  # fmt: skip
        elif trial_end is not None and trial_end > now:
            result[user_id] = _make(plans.get(s.trial_plan), s.trial_plan, "trial",
                                    trial_ends_at=trial_end)  # fmt: skip
        else:
            result[user_id] = _make(plans.get(FREE), FREE, "free", trial_ends_at=trial_end)
    return result


async def entitlements(db: AsyncSession, user_id: uuid.UUID) -> Entitlements:
    return (await entitlements_for(db, [user_id]))[user_id]


class PlanLimitError(Exception):
    """A feature or limit the user's plan doesn't include. The API turns it into 403."""

    def __init__(self, code: str, message: str, *, needs: str = "pro") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.needs = needs
