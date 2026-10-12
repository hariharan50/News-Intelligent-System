# News Intelligence System

**Project description, scope and use cases (SaaS edition)**

Status: scope approved, Phase 0 in progress.
Last updated: 10 October 2026

---

## 1. The project in one paragraph

There is far too much news for one person to follow. The News Intelligence System is a **paid SaaS product**. Anyone can sign up, choose a plan and pay with Razorpay. The system collects news from many sources, throws away everything that does not matter to a user, and pushes what is left to a personal feed ranked by importance. Users describe what they care about in a **profile**. The first market is **Indian traders**: they see central-bank decisions, war and geopolitical events, crude oil, USD/INR, Nifty 50 company news and Indian regulatory news, and nothing else. Critical items, such as an RBI rate decision, are pushed as an alert straight away, in the app and on **Telegram**. Rules and AI (Claude) work together: rules decide quickly and explain why, and AI adds summaries, importance checks and an agent the user can ask questions.

---

## 2. Decisions made

| Question | Decision |
| --- | --- |
| Product type | Public multi-user SaaS: self sign-up, paid plans |
| First market | India |
| Payments | Razorpay (subscriptions, UPI Autopay, cards; GST invoices) |
| First audience | Indian markets traders (other templates such as exporter come later) |
| News sources | Free sources at launch; move to a paid, licensed news API when revenue or coverage needs it |
| AI provider | Claude (a small, fast model for summaries and tagging, a stronger model for the agent) |
| Alert channels at launch | In-app toast, browser notification, Telegram |
| Language | English only at first |

---

## 3. The problem it solves

| Problem today | What the system does about it |
| --- | --- |
| Hundreds of headlines a day, most of them irrelevant | Shows only stories that match the user's profile |
| The same event reported by ten outlets | Groups them into one story card listing every source |
| Important news is buried under routine news | Scores every story 0 to 100 and ranks the feed by that score |
| Critical events are noticed late | Pushes an alert to the app and Telegram when a story crosses the user's alert line |
| Long articles take time to read | AI writes a two-line summary and pulls out key facts |
| "Smart" feeds that cannot explain themselves | Every card shows why it was included |

---

## 4. Who it is for

- **Subscriber (main user).** An Indian trader first; later exporters, analysts and business owners. They sign up, pick a plan, build a profile and connect Telegram.
- **Administrator (us).** Manages sources, plans, users, ingestion health and AI cost.

---

## 5. Core design rules

1. **News is processed once and shared; scoring is per user.** Collecting, de-duplicating, tagging and AI summarising happen once per story, whatever the number of users. Only matching and scoring are done per profile. This keeps AI cost and server load flat as users grow.
2. **Rules decide, AI assists.** The rule-based pipeline produces every score and every alert. AI can adjust a score within limits, add summaries and answer questions, but if Claude is slow, down or over budget, news still flows with rule-based scores.
3. **No AI call while a user waits for a page.** AI results are computed in the background, stored and reused. The only exception is the AI agent, where the user explicitly asks a question.
4. **Multi-user from day one.** Every table holding user data carries the owner's id. Plan limits are checked on the server, never only in the browser.
5. **PostgreSQL holds everything that must survive.** Redis holds only what can be rebuilt.

---

## 6. How it works, from source to screen

```text
SHARED (once per story)
 Sources ──> Collect ──> Clean ──> De-duplicate ──> Group into ──> Tag ──> AI summary +
 (RSS,        poll on a                              stories                importance check
  official    schedule
  feeds)
                                                                              │
PER USER (only for profiles that match)                                       ▼
                       Find matching profiles ──> Score 0-100 ──> Feed (live) + Alerts (app, Telegram)
```

Matching uses an index ("which profiles follow entity RBI / topic Monetary policy") so a story is scored only against profiles that could care about it, not against every user.

---

## 7. News sources

### 7.1 Licensing rule

Once users pay, showing a publisher's content is commercial use. Most publisher RSS feeds allow personal, non-commercial use only, so at launch we:

- store and show **headline, source, time and link** for publisher feeds, plus our own tags, score and AI summary;
- write AI summaries **from the headline and the published RSS excerpt only**, never from scraped full articles;
- never bypass a paywall;
- check each source's terms before enabling it in production, and get a legal review before taking the first payment.

### 7.2 Launch sources (free)

