"""Launch sources. Seeding only inserts missing slugs, so admin edits are never overwritten.

Disabled sources were checked on 2026-10-10 and refuse automated clients (HTTP 403 or no
response). We do not disguise the crawler as a browser to get around that; they need the
publisher's permission, an official API, or a licensed news provider.
"""

from dataclasses import asdict, dataclass

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import LicenceClass, Source, SourceKind


@dataclass(frozen=True)
class SourceSeed:
    slug: str
    name: str
    url: str
    homepage: str
    category: str
    region: str
    licence_class: str
    authority: int
    poll_interval_seconds: int
    is_priority: bool = False
    enabled: bool = True
    notes: str | None = None
    kind: str = SourceKind.RSS


_OFFICIAL = LicenceClass.OFFICIAL
_PRESS = LicenceClass.HEADLINE_LINK
_BLOCKED_NOTE = (
    "Refuses automated clients (checked 2026-10-10). Needs publisher permission, "
    "an official API or a licensed provider."
)

LAUNCH_SOURCES: list[SourceSeed] = [
    # --- Official, India: the original source for most market-moving Indian news.
    SourceSeed(
        "rbi-press-releases", "RBI Press Releases",
        "https://www.rbi.org.in/pressreleases_rss.xml", "https://www.rbi.org.in",
        "official_india", "IN", _OFFICIAL, 5, 30, is_priority=True,
    ),
    SourceSeed(
        "rbi-notifications", "RBI Notifications",
        "https://www.rbi.org.in/notifications_rss.xml", "https://www.rbi.org.in",
        "official_india", "IN", _OFFICIAL, 5, 60, is_priority=True,
    ),
    SourceSeed(
        "sebi", "SEBI Updates",
        "https://www.sebi.gov.in/sebirss.xml", "https://www.sebi.gov.in",
        "official_india", "IN", _OFFICIAL, 5, 60, is_priority=True,
    ),
    SourceSeed(
        "pib-finance", "PIB - Ministry of Finance",
        "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3", "https://pib.gov.in",
        "official_india", "IN", _OFFICIAL, 5, 120, enabled=False, notes=_BLOCKED_NOTE,
    ),
    SourceSeed(
        "nse-announcements", "NSE Corporate Announcements",
        "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",
        "https://www.nseindia.com",
        "official_india", "IN", _OFFICIAL, 5, 60, enabled=False, notes=_BLOCKED_NOTE,
    ),
    # --- Official, global.
    SourceSeed(
        "fed-press", "US Federal Reserve Press Releases",
        "https://www.federalreserve.gov/feeds/press_all.xml", "https://www.federalreserve.gov",
        "official_global", "US", _OFFICIAL, 5, 60, is_priority=True,
    ),
    # --- Indian business press (headline + link only).
    SourceSeed(
        "mint-markets", "Mint - Markets",
        "https://www.livemint.com/rss/markets", "https://www.livemint.com",
        "press_india", "IN", _PRESS, 3, 120,
    ),
    SourceSeed(
        "mint-economy", "Mint - Economy",
        "https://www.livemint.com/rss/economy", "https://www.livemint.com",
        "press_india", "IN", _PRESS, 3, 180,
    ),
    SourceSeed(
        "et-markets", "Economic Times - Markets",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://economictimes.indiatimes.com",
        "press_india", "IN", _PRESS, 3, 120,
    ),
    SourceSeed(
        "et-economy", "Economic Times - Economy",
        "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
        "https://economictimes.indiatimes.com",
        "press_india", "IN", _PRESS, 3, 180,
    ),
    SourceSeed(
        "business-standard-markets", "Business Standard - Markets",
        "https://www.business-standard.com/rss/markets-106.rss",
        "https://www.business-standard.com",
        "press_india", "IN", _PRESS, 3, 120, enabled=False, notes=_BLOCKED_NOTE,
    ),
    SourceSeed(
        "business-standard-economy", "Business Standard - Economy",
        "https://www.business-standard.com/rss/economy-102.rss",
        "https://www.business-standard.com",
        "press_india", "IN", _PRESS, 3, 180, enabled=False, notes=_BLOCKED_NOTE,
    ),
    SourceSeed(
        "moneycontrol-latest", "Moneycontrol - Latest",
        "https://www.moneycontrol.com/rss/latestnews.xml", "https://www.moneycontrol.com",
        "press_india", "IN", _PRESS, 2, 120, enabled=False, notes=_BLOCKED_NOTE,
    ),
    # --- Global press (headline + link only).
    SourceSeed(
        "bbc-business", "BBC News - Business",
        "https://feeds.bbci.co.uk/news/business/rss.xml", "https://www.bbc.com/news/business",
        "press_global", "GLOBAL", _PRESS, 4, 300,
    ),
    SourceSeed(
        "bbc-world", "BBC News - World",
        "https://feeds.bbci.co.uk/news/world/rss.xml", "https://www.bbc.com/news/world",
        "press_global", "GLOBAL", _PRESS, 4, 300,
    ),
    # --- Added 2026-10-11 after a test fetch with our own reader (all returned valid feeds).
    # Indian business press.
    SourceSeed(
        "businessline-markets", "BusinessLine - Markets",
        "https://www.thehindubusinessline.com/markets/feeder/default.rss",
        "https://www.thehindubusinessline.com",
        "press_india", "IN", _PRESS, 3, 180,
    ),
    SourceSeed(
        "businessline-economy", "BusinessLine - Economy",
        "https://www.thehindubusinessline.com/economy/feeder/default.rss",
        "https://www.thehindubusinessline.com",
        "press_india", "IN", _PRESS, 3, 240,
    ),
    SourceSeed(
        "businessline-companies", "BusinessLine - Companies",
        "https://www.thehindubusinessline.com/companies/feeder/default.rss",
        "https://www.thehindubusinessline.com",
        "press_india", "IN", _PRESS, 3, 240,
    ),
    SourceSeed(
        "thehindu-business", "The Hindu - Business",
        "https://www.thehindu.com/business/feeder/default.rss", "https://www.thehindu.com",
        "press_india", "IN", _PRESS, 3, 300,
    ),
    SourceSeed(
        "thehindu-markets", "The Hindu - Markets",
        "https://www.thehindu.com/business/markets/feeder/default.rss", "https://www.thehindu.com",
        "press_india", "IN", _PRESS, 3, 300,
    ),
    SourceSeed(
        "cnbctv18-market", "CNBC-TV18 - Markets",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml", "https://www.cnbctv18.com",
        "press_india", "IN", _PRESS, 3, 120,
    ),
    SourceSeed(
        "cnbctv18-economy", "CNBC-TV18 - Economy",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/economy.xml", "https://www.cnbctv18.com",
        "press_india", "IN", _PRESS, 3, 240,
    ),
    SourceSeed(
        "cnbctv18-business", "CNBC-TV18 - Business",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/business.xml", "https://www.cnbctv18.com",
        "press_india", "IN", _PRESS, 3, 300,
    ),
    SourceSeed(
        "businesstoday", "Business Today",
        "https://www.businesstoday.in/rssfeeds/?id=225346", "https://www.businesstoday.in",
        "press_india", "IN", _PRESS, 3, 240,
    ),
    SourceSeed(
        "ndtvprofit", "NDTV Profit",
        "https://feeds.feedburner.com/ndtvprofit-latest", "https://www.ndtvprofit.com",
        "press_india", "IN", _PRESS, 3, 180,
    ),
    SourceSeed(
        "et-stocks", "Economic Times - Stocks",
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        "https://economictimes.indiatimes.com",
        "press_india", "IN", _PRESS, 3, 120,
    ),
    SourceSeed(
        "et-commodities", "Economic Times - Commodities",
        "https://economictimes.indiatimes.com/markets/commodities/rssfeeds/1808152121.cms",
        "https://economictimes.indiatimes.com",
        "press_india", "IN", _PRESS, 3, 240,
    ),
    SourceSeed(
        "et-forex", "Economic Times - Forex",
        "https://economictimes.indiatimes.com/markets/forex/rssfeeds/1150221130.cms",
        "https://economictimes.indiatimes.com",
        "press_india", "IN", _PRESS, 3, 240,
    ),
    SourceSeed(
        "et-ipo", "Economic Times - IPOs",
        "https://economictimes.indiatimes.com/markets/ipos/fpos/rssfeeds/14655708.cms",
        "https://economictimes.indiatimes.com",
        "press_india", "IN", _PRESS, 3, 300,
    ),
    SourceSeed(
        "et-industry", "Economic Times - Industry",
        "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms",
        "https://economictimes.indiatimes.com",
        "press_india", "IN", _PRESS, 3, 300,
    ),
    SourceSeed(
        "mint-companies", "Mint - Companies",
        "https://www.livemint.com/rss/companies", "https://www.livemint.com",
        "press_india", "IN", _PRESS, 3, 180,
    ),
    # Global markets press.
    SourceSeed(
        "cnbc-top", "CNBC - Top News",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html", "https://www.cnbc.com",
        "press_global", "US", _PRESS, 4, 180,
    ),
    SourceSeed(
        "cnbc-world", "CNBC - World",
        "https://www.cnbc.com/id/100727362/device/rss/rss.html", "https://www.cnbc.com",
        "press_global", "US", _PRESS, 4, 240,
    ),
    SourceSeed(
        "cnbc-economy", "CNBC - Economy",
        "https://www.cnbc.com/id/20910258/device/rss/rss.html", "https://www.cnbc.com",
        "press_global", "US", _PRESS, 4, 300,
    ),
    SourceSeed(
        "cnbc-finance", "CNBC - Finance",
        "https://www.cnbc.com/id/10000664/device/rss/rss.html", "https://www.cnbc.com",
        "press_global", "US", _PRESS, 4, 300,
    ),
    SourceSeed(
        "wsj-markets", "WSJ - Markets",
        "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "https://www.wsj.com",
        "press_global", "US", _PRESS, 4, 180,
    ),
    SourceSeed(
        "wsj-world", "WSJ - World",
        "https://feeds.content.dowjones.io/public/rss/RSSWorldNews", "https://www.wsj.com",
        "press_global", "US", _PRESS, 4, 300,
    ),
    SourceSeed(
        "marketwatch", "MarketWatch - Top Stories",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories", "https://www.marketwatch.com",
        "press_global", "US", _PRESS, 3, 240,
    ),
    SourceSeed(
        "nyt-business", "New York Times - Business",
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "https://www.nytimes.com",
        "press_global", "US", _PRESS, 4, 300,
    ),
    SourceSeed(
        "nyt-economy", "New York Times - Economy",
        "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", "https://www.nytimes.com",
        "press_global", "US", _PRESS, 4, 600,
    ),
    SourceSeed(
        "guardian-business", "The Guardian - Business",
        "https://www.theguardian.com/uk/business/rss", "https://www.theguardian.com",
        "press_global", "GLOBAL", _PRESS, 4, 300,
    ),
    SourceSeed(
        "aljazeera", "Al Jazeera",
        "https://www.aljazeera.com/xml/rss/all.xml", "https://www.aljazeera.com",
        "press_global", "GLOBAL", _PRESS, 3, 300,
    ),
    SourceSeed(
        "investing-news", "Investing.com - News",
        "https://www.investing.com/rss/news.rss", "https://www.investing.com",
        "press_global", "GLOBAL", _PRESS, 3, 300,
    ),
    # Official, global: central banks and US data agencies.
    SourceSeed(
        "ecb-press", "European Central Bank - Press",
        "https://www.ecb.europa.eu/rss/press.html", "https://www.ecb.europa.eu",
        "official_global", "GLOBAL", _OFFICIAL, 5, 300, is_priority=True,
    ),
    SourceSeed(
        "boe-news", "Bank of England - News",
        "https://www.bankofengland.co.uk/rss/news", "https://www.bankofengland.co.uk",
        "official_global", "GLOBAL", _OFFICIAL, 5, 300, is_priority=True,
    ),
    SourceSeed(
        "fed-speeches", "US Federal Reserve - Speeches",
        "https://www.federalreserve.gov/feeds/speeches.xml", "https://www.federalreserve.gov",
        "official_global", "US", _OFFICIAL, 5, 600,
    ),
    SourceSeed(
        "bea-releases", "US BEA - Data Releases",
        "https://apps.bea.gov/rss/rss.xml", "https://www.bea.gov",
        "official_global", "US", _OFFICIAL, 5, 600,
    ),
    SourceSeed(
        "eia-press", "US EIA - Press Releases",
        "https://www.eia.gov/rss/press_rss.xml", "https://www.eia.gov",
        "official_global", "US", _OFFICIAL, 4, 900,
    ),
    SourceSeed(
        "eia-today", "US EIA - Today in Energy",
        "https://www.eia.gov/rss/todayinenergy.xml", "https://www.eia.gov",
        "official_global", "US", _OFFICIAL, 4, 900,
    ),
]  # fmt: skip


async def seed_sources(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Insert launch sources that don't exist yet. Returns how many were added."""
    rows = [asdict(s) for s in LAUNCH_SOURCES]
    async with session_factory() as session, session.begin():
        result = await session.execute(
            pg_insert(Source)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[Source.slug])
            .returning(Source.slug)
        )
        return len(result.scalars().all())
