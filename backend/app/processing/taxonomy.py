"""What the tagger recognises: topics, entities and event types.

This is data, kept in code so changes are reviewed and tested. Bump TAXONOMY_VERSION
whenever matching changes so stored articles can be re-tagged.

Matching rules (see tagger.py):
  * `aliases` match case-insensitively as whole words/phrases.
  * `acronyms` match case-sensitively as whole words ("ITC", "Fed"), so "fed up" or
    "titan of industry" don't match.
  * Topic and event `patterns` are regular expressions, matched case-insensitively.
"""

from dataclasses import dataclass, field

TAXONOMY_VERSION = 3


@dataclass(frozen=True)
class Topic:
    slug: str
    name: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class Entity:
    slug: str
    name: str
    kind: str  # central_bank, regulator, institution, company, commodity, currency, index, country
    aliases: tuple[str, ...] = ()
    acronyms: tuple[str, ...] = ()
    region: str | None = None
    topics: tuple[str, ...] = ()  # topics implied whenever this entity is mentioned
    ticker: str | None = None  # NSE symbol for listed companies


@dataclass(frozen=True)
class EventType:
    slug: str
    name: str
    patterns: tuple[str, ...]
    # Speculation, previews and polls ("may cut rates", "economists expect") are not the event.
    exclude: tuple[str, ...] = ()
    # Scheduled housekeeping (auctions, statistical releases): low importance by default.
    routine: bool = False
    # Specific events: two articles with this type about the same anchor entity within a day
    # describe the same event, even if their headlines share few words.
    specific: bool = False
    anchor_kinds: tuple[str, ...] = field(default=())


# --------------------------------------------------------------------------- topics

