from app.ingestion.adapters.google_news import GoogleNewsAdapter
from app.ingestion.adapters.rss import RssAdapter
from app.ingestion.types import (
    FeedItem,
    FetchError,
    FetchResult,
    SourceAdapter,
    SourceRef,
)
from app.models.source import SourceKind

# New source types (a paid news API, an exchange JSON endpoint) plug in here.
ADAPTERS: dict[str, SourceAdapter] = {
    SourceKind.RSS: RssAdapter(),
    SourceKind.GOOGLE_NEWS: GoogleNewsAdapter(),
}


def get_adapter(kind: str) -> SourceAdapter:
    try:
        return ADAPTERS[kind]
    except KeyError:
        raise FetchError(f"no adapter for source kind {kind!r}") from None


__all__ = [
    "ADAPTERS",
    "FeedItem",
    "FetchError",
    "FetchResult",
    "SourceAdapter",
    "SourceRef",
    "get_adapter",
]
