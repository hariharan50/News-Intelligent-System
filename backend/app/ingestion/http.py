import email.utils
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.net import PUBLIC_ONLY_HOOKS
from app.ingestion.types import FetchError

MAX_RETRY_AFTER_SECONDS = 3600

_client: httpx.AsyncClient | None = None


def build_client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        headers={
            # Identify ourselves honestly; publishers can contact us or block us by name.
            "User-Agent": settings.ingest_user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
            "text/xml;q=0.9, */*;q=0.5",
        },
        timeout=httpx.Timeout(settings.ingest_timeout_seconds),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        # Sources are admin-configurable URLs: refuse to reach private addresses,
        # including through redirects (SSRF).
        event_hooks=PUBLIC_ONLY_HOOKS,
    )


def get_client() -> httpx.AsyncClient:
    """One shared client per worker process (connection pooling, keep-alive)."""
    global _client
    if _client is None or _client.is_closed:
        _client = build_client()
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def parse_retry_after(value: str | None) -> int | None:
    """Retry-After is either delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        seconds = int(value)
    else:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = int((when - datetime.now(UTC)).total_seconds())
    return max(0, min(seconds, MAX_RETRY_AFTER_SECONDS))


@dataclass(frozen=True, slots=True)
class RawResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes


async def conditional_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    etag: str | None,
    last_modified: str | None,
    max_bytes: int,
) -> RawResponse:
    """GET with If-None-Match / If-Modified-Since and a hard cap on body size.

    A 304 comes back with an empty body. Errors and HTTP >= 400 raise FetchError.
    """
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 304:
                return RawResponse(304, resp.headers, b"")
            if resp.status_code >= 400:
                raise FetchError(
                    f"HTTP {resp.status_code}",
                    http_status=resp.status_code,
                    retry_after=parse_retry_after(resp.headers.get("Retry-After"))
                    if resp.status_code in (429, 503)
                    else None,
                )
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError(
                        f"response larger than {max_bytes} bytes", http_status=resp.status_code
                    )
                chunks.append(chunk)
            return RawResponse(resp.status_code, resp.headers, b"".join(chunks))
    except httpx.TimeoutException as exc:
        raise FetchError(f"timeout: {type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"network error: {type(exc).__name__}: {exc}") from exc