TOPICS: tuple[Topic, ...] = (
    Topic("monetary_policy", "Monetary policy", (
        r"repo rate", r"reverse repo", r"policy rates?", r"interest rates?",
        r"rate (cut|hike)s?", r"monetary policy", r"\bMPC\b", r"\bFOMC\b",
        r"basis points?", r"\d+\s?bps\b", r"\bCRR\b", r"\bSLR\b", r"hawkish", r"dovish",
        r"rate decision", r"neutral stance", r"accommodative",
    )),
    Topic("inflation", "Inflation", (
        r"inflation", r"\bCPI\b", r"\bWPI\b", r"consumer price", r"wholesale price",
        r"price rise", r"deflation",
    )),
    Topic("currency", "Currency and forex", (
        r"rupee", r"\bINR\b", r"usd/inr", r"dollar index", r"forex", r"foreign exchange",
        r"fx reserves", r"currenc(y|ies)", r"exchange rate", r"greenback",
    )),
    Topic("energy", "Crude oil and energy", (
        r"crude", r"oil prices?", r"\bbrent\b", r"\bWTI\b", r"\bOPEC\+?", r"petrol", r"diesel",
        r"natural gas", r"\bLNG\b", r"refiner(y|ies)", r"oil marketing compan(y|ies)",
        r"\bOMCs?\b", r"fuel prices?", r"energy prices?",
    )),
    Topic("metals", "Metals and commodities", (
        r"\bgold\b", r"\bsilver\b", r"copper", r"aluminium", r"aluminum", r"steel prices?",
        r"commodit(y|ies)", r"\bzinc\b", r"iron ore",
    )),
    Topic("geopolitics", "Geopolitics and conflict", (
        r"\bwar\b", r"missiles?", r"air ?strikes?", r"drone (attack|strike)s?", r"military",
        r"troops", r"ceasefire", r"invasion", r"sanctions?", r"armed conflict",
        r"border (tension|clash)es?", r"terror(ist|ism)? attack", r"\bNATO\b", r"hostages?",
        r"geopolitic", r"shelling", r"nuclear (talks|deal|programme|program)",
    )),
    Topic("trade", "Trade and tariffs", (
        r"tariffs?", r"trade (deal|war|pact|agreement|talks|deficit|surplus)",
        r"import dut(y|ies)", r"customs dut(y|ies)", r"export (ban|curbs?)", r"anti-dumping",
        r"\bWTO\b", r"free trade",
    )),
    Topic("fiscal", "Fiscal policy and tax", (
        r"union budget", r"\bbudget\b", r"fiscal deficit", r"\bGST\b", r"income tax",
        r"tax (cut|hike|reform|rate)s?", r"government borrowing", r"disinvestment",
        r"finance minist(er|ry)", r"capital gains tax", r"\bSTT\b",
    )),
    Topic("market_regulation", "Market regulation", (
        r"\bF&O\b", r"derivatives? (rules|framework|trading)", r"insider trading",
        r"listing (rules|regulations|norms)", r"adjudication order", r"settlement order",
        r"show[- ]cause", r"market regulator", r"\bcircular\b", r"consultation paper",
        r"mutual fund (rules|norms|regulations)",
    )),
    Topic("banking", "Banking and credit", (
        r"\bbanks?\b", r"banking", r"\bNPAs?\b", r"bad loans", r"credit growth", r"deposits?",
        r"lending", r"\bloans?\b", r"\bNBFCs?\b", r"liquidity",
    )),
    Topic("earnings", "Earnings", (
        r"\bQ[1-4]\b.{0,20}results?", r"quarterly results?", r"net profit", r"\bEBITDA\b",
        r"earnings", r"revenue (rises|jumps|falls|grows|declines|up|down)",
        r"profit (rises|jumps|falls|surges|declines|drops|grows|up|down)", r"guidance",
    )),
    Topic("indian_equities", "Indian equity market", (
        r"\bnifty\b", r"\bsensex\b", r"bank nifty", r"dalal street", r"\bFIIs?\b", r"\bFPIs?\b",
        r"\bDIIs?\b", r"stock market", r"\bIPOs?\b", r"market cap", r"smallcap", r"midcap",
        r"\bNSE\b", r"\bBSE\b",
    )),
    Topic("stocks", "Stocks and equities", (
        r"\bstocks?\b", r"\bshares\b", r"\bequit(y|ies)\b", r"\bsell-?off\b", r"\brally\b",
        r"\bbull(ish)?\b", r"\bbear(ish)?\b", r"target price", r"\bbuy rating\b",
    )),
    Topic("bonds", "Bonds and yields", (
        r"bond yields?", r"\byields?\b", r"\bG-?secs?\b", r"government securities",
        r"treasury bills?", r"\bT-bills?\b", r"10-year", r"bond market", r"\bOMOs?\b", r"gilts?",
        r"state government securities", r"\bSDLs?\b",
    )),
    Topic("economy", "Economic data", (
        r"\bGDP\b", r"\bPMI\b", r"\bIIP\b", r"industrial (output|production)", r"unemployment",
        r"jobs report", r"payrolls", r"current account", r"economic growth", r"recession",
        r"growth (forecast|outlook|estimate)", r"core sector",
    )),
    Topic("global_markets", "Global markets", (
        r"wall street", r"dow jones", r"\bS&P 500\b", r"nasdaq", r"nikkei", r"hang seng",
        r"\bFTSE\b", r"global markets", r"world stocks", r"\bDAX\b",
    )),
    Topic("crypto", "Crypto", (r"bitcoin", r"crypto", r"ethereum", r"stablecoins?"),),
)  # fmt: skip


# --------------------------------------------------------------------------- entities