| Group | Examples | Why |
| --- | --- | --- |
| Official, India | RBI press releases, SEBI, PIB (Ministry of Finance), NSE and BSE corporate announcements | Fastest and most market-moving; often the original source |
| Official, global | US Federal Reserve, US Treasury, OPEC press releases | Global rate and oil decisions |
| Indian business press (headline + link) | Mint, Business Standard, Economic Times, Moneycontrol | Coverage and confirmation |
| Global press (headline + link) | BBC Business and World | Geopolitics and wars |

**Reuters and Wall Street Journal** have no free feed suitable for commercial use (Reuters has no public RSS; WSJ is paywalled with personal-use terms). They are planned for the **paid phase** through a licensed news API or a direct licence.

### 7.3 Paid phase (later)

When revenue or coverage needs it, add a licensed news API behind the same adapter interface. No pipeline change is needed.

---

## 8. Plans and pricing (draft)

Prices are placeholders, to be decided before Phase 7.

| | Free | Pro | Pro+ |
| --- | --- | --- | --- |
| Feed delay | 15 minutes | Real time | Real time |
| Profiles | 1 | 3 | 5 |
| In-app alerts | Yes | Yes | Yes |
| Telegram alerts | No | Yes | Yes, with priority sending |
| AI summaries | Headlines only | Yes | Yes |
| AI agent (ask the news, daily brief) | No | Limited | Yes |
| Saved stories | 50 | Unlimited | Unlimited |

Plans are stored as data (plan → features and limits), so they can be changed without code changes.

---

## 9. Features in scope

### 9.1 Accounts and billing
- Sign up with email and password (with email verification) or Google.
- Log in, log out, reset password.
- **JWT + session authentication:** short-lived JWT access tokens (about 15 minutes) for API calls, plus a server-side session for each device. The session is stored in PostgreSQL, cached in Redis and referenced by a rotating refresh token in an httpOnly cookie. This allows real logout, "log out of all devices", a list of active devices, and instant revocation when an account is blocked or compromised.
- Choose a plan and pay with Razorpay; upgrade, downgrade, cancel; GST invoices.
- Free trial of Pro (length to be decided).
- Plan limits enforced by the server.

### 9.2 Ingestion
- Pluggable source adapters: RSS, official feeds, and later a paid news API.
- Scheduled polling per source (every 30 seconds for priority sources), per-source rate limits, retries and back-off.

### 9.3 Processing
- Cleaning and normalising every item.
- Exact and near-duplicate detection across sources; one story per event.
- Tagging by topic, region, event type and entity (Nifty 50 companies, RBI, SEBI, Brent crude, USD/INR and other macro entities).

### 9.4 Profiles
- Topics, keywords, entities, sources, regions; include and exclude rules; mute lists (optionally time-limited).
- Priority weights for each rule.
- Show threshold and alert threshold.
- Pinned rules that guarantee an alert for named events (for example an RBI rate decision).
- An "Indian markets trader" template to start from.
- Live preview of the last 24 hours before saving a change.

### 9.5 Importance and scoring
- A score from 0 to 100 for each story and profile.
- Tiers: **Critical** (80+), **High** (60–79), **Normal** (40–59), **Low** (20–39).
- A "why" line on every card.
- AI importance check on borderline stories: may move a score by at most 15 points and can never create an alert on its own.

### 9.6 Feed
- Ranked by score and freshness; filters (tier, topic, entity, source, region, unread, saved) and search.
- Read and saved state; live updates without a page refresh.

### 9.7 Alerts
- In-app toast and browser notification.
- **Telegram**: the user clicks "Connect Telegram", opens our bot through a one-time link, and alerts arrive in their chat.
- One alert per story per user; later sources reporting the same event do not trigger a second alert.
- Quiet hours (optional) and per-channel minimum tier.
- Built on a single notifier interface so email or WhatsApp can be added later.

### 9.8 AI features (Claude)
- **Summary and key facts** for each story: who, what, numbers, dates, affected markets or sectors. Computed once and shared by all users.
- **Importance check** on borderline stories (see 9.5).
- **AI agent**: the user asks questions such as "What happened with crude today?" or "Anything on HDFC Bank this week?", answered from stored stories with links. Plus an optional pre-market daily brief.
- Daily global budget, per-plan limits and an admin off switch.
- Any "likely impact" text is labelled as the model's opinion, not trading advice.

