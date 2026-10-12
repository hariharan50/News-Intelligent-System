"""Transactional emails: subject, plain text and a simple HTML version of each.

Plain wording, one clear button, and the raw link as text for clients that strip buttons.
"""

from dataclasses import dataclass
from html import escape

BRAND = "News Intelligence"


@dataclass(frozen=True, slots=True)
class Rendered:
    subject: str
    text: str
    html: str


def _html(title: str, paragraphs: list[str], button: tuple[str, str] | None, footer: str) -> str:
    body = "".join(f'<p style="margin:0 0 14px">{escape(p)}</p>' for p in paragraphs)
    if button:
        label, url = button
        body += (
            f'<p style="margin:22px 0"><a href="{escape(url)}" style="background:#14b8a6;'
            "color:#04201c;padding:11px 20px;border-radius:8px;text-decoration:none;"
            f'font-weight:600;display:inline-block">{escape(label)}</a></p>'
            f'<p style="margin:0 0 14px;font-size:13px;color:#64748b">Or open this link: '
            f'<a href="{escape(url)}" style="color:#0f766e">{escape(url)}</a></p>'
        )
    return (
        '<!doctype html><html><body style="margin:0;background:#f1f5f9;padding:24px 12px;'
        'font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#0f172a">'
        '<div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;'
        'padding:28px">'
        f'<p style="margin:0 0 18px;font-weight:700;color:#0f766e">{BRAND}</p>'
        f'<h1 style="margin:0 0 16px;font-size:20px">{escape(title)}</h1>{body}'
        f'<p style="margin:22px 0 0;font-size:12px;color:#94a3b8">{escape(footer)}</p>'
        "</div></body></html>"
    )


def _text(paragraphs: list[str], button: tuple[str, str] | None, footer: str) -> str:
    parts = list(paragraphs)
    if button:
        parts.append(f"{button[0]}: {button[1]}")
    parts.append(f"-- \n{footer}")
    return "\n\n".join(parts)


def _render(
    subject: str, title: str, paragraphs: list[str], button: tuple[str, str] | None, footer: str
) -> Rendered:
    return Rendered(
        subject, _text(paragraphs, button, footer), _html(title, paragraphs, button, footer)
    )


def _hello(name: str | None) -> str:
    return f"Hi {name.split()[0]}," if name else "Hi,"


SECURITY_FOOTER = (
    f"You're receiving this because of your {BRAND} account. If this wasn't you, you can "
    "ignore this email; your account is unchanged."
)
ACCOUNT_FOOTER = f"This is a service email about your {BRAND} account."


def verify_email(name: str | None, url: str, hours: int) -> Rendered:
    return _render(
        f"Confirm your email for {BRAND}",
        "Confirm your email address",
        [_hello(name), "Please confirm this is your email address. We use it for receipts, "
         "alerts about your account and to help you get back in if you forget your password.",
         f"The link works for {hours} hours."],
        ("Confirm email", url),
        SECURITY_FOOTER,
    )  # fmt: skip


def reset_password(name: str | None, url: str, minutes: int) -> Rendered:
    return _render(
        f"Reset your {BRAND} password",
        "Reset your password",
        [_hello(name), "Someone (hopefully you) asked to reset the password for this account.",
         f"The link works once, for {minutes} minutes. Resetting signs you out on every device."],
        ("Choose a new password", url),
        SECURITY_FOOTER,
    )  # fmt: skip


def password_changed(name: str | None, url: str) -> Rendered:
    return _render(
        f"Your {BRAND} password was changed",
        "Your password was changed",
        [_hello(name), "The password for your account was just changed, and other devices "
         "were signed out.",
         "If you didn't do this, reset your password now and check your signed-in devices."],
        ("Reset password", url),
        ACCOUNT_FOOTER,
    )  # fmt: skip


def trial_ending(name: str | None, days: int, plan: str, url: str) -> Rendered:
    when = "tomorrow" if days <= 1 else f"in {days} days"
    return _render(
        f"Your {plan} trial ends {when}",
        f"Your {plan} trial ends {when}",
        [_hello(name), f"Your free {plan} trial ends {when}. To keep the real-time feed, "
         "Telegram alerts and AI summaries, choose a plan.",
         "If you do nothing, your account moves to the Free plan. Your profiles and saved "
         "stories stay."],
        ("Choose a plan", url),
        ACCOUNT_FOOTER,
    )  # fmt: skip


def payment_failed(name: str | None, plan: str, url: str) -> Rendered:
    return _render(
        f"Your {plan} renewal didn't go through",
        "We couldn't renew your subscription",
        [_hello(name), f"The latest payment for your {plan} plan failed. Razorpay will retry "
         "over the next few days, and you keep your plan meanwhile.",
         "Check your UPI app or card. If it keeps failing, you can cancel and subscribe again "
         "with another payment method on the billing page."],
        ("Open billing", url),
        ACCOUNT_FOOTER,
    )  # fmt: skip