_MACRO: tuple[Entity, ...] = (
    # Central banks and regulators
    Entity("rbi", "Reserve Bank of India", "central_bank",
           ("Reserve Bank of India", "Reserve Bank"), ("RBI",), "IN", ("monetary_policy",)),
    Entity("sebi", "SEBI", "regulator",
           ("Securities and Exchange Board of India", "markets regulator"), ("SEBI", "Sebi"),
           "IN", ("market_regulation",)),
    Entity("fed", "US Federal Reserve", "central_bank",
           ("Federal Reserve", "Federal Open Market Committee", "Jerome Powell"),
           ("Fed", "FOMC"), "US", ("monetary_policy",)),
    Entity("ecb", "European Central Bank", "central_bank",
           ("European Central Bank", "Christine Lagarde"), ("ECB",), "EU", ("monetary_policy",)),
    Entity("boj", "Bank of Japan", "central_bank", ("Bank of Japan",), ("BoJ", "BOJ"), "JP",
           ("monetary_policy",)),
    Entity("boe", "Bank of England", "central_bank", ("Bank of England",), ("BoE",), "GB",
           ("monetary_policy",)),
    Entity("pboc", "People's Bank of China", "central_bank", ("People's Bank of China",),
           ("PBOC", "PBoC"), "CN", ("monetary_policy",)),
    Entity("finmin", "Ministry of Finance (India)", "institution",
           ("Finance Ministry", "Ministry of Finance", "Nirmala Sitharaman", "finance minister"),
           (), "IN", ("fiscal",)),
    Entity("gst_council", "GST Council", "institution", ("GST Council",), (), "IN", ("fiscal",)),
    Entity("nse", "National Stock Exchange", "institution", ("National Stock Exchange",),
           ("NSE",), "IN", ("indian_equities",)),
    Entity("bse", "BSE", "institution", ("Bombay Stock Exchange",), ("BSE",), "IN",
           ("indian_equities",)),
    Entity("opec", "OPEC", "institution", (), ("OPEC", "OPEC+"), None, ("energy",)),
    Entity("imf", "IMF", "institution", ("International Monetary Fund",), ("IMF",)),
    Entity("world_bank", "World Bank", "institution", ("World Bank",)),
    Entity("us_treasury", "US Treasury", "institution", ("US Treasury", "Treasury Secretary"),
           (), "US"),
    # Indices
    Entity("nifty50", "Nifty 50", "index", ("Nifty 50", "Nifty"), (), "IN", ("indian_equities",)),
    Entity("sensex", "Sensex", "index", ("Sensex",), (), "IN", ("indian_equities",)),
    Entity("bank_nifty", "Nifty Bank", "index", ("Bank Nifty", "Nifty Bank"), (), "IN",
           ("indian_equities",)),
    Entity("india_vix", "India VIX", "index", ("India VIX",), (), "IN", ("indian_equities",)),
    # Commodities
    Entity("brent", "Brent crude", "commodity", ("Brent crude", "Brent"), (), None, ("energy",)),
    Entity("wti", "WTI crude", "commodity", ("West Texas Intermediate",), ("WTI",), None,
           ("energy",)),
    Entity("crude_oil", "Crude oil", "commodity", ("crude oil", "oil prices", "crude prices"),
           (), None, ("energy",)),
    Entity("natural_gas", "Natural gas", "commodity", ("natural gas", "LNG"), (), None,
           ("energy",)),
    Entity("gold", "Gold", "commodity", ("gold prices", "gold price", "gold"), (), None,
           ("metals",)),
    Entity("silver", "Silver", "commodity", ("silver prices", "silver"), (), None, ("metals",)),
    # Currencies
    Entity("usd_inr", "USD/INR", "currency",
           ("USD/INR", "rupee", "Indian rupee", "rupee-dollar"), ("INR",), "IN", ("currency",)),
    Entity("dxy", "US dollar index", "currency", ("dollar index",), ("DXY",), "US",
           ("currency",)),
    Entity("bitcoin", "Bitcoin", "currency", ("bitcoin",), ("BTC",), None, ("crypto",)),
    # Countries and regions (geopolitics)
    Entity("india", "India", "country", ("India", "Indian government", "New Delhi"), (), "IN"),
    Entity("united_states", "United States", "country",
           ("United States", "Washington", "White House", "Donald Trump"), ("US", "U.S.", "USA"),
           "US"),
    Entity("china", "China", "country", ("China", "Chinese", "Beijing", "Xi Jinping"), (),
           "CN"),
    Entity("russia", "Russia", "country", ("Russia", "Russian", "Kremlin", "Moscow", "Putin"),
           (), "RU"),
    Entity("ukraine", "Ukraine", "country", ("Ukraine", "Ukrainian", "Kyiv", "Zelensky"), (),
           "UA"),
    Entity("israel", "Israel", "country", ("Israel", "Israeli", "Netanyahu"), (), "IL"),
    Entity("iran", "Iran", "country", ("Iran", "Iranian", "Tehran"), (), "IR"),
    Entity("pakistan", "Pakistan", "country", ("Pakistan", "Pakistani", "Islamabad"), (), "PK"),
    Entity("middle_east", "Middle East", "country",
           ("Middle East", "Gaza", "Red Sea", "Strait of Hormuz", "Gulf"), (), "ME"),
    Entity("european_union", "European Union", "country",
           ("European Union", "eurozone", "euro zone", "Brussels"), ("EU",), "EU"),
)  # fmt: skip