### 9.9 Feedback
- Mark a story "relevant" or "not relevant"; matching rule weights change in small, visible, resettable steps.
- After repeated "not relevant" marks the app suggests muting.

### 9.10 Administration
- Sources: add, test, enable, disable; ingestion health (status, delay, error rate, failed jobs).
- Users and subscriptions.
- AI usage and cost by day; global AI switch.

---

## 10. Out of scope (for now)

| Not included | Note |
| --- | --- |
| Live prices or price-based alerts | News only |
| Placing trades or giving trading advice | AI impact notes are labelled as opinion |
| Showing full article text | Headline, excerpt and link only, unless licensed |
| Languages other than English | Later |
| Mobile apps | Responsive web app; Telegram covers mobile alerts |
| Social media as a source | Later |
| Payment providers other than Razorpay | Stripe can be added for international customers later |
| Kubernetes or multi-server deployment | One server with Docker Compose first |

---

## 11. Use cases

### Use case 1: A trader catches an RBI rate decision
1. RBI publishes a policy decision on its press-release feed; we poll it every 30 seconds.
2. The story is tagged "Monetary policy", "RBI", "rate decision".
3. For the trader's profile it scores 89; a pinned rule lifts it to 95.
4. An alert appears in the app and on Telegram; the story sits at the top of the feed as Critical.
5. The card shows why: *Pinned: RBI rate decision · Topic: Monetary policy · Entity: RBI · Keyword: repo rate · Source: RBI*.
6. A few seconds later Claude adds a two-line summary with the new repo rate.
7. As Mint and Business Standard report it, they join the same story card. No second alert.

### Use case 2: Sign up and pay
A trader lands on the website, signs up with Google, starts from the trader template, and connects Telegram. They choose Pro and pay with UPI Autopay through Razorpay. Real-time alerts switch on immediately.

### Use case 3: Morning catch-up
The trader opens the feed, sorts by "Top", filters to unread, and reads the last 12 hours ranked by importance, with an AI summary on each important story. Or they read the pre-market brief Claude wrote at 8:30.

### Use case 4: Asking the news
"What moved crude this week?" The AI agent answers in a few lines, citing the stories it used, each with a link.

### Use case 5: Tuning out noise
A user marks two stories about a company they do not follow as "not relevant". The rule weight drops a little each time. After a third, the app suggests muting the company.

### Use case 6: Changing what matters
A crisis starts in a region the user did not track. They add the region and two keywords, check the 24-hour preview, and save. The feed re-scores recent stories within seconds.

### Use case 7: Claude is down
The AI provider fails for an hour. Stories, scores, the feed and alerts keep working. Summaries are missing for that hour and are filled in when Claude is back.

### Use case 8: A payment fails
A UPI Autopay renewal fails. Razorpay retries; the user is told in the app and by email. After the grace period the account drops to the Free plan. No data is lost.

---

## 12. Scoring, in plain terms

Each story gets a score for each profile from two things:

- **Relevance:** how well the story matches the profile: topic, entity, keyword, source or region. Several matches reinforce each other; one strong match is enough.
- **Importance:** how big the story is for anyone: source authority, event type, how many outlets carry it, and whether it was unscheduled.

The score is relevance scaled by importance. Importance can reduce a relevant story's score but cannot lift an irrelevant one into the feed. A story that only matches "it is from India" can never score above 25.

Example for the trader template (show at 50, alert at 80):

| Story | Score | Result |
| --- | --- | --- |
| RBI policy rate decision, from RBI's own feed | 89, pinned to 95 | Critical; alert sent |
| Brent crude jumps after a supply disruption, 3 sources | 77 | High; shown; no alert |
| RBI routine reverse-repo auction result | 52 | Normal; shown low in the feed |
| Cricket auction story from an Indian outlet | 19 | Hidden |

---

## 13. Technology

| Part | Technology | Role |
| --- | --- | --- |
| Backend | FastAPI (async Python 3.12) | REST API and live update stream |
| Database | PostgreSQL 16 | Single source of truth |
| Cache and queues | Redis 7 | Job queues, rate limits, locks, caches, live event broadcast |
| Background work | Taskiq workers on Redis | Ingestion, processing, scoring, alerts, AI jobs |
| Frontend | React + TypeScript (Vite) | Website, feed, profile editor, billing pages, admin |
| Live updates | Server-Sent Events | New stories and alerts to the browser |
| Payments | Razorpay | Subscriptions and invoices |
| Alerts | Telegram Bot API | Mobile alerts |
| AI | Claude (Anthropic API) behind a provider interface | Summaries, importance checks, agent |
| Packaging | Docker and Docker Compose | Whole system starts with one command |

