from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx


@dataclass(frozen=True, slots=True)
class FeedItem:
    """One item as an adapter understood it, before hashing and storage."""

    guid: str
    url: str
    title: str
    excerpt: str | None = None
    author: str | None = None
    tags: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    # The feed gave a date but no time of day.
    published_date_only: bool = False
    # The original publisher when the source aggregates others (Google News); else None.
    publisher: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRef:
    """What an adapter needs to know about a source (decoupled from the ORM row)."""

    url: str
    etag: str | None = None
    last_modified: str | None = None
    # IANA zone used for feed dates that carry no timezone.
    timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class FetchResult:
    http_status: int
    items: list[FeedItem]
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None


class FetchError(Exception):
    """A poll failed. `retry_after` (seconds) is set when the server asked us to wait."""

    def __init__(
        self, message: str, *, http_status: int | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after


class SourceAdapter(Protocol):
    async def fetch(self, client: httpx.AsyncClient, source: SourceRef) -> FetchResult: ...