def _co(slug: str, name: str, ticker: str, aliases: tuple[str, ...], acronyms=()) -> Entity:
    return Entity(slug, name, "company", aliases, tuple(acronyms), "IN", (), ticker)


# Nifty 50 constituents. The index is rebalanced twice a year: verify against NSE's current
# list before launch and keep this in sync.
_NIFTY50: tuple[Entity, ...] = (
    _co("adani_enterprises", "Adani Enterprises", "ADANIENT", ("Adani Enterprises",)),
    _co("adani_ports", "Adani Ports", "ADANIPORTS", ("Adani Ports", "APSEZ")),
    _co("apollo_hospitals", "Apollo Hospitals", "APOLLOHOSP", ("Apollo Hospitals",)),
    _co("asian_paints", "Asian Paints", "ASIANPAINT", ("Asian Paints",)),
    _co("axis_bank", "Axis Bank", "AXISBANK", ("Axis Bank",)),
    _co("bajaj_auto", "Bajaj Auto", "BAJAJ-AUTO", ("Bajaj Auto",)),
    _co("bajaj_finance", "Bajaj Finance", "BAJFINANCE", ("Bajaj Finance",)),
    _co("bajaj_finserv", "Bajaj Finserv", "BAJAJFINSV", ("Bajaj Finserv",)),
    _co("bel", "Bharat Electronics", "BEL", ("Bharat Electronics",)),
    _co("bharti_airtel", "Bharti Airtel", "BHARTIARTL", ("Bharti Airtel", "Airtel")),
    _co("cipla", "Cipla", "CIPLA", ("Cipla",)),
    _co("coal_india", "Coal India", "COALINDIA", ("Coal India",)),
    _co("dr_reddys", "Dr Reddy's Laboratories", "DRREDDY", ("Dr Reddy's", "Dr. Reddy's")),
    _co("eicher_motors", "Eicher Motors", "EICHERMOT", ("Eicher Motors", "Royal Enfield")),
    _co("eternal", "Eternal (Zomato)", "ETERNAL", ("Zomato", "Eternal Ltd", "Blinkit")),
    _co("grasim", "Grasim Industries", "GRASIM", ("Grasim",)),
    _co("hcltech", "HCLTech", "HCLTECH", ("HCLTech", "HCL Technologies", "HCL Tech")),
    _co("hdfc_bank", "HDFC Bank", "HDFCBANK", ("HDFC Bank",)),
    _co("hdfc_life", "HDFC Life", "HDFCLIFE", ("HDFC Life",)),
    _co("hero_motocorp", "Hero MotoCorp", "HEROMOTOCO", ("Hero MotoCorp",)),
    _co("hindalco", "Hindalco", "HINDALCO", ("Hindalco",)),
    _co("hul", "Hindustan Unilever", "HINDUNILVR", ("Hindustan Unilever",), ("HUL",)),
    _co("icici_bank", "ICICI Bank", "ICICIBANK", ("ICICI Bank",)),
    _co("indusind_bank", "IndusInd Bank", "INDUSINDBK", ("IndusInd Bank", "IndusInd")),
    _co("infosys", "Infosys", "INFY", ("Infosys",)),
    _co("itc", "ITC", "ITC", ("ITC Ltd",), ("ITC",)),
    _co("jio_financial", "Jio Financial Services", "JIOFIN", ("Jio Financial",)),
    _co("jsw_steel", "JSW Steel", "JSWSTEEL", ("JSW Steel",)),
    _co("kotak_bank", "Kotak Mahindra Bank", "KOTAKBANK", ("Kotak Mahindra Bank", "Kotak Bank")),
    _co("larsen", "Larsen & Toubro", "LT", ("Larsen & Toubro", "Larsen and Toubro"), ("L&T",)),
    _co("mahindra", "Mahindra & Mahindra", "M&M", ("Mahindra & Mahindra", "Mahindra and Mahindra"),
        ("M&M",)),
    _co("maruti", "Maruti Suzuki", "MARUTI", ("Maruti Suzuki", "Maruti")),
    _co("nestle_india", "Nestle India", "NESTLEIND", ("Nestle India",)),
    _co("ntpc", "NTPC", "NTPC", (), ("NTPC",)),
    _co("ongc", "ONGC", "ONGC", ("Oil and Natural Gas Corporation",), ("ONGC",)),
    _co("power_grid", "Power Grid", "POWERGRID", ("Power Grid",)),
    _co("reliance", "Reliance Industries", "RELIANCE",
        ("Reliance Industries", "Reliance Retail", "Jio Platforms", "Reliance Jio",
         "Mukesh Ambani"), ("RIL",)),
    _co("sbi", "State Bank of India", "SBIN", ("State Bank of India",), ("SBI",)),
    _co("sbi_life", "SBI Life", "SBILIFE", ("SBI Life",)),
    _co("shriram_finance", "Shriram Finance", "SHRIRAMFIN", ("Shriram Finance",)),
    _co("sun_pharma", "Sun Pharma", "SUNPHARMA", ("Sun Pharma", "Sun Pharmaceutical")),
    _co("tata_consumer", "Tata Consumer Products", "TATACONSUM", ("Tata Consumer",)),
    _co("tata_motors", "Tata Motors", "TATAMOTORS", ("Tata Motors", "Jaguar Land Rover"),
        ("JLR",)),
    _co("tata_steel", "Tata Steel", "TATASTEEL", ("Tata Steel",)),
    _co("tcs", "Tata Consultancy Services", "TCS", ("Tata Consultancy Services",), ("TCS",)),
    _co("tech_mahindra", "Tech Mahindra", "TECHM", ("Tech Mahindra",)),
    _co("titan", "Titan Company", "TITAN", ("Titan Company",), ("Titan",)),
    _co("trent", "Trent", "TRENT", ("Trent Ltd", "Zudio"), ("Trent",)),
    _co("ultratech", "UltraTech Cement", "ULTRACEMCO", ("UltraTech Cement", "UltraTech")),
    _co("wipro", "Wipro", "WIPRO", ("Wipro",)),
)  # fmt: skip

