"""Delivering one email: over SMTP, or to the log in development."""

import asyncio
import json
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import get_settings

log = logging.getLogger(__name__)

DEV_OUTBOX_KEY = "nis:dev:emails"  # the console mailer keeps the last 50 here


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str


class MailError(Exception):
    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent  # e.g. the address was rejected: don't retry


class Mailer(Protocol):
    async def send(self, email: OutgoingEmail) -> None: ...


def build_message(email: OutgoingEmail) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.email_from
    msg["To"] = email.to
    msg["Subject"] = email.subject
    msg["Message-ID"] = make_msgid(domain=s.email_from.rsplit("@", 1)[-1].strip("> "))
    if s.email_reply_to:
        msg["Reply-To"] = s.email_reply_to
    msg.set_content(email.text)
    msg.add_alternative(email.html, subtype="html")
    return msg


class SmtpMailer:
    """Any SMTP service. smtplib runs in a thread so the event loop isn't blocked."""

    def _send_sync(self, msg: EmailMessage) -> None:
        s = get_settings()
        if not s.smtp_host:
            raise MailError("SMTP_HOST is not set", permanent=False)
        context = ssl.create_default_context()
        try:
            if s.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=context)
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            with server:
                if s.smtp_security == "starttls":
                    server.starttls(context=context)
                if s.smtp_username:
                    server.login(s.smtp_username, s.smtp_password or "")
                server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            raise MailError(f"recipient refused: {exc.recipients}", permanent=True) from None
        except smtplib.SMTPAuthenticationError:
            raise MailError("SMTP login failed: check SMTP_USERNAME / SMTP_PASSWORD") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"{type(exc).__name__}: {exc}"[:300]) from None

    async def send(self, email: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send_sync, build_message(email))


class ConsoleMailer:
    """Development: log the email and keep it in Redis so links can be clicked."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def send(self, email: OutgoingEmail) -> None:
        log.info("EMAIL (not sent, EMAIL_PROVIDER=console) to %s: %s\n%s",
                 email.to, email.subject, email.text)  # fmt: skip
        record = {"to": email.to, "subject": email.subject, "text": email.text}
        await self.redis.lpush(DEV_OUTBOX_KEY, json.dumps(record))
        await self.redis.ltrim(DEV_OUTBOX_KEY, 0, 49)


def get_mailer(redis: Redis) -> Mailer:
    return SmtpMailer() if get_settings().email_provider == "smtp" else ConsoleMailer(redis)
