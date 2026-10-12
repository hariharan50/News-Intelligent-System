"""Pure functions that turn messy feed fields into clean, comparable values."""

import email.utils
import hashlib
import html
import re
import unicodedata
from datetime import UTC, datetime, timedelta, tzinfo
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

EXCERPT_MAX_CHARS = 600
TITLE_MAX_CHARS = 500
# Feeds sometimes stamp items in the future (wrong timezone). Beyond this skew we distrust them.
FUTURE_SKEW = timedelta(minutes=5)

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "cmpid",
    "ito",
    "at_medium",
    "at_campaign",
    "at_custom1",
    "at_custom2",
    "at_custom3",
    "at_custom4",
}
_WS = re.compile(r"\s+")
_BLOCK_TAGS = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Strip tags and entities, collapse whitespace."""
    if not value:
        return ""
    if "<" in value:
        parser = _TextExtractor()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts)
    return _WS.sub(" ", html.unescape(value)).strip()


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    cut = value[: limit - 1]
    # Prefer to cut at a word boundary if one is reasonably close.
    space = cut.rfind(" ")
    if space > limit * 0.8:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def clean_title(value: str | None) -> str:
    return truncate(html_to_text(value), TITLE_MAX_CHARS)


def clean_excerpt(value: str | None, title: str) -> str | None:
    text = html_to_text(value)
    # Many feeds repeat the headline as the description; that adds nothing.
    if not text or text.casefold() == title.casefold():
        return None
    return truncate(text, EXCERPT_MAX_CHARS)


def canonical_url(url: str) -> str:
    """Normalise a URL so the same article from two feeds compares equal.

    Lower-cases scheme and host, drops default ports, fragments and tracking parameters
    (utm_*, fbclid, ...), and sorts the remaining query parameters.
    """
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if parts.port and not (
        (scheme == "http" and parts.port == 80) or (scheme == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        )
    )
    return urlunsplit((scheme, host, path, query, ""))


def normalise_title_for_hash(title: str) -> str:
    """Case-, accent-, punctuation- and whitespace-insensitive form used for exact-dup checks."""
    folded = unicodedata.normalize("NFKD", title).casefold()
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = re.sub(r"[^\w\s]", " ", folded)
    return _WS.sub(" ", folded).strip()


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


_SCHEME_IN_PATH = re.compile(r"(?<=.)https?://", re.IGNORECASE)


def repair_url(url: str) -> str:
    """Fix feeds that prefix an absolute link with their own host.

    e.g. SEBI: "https://www.sebi.gov.in/https://www.sebi.gov.in/sebi_data/x.pdf"
    """
    url = url.strip()
    matches = list(_SCHEME_IN_PATH.finditer(url))
    return url[matches[-1].start() :] if matches else url


_DATE_ONLY_FORMATS = ("%d %b %Y %z", "%d %b %Y", "%d %B %Y %z", "%d %B %Y", "%Y-%m-%d")


def parse_feed_date(raw: str | None, default_tz: tzinfo) -> tuple[datetime | None, bool]:
    """Parse the date formats real feeds use. Returns (datetime, is_date_only).

    Handles RFC 822 with or without weekday or timezone, ISO 8601, and date-only values
    such as "09 Oct, 2026 +0530". Values without a timezone are read in `default_tz`
    (the source's local time: RBI publishes IST without saying so).
    """
    if not raw or not (value := raw.strip()):
        return None, False

    parsed: datetime | None = None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=default_tz)
        return parsed.astimezone(UTC), False

    compact = _WS.sub(" ", value.replace(",", " ")).strip()
    for fmt in _DATE_ONLY_FORMATS:
        try:
            day = datetime.strptime(compact, fmt)
        except ValueError:
            continue
        if day.tzinfo is None:
            day = day.replace(tzinfo=default_tz)
        return day.astimezone(UTC), True
    return None, False


def resolve_published_at(
    published: datetime | None, fetched_at: datetime, *, date_only: bool = False
) -> tuple[datetime, bool]:
    """Return (published_at, estimated).

    Missing or future dates fall back to the fetch time. A date without a time of day is
    the fetch time if that is the same day (the item is new to us today), otherwise the
    start of that day; either way it is marked estimated.
    """
    if published is None:
        return fetched_at, True
    if date_only:
        end_of_day = published + timedelta(days=1)
        return (fetched_at if fetched_at < end_of_day else published), True
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    if published > fetched_at + FUTURE_SKEW:
        return fetched_at, True
    return published, False