ENTITIES: tuple[Entity, ...] = _MACRO + _NIFTY50

# Every article from these sources is about this entity, whatever its headline says.
SOURCE_ENTITIES: dict[str, tuple[str, ...]] = {
    "rbi-press-releases": ("rbi",),
    "rbi-notifications": ("rbi",),
    "sebi": ("sebi",),
    "fed-press": ("fed",),
    "fed-speeches": ("fed",),
    "ecb-press": ("ecb",),
    "boe-news": ("boe",),
    "pib-finance": ("finmin",),
    "nse-announcements": ("nse",),
}


# --------------------------------------------------------------------------- event types
# Order matters: the first matching type wins, so specific types come before broad ones.

_SPECULATION = (
    r"\b(may|might|could|likely|unlikely|expected|expects?|seen|poll|preview|ahead of|"
    r"bets?|odds|economists|analysts say|what to expect|report|on tap|week ahead|"
    r"this week|next week|due (on|next)|awaits?|awaiting|looms?|looming|in focus|upcoming|"
    r"eyes?|in sight|on the radar)\b",
)

EVENT_TYPES: tuple[EventType, ...] = (
    EventType("routine_operation", "Routine central-bank operation", (
        r"auction of (state government securities|government (of india )?(stock|securities)|"
        r"\d+-day.{0,40}treasury bills)",
        r"treasury bills? auction", r"\bVRRR\b", r"variable rate (reverse )?repo",
        r"money market operations", r"weekly statistical supplement", r"reserve money",
        r"\bOMO (sale|purchase)", r"open market operations?", r"result of .{0,40}auction",
        r"cut-off (yield|price)",
    ), routine=True),
    EventType("rate_decision", "Interest-rate decision", (
        r"\b(keeps?|holds?|leaves?|raises?|hikes?|cuts?|lowers?|reduces?|trims?|maintains?)\b"
        r".{0,40}\b(repo|policy|interest|benchmark|key) rates?",
        r"\b(repo|policy|interest|benchmark|key) rates?\b.{0,30}"
        r"\b(unchanged|steady|cut|hiked|raised|lowered|reduced|on hold)\b",
        r"monetary policy (statement|decision|announcement)", r"\bFOMC statement",
        r"\bMPC (decision|outcome|announces)", r"policy rate decision",
    ), exclude=_SPECULATION, specific=True, anchor_kinds=("central_bank",)),
    EventType("rate_outlook", "Rate outlook / expectations", (
        r"\brate (cut|hike)s?\b", r"\b(repo|policy|interest) rates?\b",
    )),
    EventType("inflation_data", "Inflation data", (
        r"\b(CPI|WPI|retail|wholesale|consumer price|headline|core)\b.{0,15}inflation\b"
        r".{0,40}\b(at|to|rises?|rose|eases?|eased|falls?|fell|accelerates?|slows?|cools?|"
        r"quickens?|\d)",
        r"inflation (data|print|figures?|numbers?)",
    ), exclude=_SPECULATION, specific=True, anchor_kinds=("country", "central_bank")),
    EventType("gdp_data", "GDP data", (
        r"\bGDP\b.{0,30}\b(grows?|grew|growth|expands?|expanded|contracts?|contracted|data|"
        r"figures?|print)\b",
    ), exclude=_SPECULATION, specific=True, anchor_kinds=("country",)),
    EventType("economic_data", "Economic data release", (
        r"\bPMI\b", r"\bIIP\b", r"industrial (output|production)", r"jobs report",
        r"(non-?farm )?payrolls", r"unemployment rate", r"trade deficit", r"current account",
        r"core sector", r"forex reserves",
    )),
    EventType("currency_intervention", "Currency move / intervention", (
        r"\b(RBI|central bank)\b.{0,40}\b(interven\w+|steps in|support(s|ing)? (the )?rupee|"
        r"sells? dollars)",
        r"rupee.{0,30}\b(record|all-time|lifetime) (low|high)",
        r"rupee (hits|nears|slumps|plunges)",
    )),
    EventType("regulatory_change", "Regulatory change", (
        r"\b(SEBI|Sebi|RBI|regulator)\b.{0,60}\b(new (\w+ )?(rules?|norms|framework|"
        r"regulations)|tightens?|eases?|relaxes?|revises?|amends?|introduces?|proposes?|"
        r"bans?|restricts?|notif(y|ies))\b",
        r"\b\w+ regulations,? 20\d\d\b",
        r"\bcircular\b", r"consultation paper", r"master directions?", r"regulatory measures",
        r"\bframework for\b",
    )),
    EventType("regulatory_order", "Enforcement order", (
        r"(adjudication|settlement|exemption|revocation|interim|final|confirmatory) order",
        r"order in the matter of", r"show[- ]cause notice", r"\bappeal no\b",
        r"recovery (proceedings|certificate)", r"\bremittance\b.{0,40}against",
        r"(imposes?|levies|slaps?) (a )?(monetary )?penalty", r"penalty (on|of) ",
        r"enforcement actions?",
    ), routine=True),
    EventType("earnings_result", "Company results", (
        r"\bQ[1-4]\b.{0,25}\bresults?\b", r"quarterly results?",
        r"net profit.{0,30}\b(rises?|jumps?|falls?|surges?|declines?|drops?|grows?|up|down|"
        r"flat)\b",
        r"\b(profit|revenue|earnings)\b.{0,15}\b(beats?|misses?|rises?|jumps?|falls?|surges?|"
        r"declines?|drops?)\b",
    ), specific=True, anchor_kinds=("company",)),
    EventType("military_conflict", "Military conflict", (
        r"missiles?", r"air ?strikes?", r"drone (attack|strike)s?", r"invasion", r"shelling",
        r"bombing", r"military (strike|operation|escalation)", r"\bwar\b",
        r"killed in .{0,20}(attack|strike)", r"troops", r"ceasefire",
    )),
    EventType("sanctions", "Sanctions", (r"sanctions?",)),
    EventType("tariff_action", "Tariff / trade action", (
        r"\b(imposes?|raises?|hikes?|cuts?|announces?|slaps?|threatens?|lifts?)\b.{0,30}"
        r"\b(tariffs?|dut(y|ies))\b",
        r"tariffs? (on|of) ", r"trade (deal|pact|agreement) (signed|sealed|reached)",
    )),
    EventType("supply_disruption", "Supply disruption", (
        r"supply (disruption|shock|outage)", r"(output|production) cuts?",
        r"pipeline (attack|shutdown|outage)", r"strait of hormuz", r"refinery (fire|outage|"
        r"shutdown)", r"shipping (disruption|attack)", r"red sea",
    )),
    EventType("rating_action", "Credit rating action", (
        r"\b(upgrades?|downgrades?)\b.{0,30}\b(rating|outlook)\b",
        r"\b(Moody's|Fitch|S&P Global Ratings)\b",
    )),
    EventType("ipo_listing", "IPO / listing", (
        r"\bIPOs?\b", r"initial public offering", r"listing (day|gains?|price)",
        r"\blists? at\b", r"grey market premium|\bGMP\b",
    )),
    EventType("merger_acquisition", "Merger / acquisition", (
        r"\bacqui(res?|red|sition)\b", r"\bmerger\b", r"takeover", r"buyout",
        r"stake (sale|purchase|acquisition)", r"\bbuys? .{0,20}stake",
    )),
    EventType("corporate_action", "Dividend / buyback / split", (
        r"\bdividend\b", r"\bbuyback\b", r"bonus (issue|shares?)", r"stock split",
        r"record date",
    )),
    EventType("policy_announcement", "Government policy announcement", (
        r"\b(government|ministry|cabinet|centre)\b.{0,30}\b(approves?|announces?|notifies?|"
        r"launches?|unveils?)\b",
        r"\bGST Council\b.{0,40}\b(decisions?|meeting|approves?|cuts?|recommends?|rate)",
        r"\bscheme\b", r"\bnew policy\b",
    )),
)  # fmt: skip

TOPICS_BY_SLUG = {t.slug: t for t in TOPICS}
ENTITIES_BY_SLUG = {e.slug: e for e in ENTITIES}
EVENT_TYPES_BY_SLUG = {e.slug: e for e in EVENT_TYPES}