Containers: `postgres`, `redis`, `migrate` (runs database migrations, then exits), `api`, `scheduler`, `worker`, `worker-ai` (added in Phase 6), `web`.

---

## 14. Build plan

Each phase ends with something testable and a pause for review.

| Phase | What gets built | What you can test at the end |
| --- | --- | --- |
| **0. Foundation** | Repository, Docker setup, configuration, migrations, health checks, React skeleton | `docker compose up` brings every service up healthy |
| **1. Ingestion** | Source adapters (official + RSS), scheduler, rate limits, retries | News flows into the database; re-polling adds no duplicates |
| **2. Processing** | Duplicate detection, story grouping, tagging (Nifty 50 + macro entities) | One event from several outlets becomes one story |
| **3. Accounts and profiles** | Sign-up, login, profiles, trader template, scoring engine | Each user gets their own ranked feed with reasons |
| **4. Feed UI and live updates** | React feed, filters, search, profile editor | New stories appear without a refresh. **Internal MVP** |
| **5. Alerts and Telegram** | In-app, browser and Telegram alerts; feedback | An RBI decision reaches Telegram within one poll interval, exactly once |
| **6. AI summaries and importance** | Claude provider, shared summaries, importance check, budgets | Summaries appear; switching AI off changes nothing else |
| **7. Billing and plans** | Razorpay subscriptions, plan limits, pricing pages, invoices | **Public beta: people can pay** |
| **8. AI agent** | Ask-the-news, daily brief | Agent answers with sources |
| **9. Hardening and launch** | Load tests, security review, legal pages, monitoring, backups | **Public launch** |

---

## 15. What "good" looks like

- A new item is visible in the feed within 5 seconds of being fetched (95th percentile).
- A priority-source alert reaches Telegram within one poll interval (30 seconds) of publication.
- No story is lost when a worker crashes, Redis is wiped or the database restarts.
- With AI switched off, every non-AI feature works.
- AI cost per active user stays inside the plan's margin.

---

## 16. Risks

- **Source licensing.** The main business risk. Mitigated by headline-and-link display, official sources, legal review before launch and a planned move to licensed APIs.
- **Speed of sources.** RBI and most sources are polled, not pushed, so "immediately" means within one poll interval.
- **Duplicate handling.** Grouping can merge two stories or split one. The rules lean towards splitting.
- **Tagging quality.** New companies or themes are invisible until added to the entity lists; users can add their own keywords.
- **AI cost.** Shared summaries, budgets and plan limits cap it; real volume is measured in Phase 6.
- **Payments and compliance.** GST invoicing, refunds and India's DPDP Act need to be handled before taking money.

---

## 17. Open questions

| # | Question | Assumed for now |
| --- | --- | --- |
| 1 | Plan prices and trial length | To decide before Phase 7 |
| 2 | Data retention | Stories 90 days; saved stories while the account exists |
| 3 | Do war and supply-shock events get a guaranteed alert like rate decisions? | No; they alert only at 80+ |
| 4 | Feedback changes weights automatically, or only suggests? | Automatic, small, visible, resettable |
| 5 | Hosting provider for production | One VPS in India running the same Docker setup |

---

## 18. Glossary

| Term | Meaning |
| --- | --- |
| **Source** | Where news comes from: an RSS feed, an official feed, a news API |
| **Adapter** | Code that knows how to read one kind of source |
| **Article** | One item from one source |
| **Story** | One real-world event, grouping every article that reports it |
| **Profile** | A user's description of what matters: rules, weights, thresholds |
| **Template** | A ready-made profile, such as "Indian markets trader" |
| **Entity** | A named thing the system recognises: RBI, SEBI, Brent crude, USD/INR, Reliance |
| **Score** | 0 to 100, how much a story matters to a profile |
| **Tier** | Critical, High, Normal or Low |
| **Alert threshold** | The score a story needs to be pushed as an alert |
| **Pinned rule** | A rule that guarantees a minimum score for a named event |
| **Plan** | Free, Pro or Pro+, with its features and limits |
