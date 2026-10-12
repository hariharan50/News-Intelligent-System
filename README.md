# News Intelligence System
http://localhost:8081/

[![CI](https://github.com/hariharan68/News-Intelligentce-System/actions/workflows/ci.yml/badge.svg)](https://github.com/hariharan68/News-Intelligentce-System/actions/workflows/ci.yml)

A SaaS product that pushes the news that matters to each user, ranked by importance, with alerts in the app and on Telegram. It starts with Indian traders.
The product scope is in [news-intelligence-system.md](news-intelligence-system.md).

**Current phase:** 5 (Alerts, Telegram, feedback, admin) complete. The rule-based product is complete: sign up, read your ranked feed, get alerts in the app, the browser and Telegram.

## Stack

| Part | Technology |
| --- | --- |
| API | FastAPI (Python 3.12), SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 16 |
| Cache and queues | Redis 7 |
| Background jobs | Taskiq on Redis Streams (`scheduler` + `worker`) |
| Frontend | React + TypeScript (Vite), served by nginx |
| Packaging | Docker Compose |

## Run everything

Requires Docker Desktop.

```bash
cp .env.example .env    # then set POSTGRES_PASSWORD (letters and digits only)
docker compose up -d --build
```

| What | URL (default ports; change them in `.env`) |
| --- | --- |
| Web app | http://localhost:8080 |
| API docs | http://localhost:8000/api/docs |
| Liveness | http://localhost:8000/api/health/live |
| Readiness | http://localhost:8000/api/health/ready |

`docker compose ps` should show every service `healthy`, and `migrate` as `Exited (0)`. The worker turns healthy after its first scheduled heartbeat, which takes up to about a minute.

## Services

| Service | Role |
| --- | --- |
| `postgres` | All persistent data |
| `redis` | Task queue, caches, locks, live events |
| `migrate` | Runs `alembic upgrade head`, then exits; other services wait for it |
| `api` | FastAPI |
| `scheduler` | Enqueues scheduled tasks. **Run exactly one.** |
| `worker` | Runs pipeline tasks |
| `web` | nginx serving the React build and proxying `/api` to `api` |

## How ingestion works

Every 5 seconds the `ingest.dispatch_due` task claims sources whose `next_poll_at` has passed. It uses PostgreSQL `FOR UPDATE SKIP LOCKED` with a lease. It then enqueues one `ingest.poll_source` task per claimed source. A poll:

1. takes a per-source Redis lock, so a duplicate task does nothing;
2. waits for a per-host slot (at most one request per host per second, across all workers);
3. does a conditional GET (`ETag` / `If-Modified-Since`), so an unchanged feed costs a 304;
4. parses and normalises each item: plain text, canonical URL without tracking parameters, and dates in UTC (a timezone-less date uses the source region's zone, e.g. IST);
5. inserts new articles with `ON CONFLICT DO NOTHING` on `(source_id, guid)` and `url_hash`, so re-polls and overlapping feeds never create duplicates;
6. records the attempt in `source_fetches` and updates the source's health. Failures back off exponentially (honouring `Retry-After`); a source becomes `degraded` after one failure and `failing` after 10.

Launch sources are in `backend/app/ingestion/seed.py`. The `migrate` container inserts any missing ones and never overwrites edits. Sources that refuse automated clients are seeded **disabled** with a note; we don't disguise the crawler to get around that.

## How processing works

When a poll stores new articles, it enqueues `process.pending`, which also runs every 30 seconds as a safety net. One lane, held by a PostgreSQL advisory lock, takes unprocessed articles oldest first and handles each in three steps:

1. **Tag** it with rules from `backend/app/processing/taxonomy.py`:
   - topics (monetary policy, energy, FX…);
   - entities (RBI, SEBI, the Fed, Nifty 50 companies, crude, USD/INR, countries);
   - one event type, read from the headline (rate decision, results, enforcement order, routine operation…);
   - regions.

   Bump `TAXONOMY_VERSION` after changing matching rules.
2. **Cluster** it into a story (`clustering.py`). The comparison uses significant words in the headline, not raw characters, plus tags. Articles join a story when:
   - the headline is identical; or
   - the headlines are very similar; or
   - the headlines are fairly similar and share a specific entity or event type; or
   - the event type is the same specific kind (such as a rate decision) about the same central bank or company, within 24 hours.

   Two guards keep stories apart: one publisher's feeds never merge with each other unless the headline is identical, and headlines about different actors of the same kind ("Fed cuts…" vs "ECB cuts…") never merge. When unsure, the rules split.
3. **Refresh** the story: the lead headline comes from the most authoritative source (RBI's own release beats press coverage), plus the union of tags, source and article counts, and whether the story is routine.

`python -m app.cli reprocess` rebuilds all stories, for use after taxonomy changes. Inside Docker: `docker compose exec api python -m app.cli reprocess`.

## Authentication (JWT + session)

| Piece | What it is |
| --- | --- |
| Access token | JWT (HS256), valid for 15 minutes, sent as `Authorization: Bearer …`. It carries the user id and session id (`sid`). |
| Session | One `user_sessions` row per signed-in device. Every request checks the session is still active (cached in Redis for up to 5 minutes; revoking deletes the cache key), so logout and revocation take effect immediately. |
| Refresh token | Random, in an httpOnly, SameSite=Strict cookie scoped to `/api/auth`, stored only as a SHA-256 hash. It rotates on every refresh. An already-rotated token presented more than 20 seconds after rotation revokes the whole session (theft detection). |
| Passwords | Argon2id. Login is rate-limited per email and IP, and unknown emails take as long as wrong passwords. |

Endpoints:
- `POST /api/auth/register`, `/login`, `/token` (form login for the API docs' Authorize button), `/refresh`, `/logout`, `/logout-all`
- `GET /api/auth/me`, `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}`

`JWT_SECRET` must be set to a long random value; staging and production refuse to start with the development default.

### Continue with Google (single sign-on)

Google only proves who the person is. After that, they get the same session as above: a 15-minute JWT and a rotating refresh cookie, which can be listed and revoked.

Flow: `GET /api/auth/google/start` redirects to Google, and Google sends the browser back to `GET /api/auth/google/callback`. It is the OpenID Connect authorization-code flow, run on the server; the page loads no Google script.

| Check | Why |
| --- | --- |
| PKCE (S256) | A stolen authorization code can't be redeemed without the verifier, which never leaves the server. |
| `state`, single use, 10 minutes, in Redis and in a `SameSite=Lax` cookie scoped to `/api/auth/google` | Stops login CSRF: the callback must come back to the same browser that started it. `GETDEL` blocks replay. |
| `nonce` inside the ID token | Ties the token to this sign-in, so an old token can't be replayed. |
| ID token signature against Google's published keys (RS256 only), `iss`, `aud` = our client id, `exp`/`iat` | A forged or foreign token is rejected. `alg: none` and HS256 tricks fail. |
| `email_verified` must be true | An unverified Google address can't take over an account. |
| Linking by email (Google ID `sub` stored in `user_identities`) | A verified Google email signs in to the existing account. If that account's email was never confirmed, its password, 2FA and sessions are wiped first, so anyone who registered with that address before its real owner (pre-hijacking) is locked out. |
| New accounts need consent | Only the sign-up page (after the Terms and risk-notice boxes are ticked) may create an account. |
| Two-step sign-in still applies | Accounts with 2FA get the code step. The challenge travels in the URL `#fragment`, which is never sent to a server or written to logs. |
| `next` checked with `safe_next()` | Only same-site paths, so the flow can't be used as an open redirect. |
| Rate limit (30 per 10 minutes per IP) and errors | Failures redirect with a short code (`/login?sso_error=…`); details go only to the server log. |

Accounts created with Google have no usable password (`has_password=false`). "Forgot password" adds one. My account → **Sign-in methods** shows both methods. Google can be disconnected only when a password exists.

Setup: create a Web OAuth client in Google Cloud. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and register `<APP_BASE_URL>/api/auth/google/callback` (exactly) as the redirect URI. `GET /api/meta/features` reports `google: true` once it is configured, and the buttons appear.

## Profiles, scoring and the feed

Every new account gets a copy of the **Indian markets trader** template (`backend/app/scoring/templates.py`). It contains central banks, the rupee, crude, geopolitics, SEBI and all Nifty 50 companies, and pins RBI rate decisions at 95 or more and Fed rate decisions at 85 or more.

A profile's rules are topic, entity, event type, keyword, source or region rules, each with a weight from 1 to 10. Each rule is one of three modes:
- `include` adds relevance;
- `exclude` hides matching stories, and is a mute when it has `expires_at`;
- `pin` guarantees a minimum score; event-type pins can be narrowed to one entity ("RBI rate decision").

**Score** = relevance × importance (`backend/app/scoring/engine.py`):
- Relevance combines the matched rules: several matches reinforce each other, and one strong match is enough. Matching only a region or a source caps relevance at 25.
- Importance, a factor from 0.5 to 1.0, comes from event type, coverage (how many outlets carry the story) and source authority. It can halve a story's score but never lift an irrelevant one.
- Tiers: Critical 80+, High 60–79, Normal 40–59, Low 20–39.

Scores are stored with their reasons in `story_scores` whenever a story or a profile changes. Reading the feed is a plain indexed query.

Endpoints:
- `GET /api/feed?profile_id=&sort=top|latest&since_hours=&tier=&topic=&entity=&event_type=&include_routine=&limit=&offset=`
- `GET|POST /api/profiles`, `GET|PUT|DELETE /api/profiles/{id}`
- `POST /api/profiles/preview` scores the last N hours against unsaved rules
- `GET /api/meta/taxonomy`, `/api/meta/templates`, `/api/meta/sources` (public)

After changing the scoring weights, run `docker compose exec api python -m app.cli rescore`.

**Try it:** open http://localhost:8000/api/docs (use your `API_HOST_PORT`), click **Authorize**, and sign in. A local demo account's credentials are in `.env` as `DEMO_EMAIL` / `DEMO_PASSWORD`; that file is git-ignored.

## Alerts

When a story crosses a profile's alert line, `alerts/service.py` creates an alert **once per user per event**:
- the same story never alerts twice;
- a second story about the same event, within 6 hours, is grouped instead of alerted again. "Same event" means the same event type and a shared company, institution, commodity or currency, for example three outlets on the RBI's rupee move.

Only stories published in the last 24 hours alert.

Each alert is delivered:
- **in the app**, as a live toast plus the Alerts inbox at `/alerts`;
- **in the browser**, as a system notification when the tab is in the background (the user turns this on in Notifications);
- **on Telegram**, if the user connected it.

Telegram delivery is a sweep every 5 seconds over alerts marked `pending`, so a crash delays alerts but never loses them. It stays under Telegram's limits (1 message per second per chat, about 25 per second in total) and honours `retry_after`. A chat that blocked the bot is disconnected automatically. **Quiet hours** hold back Telegram alerts at night, except pinned events.

### Telegram alerts: setup

1. In Telegram, open **@BotFather**, send `/newbot`, and pick a name and a username (it must end in `bot`).
2. Put the token and the username, without the `@`, in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_BOT_USERNAME=YourNewsBot
   ```
3. Restart: `docker compose up -d`. The `telegram` service receives `/start` messages through long polling, so no public URL is needed. Run exactly one.
4. Each user goes to **Notifications → Connect Telegram**. That opens a one-time `t.me` link, valid for 15 minutes. Pressing **Start** connects the chat.

Commands the bot understands: `/status` and `/stop`.

## Feedback

👍/👎 on a story moves the weights of the profile rules that matched it by one step. Learning never moves a weight more than 3 away from what the user set. The editor marks learned weights ("learned · was 5") and can reset them. After 3 "not relevant" verdicts on stories about the same company or institution within 30 days, the app offers to mute it.

## AI summaries (Claude)

The worker writes a short summary, up to 4 key facts and "may affect" tags (labelled as AI opinion) for each recent story that scores 60+ (High tier) for at least one profile. Each summary is written once and shared by every user. It uses only the headlines and feed excerpts already stored, never scraped articles. When more reports join a story, its summary is rewritten (at most 3 times, at most once every 10 minutes).

The same call also rates **market impact** from 1 to 5 (the AI importance check). This moves the story's score for every profile: −15, −8, 0, +8 or +15 points. It changes ranking and which feeds the story reaches, and it appears on the card, for example "AI: major market impact (+8)". A pin's guaranteed score always holds. **Alerts ignore it**: they use the rule-only score, so AI can never cause or block an alert. Set `AI_IMPORTANCE_ENABLED=false` to keep summaries without the adjustment, or change the limit with `AI_MAX_ADJUSTMENT`.

Rules decide and AI assists. Scores, the feed and alerts never wait for or depend on AI. If there is no key, the switch is off, Claude is down or the budget is used up, stories just show no summary.

Setup: put `ANTHROPIC_API_KEY` in `.env` and run `docker compose up -d`. Optional settings:
- `AI_SUMMARY_MODEL`: default `claude-opus-5-5`. `claude-haiku-5-5` is about 40× cheaper per story.
- `AI_DAILY_BUDGET_USD`: default 3. New AI work stops for the day (India time) once estimated spend reaches it.

Admin → **AI** shows the on/off switch, today's spend against the budget, 14 days of usage and recent errors.

## AI agent: Ask the news and the daily brief

**Ask the news** (`/ask`) lets users ask questions such as "What moved crude this week?" or "Anything on HDFC Bank?".
- Claude answers by searching only the stories we've collected. It has three read-only tools: `search_stories`, `top_stories` (ranked by the user's own profile) and `get_story`.
- Every claim is cited, and each citation links to the original article. Claude never answers from memory, and it never gives investment advice.
- The answer streams in as it is written, and status lines such as "Searching stories: crude oil" show progress. Follow-up questions work.
- Daily limits: Pro gets 10 questions a day, Pro+ gets 50, and Free has no access. The limits reset at midnight IST.

**Pre-market brief** (Pro+):
- It is generated at 08:30 IST on weekdays from the top stories of the user's main profile in the last 18 hours.
- It appears at the top of the feed and is sent to Telegram if the user has connected it.

**Cost control:**
- The agent and the brief share their own daily budget, `AI_AGENT_DAILY_BUDGET_USD`, separate from the summaries' budget.
- The admin AI switch turns both off.

## Plans and billing (Razorpay)

There are three plans, stored as data in the `plans` table: **Free**, **Pro** and **Pro+**. The server enforces every limit:

| | Free | Pro | Pro+ |
|---|---|---|---|
| Feed | 15 minutes delayed | Real time | Real time |
| Profiles | 1 | 3 | 5 |
| Telegram alerts | No | Yes | Yes |
| AI summaries | No | Yes | Yes |
| Saved stories | 50 | Unlimited | Unlimited |

In-app alerts are real time on every plan. New accounts get **Pro free for 7 days** (`TRIAL_DAYS`). When a trial or subscription ends, the account drops to Free and no data is deleted.

Payments use Razorpay Subscriptions:
1. The user picks a plan. The server creates the subscription and Razorpay Checkout opens (UPI Autopay, cards, net banking).
2. After payment, the server checks Razorpay's signature and fetches the subscription from Razorpay. The browser's word is never trusted.
3. Webhooks keep it current: renewals, failed payments, cancellations. Each event is processed once, and its signature is checked.
4. A worker task re-syncs subscriptions every 10 minutes. This covers local development, where webhooks can't reach you, and any missed webhook.

Upgrades take effect immediately, with Razorpay prorating. Downgrades and cancellations take effect at the end of the paid period. If a renewal fails, the user keeps the plan for `BILLING_GRACE_DAYS` (3) while Razorpay retries. Razorpay issues the GST invoices, which are linked from **Plan & billing**.

Setup, in test mode first:
1. Razorpay dashboard → Subscriptions → Plans: create monthly **Pro** and **Pro+** plans. Their amounts must match the prices in the `plans` table (₹499 and ₹999 are placeholders).
2. Put `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_PLAN_PRO` and `RAZORPAY_PLAN_PRO_PLUS` in `.env`.
3. Webhooks: add `https://<your domain>/api/billing/webhook` with a secret (`RAZORPAY_WEBHOOK_SECRET`) and select the `subscription.*` events.
4. Enable GST invoicing in the Razorpay dashboard.

Without keys, the app still runs: billing shows "not configured" and users stay on their trial or on Free.

## Emails

The app sends these emails:
- **Confirm your email** after sign-up. A banner in the app offers "Resend email" until the address is confirmed. Paying requires a confirmed email.
- **Reset password**. On the sign-in page, "Forgot password?" sends a link that works once, for 60 minutes. Resetting signs the account out on every device. The response is identical whether or not an account exists, so it can't be used to discover accounts.
- **Your password was changed**: a security notice after any password change or reset.
- **Your trial ends tomorrow**: sent once per trial.
- **Your renewal didn't go through**: sent once per failed renewal.

How sending works:
- Emails go into an `email_outbox` table. The worker sends them within about 5 seconds and retries with backoff, so a slow mail server never slows down a request.
- Links point to `APP_BASE_URL`.
- Link tokens are random and single-use, and only their SHA-256 hash is stored.
- Sending is rate-limited.

**Development:** with `EMAIL_PROVIDER=console`, nothing is actually sent. Emails appear in `docker compose logs worker` and at http://127.0.0.1:8001/api/dev/emails, so you can open the links.

**Real sending:** any SMTP service works. Set `EMAIL_PROVIDER=smtp` and the `SMTP_*` values. `.env.example` shows the settings for these free options:
- **Brevo**: 300 emails per day
- **Gmail**: about 500 per day, using an App Password
- **Resend**: 3,000 per month

For production, use a sender address on your own domain, with SPF and DKIM set up at the provider.

## Security

**Two-step sign-in (2FA).**
- Any user can turn it on in **My account**: scan a QR code with an authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password).
- Signing in then needs the password plus a 6-digit code. Ten one-time **recovery codes** cover a lost phone.
- Turning 2FA on signs out the account's other devices.
- **Administrators must use it** outside local development (`ADMIN_REQUIRE_2FA`).
- Secrets are encrypted at rest with AES-GCM (`DATA_ENCRYPTION_KEY`).
- A code can't be reused, and a sign-in attempt allows 5 wrong codes before starting over.

**Admin audit log.** Every admin change is recorded with who, what, the before and after values, and the IP address, and is shown in Admin → **Audit log**. This covers users (disable, admin), sources (add, change, poll, test URL) and the AI switch.

**Headers.** nginx sends a content security policy (only our scripts, plus Razorpay Checkout), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy` and `Permissions-Policy`, and hides its version.

**Production checks.** With `ENVIRONMENT=production`, the API refuses to start if any of these is wrong:
- `APP_BASE_URL` isn't `https://`
- `CORS_ORIGINS` includes localhost
- secure cookies are switched off
- `DATA_ENCRYPTION_KEY` is missing
- email isn't set to SMTP
- admin 2FA is disabled

The API docs and `/api/dev/*` exist only in local and test.

## Admin

Make your account an administrator:

```bash
docker compose exec api python -m app.cli make-admin you@example.com
```

`/admin` then shows four tabs:
- **Overview:** pipeline heartbeat, articles, alerts, users, paying users and MRR, source health, Telegram and AI status.
- **Sources:** 24-hour fetch stats; enable or disable; poll now; **Add source**, with a **Test feed** preview of what would be collected.
- **Users:** search; see each user's plan; disable (signs them out everywhere immediately); make or remove admin.
- **AI:** global switch, daily budget and usage (see above).

Feed URLs are fetched only if they resolve to public addresses, including through redirects. A source can't make the server reach Redis, the database or cloud metadata (SSRF protection).

## Web app

| Page | What it does |
| --- | --- |
| `/signup`, `/login` | Create an account (it starts with the trader profile) or sign in. |
| `/` Feed | Ranked stories. It has Top/Latest sorting, a time window, tier, unread, hide-routine and text search filters, and clickable topic/entity chips. Each story shows its "why", coverage, read/save, and mark-all-read. Filters live in the URL, so a view can be bookmarked. |
| `/saved` | Saved stories, kept indefinitely. |
| `/profiles`, `/profiles/:id` | Profile list and editor: thresholds, pinned / followed / muted rules with weights, add-rule form, and a live preview of the last 24 hours. Leaving with unsaved changes asks first. |
| `/settings/devices` | Signed-in devices; sign one out, or sign out everywhere. |
| `/system` | Pipeline health, sources and unranked stories (development). |

**Auth in the browser:** the access token is held in memory only. The httpOnly refresh cookie restores the session on reload and refreshes the token a minute before it expires; any 401 triggers one shared refresh and a retry.

**Live updates:** `GET /api/stream` is a Server-Sent Events stream read with `fetch()`, so the token goes in the `Authorization` header, never in a URL. After scoring, the worker publishes a per-user event through Redis pub/sub, and the page shows an "N new stories" banner plus a toast for alert-level stories. Only stories published in the last 24 hours are announced, and only when a story is new to a feed, crosses the alert line, or rises by 5 or more points. Re-scoring (`reprocess`, `rescore`) is silent. nginx passes the stream through unbuffered.

**Read/saved API:** `PUT|DELETE /api/stories/{id}/read`, `PUT|DELETE /api/stories/{id}/saved`, `POST /api/feed/mark-read`, `GET /api/saved`. The feed accepts `unread_only=true` and `q=<text>`.

## Development endpoints

Mounted when `ENVIRONMENT=local`:
- `GET /api/dev/sources`
- `GET /api/dev/articles?limit=50&source=<slug>`
- `GET /api/dev/stories?topic=&entity=&event_type=&include_routine=&multi_source_only=`
- `GET /api/dev/taxonomy`

## Tests

Unit tests need nothing running. Integration tests use the Docker PostgreSQL and Redis: a separate `nis_test` database and Redis DB 15. Without them, those tests are skipped.

```bash
docker compose up -d postgres redis
cd backend && uv run pytest
```

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main`, on every pull request, and on demand from the Actions tab. A newer push cancels the older run. All three jobs must pass:

| Job | Checks |
| --- | --- |
| Backend | ruff lint and format; `alembic upgrade head` on an empty database; `alembic check` (models match migrations, so a forgotten migration fails); full downgrade and re-upgrade; the whole test suite against real PostgreSQL 16 and Redis 7 |
| Frontend | `npm ci`, type-check, lint, production build |
| Docker | `docker-compose.yml` is valid, and both images build (layers cached between runs) |

CI only reads the code: it publishes and deploys nothing. Deployment (CD) is planned for Phase 9.

Run the same checks locally before pushing:

```bash
# from ./backend (needs: docker compose up -d postgres redis)
uv run ruff check . && uv run ruff format --check . && uv run alembic check && uv run pytest

# from ./frontend
npm run typecheck && npm run lint && npm run build
```

## Develop without Docker

Use this while coding: the backend and frontend reload as soon as you save. Only PostgreSQL and Redis run in Docker. Don't run it together with the full `docker compose up`, or two workers would fetch the same news.

**One-time setup** (both files are git-ignored):

- `backend/.env`: `ENVIRONMENT=local`, plus `DATABASE_URL`, `REDIS_URL` and `JWT_SECRET` pointing at the Docker ports, for example `postgresql+asyncpg://nis:<password>@127.0.0.1:<POSTGRES_HOST_PORT>/nis`.
- `frontend/.env.local`: `VITE_API_PROXY_TARGET=http://127.0.0.1:<backend port>`. Without it, Vite uses port 8000.

On Windows, write `127.0.0.1`, not `localhost`. `localhost` tries IPv6 first, and Docker only listens on IPv4, so every connection waits about 2 seconds.

**Run** (separate terminals):

```bash
docker compose up -d postgres redis migrate                               # once: database + Redis

# from ./backend
uv run uvicorn app.main:app --reload --port 8002                          # API (pick a free port)
uv run taskiq worker app.worker.broker:broker app.worker.tasks --workers 1  # fetches + scores news
uv run taskiq scheduler app.worker.scheduler:scheduler app.worker.tasks   # tells the worker when

# from ./frontend
npm run dev                                                               # http://localhost:5173
```

The worker and scheduler are only needed for new news to arrive; the API and UI work without them. Stop each terminal with Ctrl+C, and stop the containers with `docker compose down` (data is kept).

## Database migrations

```bash
# from ./backend, after changing models
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

## Deploying (production)

**What you need:**
- A Linux server with Docker, for example 2 vCPU and 4 GB RAM to start.
- A domain whose DNS A record points at the server.
- Ports 80 and 443 open.

**1. Get the code and configure it.**
```bash
git clone <your repo> nis && cd nis && cp .env.example .env
```
Fill in `.env`. These are required:
- `ENVIRONMENT=production` (set by the prod file)
- `DOMAIN`, `ACME_EMAIL`
- `POSTGRES_PASSWORD` and `JWT_SECRET`: long random values
- `DATA_ENCRYPTION_KEY`: the command for it is in `.env.example`
- `EMAIL_PROVIDER=smtp` and the `SMTP_*` settings

Optional: the Anthropic, Razorpay and Telegram keys, and `SENTRY_DSN`.

**2. Start.**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
- Caddy gets an HTTPS certificate for `DOMAIN` automatically, and only ports 80/443 are exposed.
- The API refuses to start if a setting is unsafe for production. Its log says which one: `docker compose logs api`.

**3. First admin.** Sign up on the site, then run:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec api python -m app.cli make-admin you@example.com
```
Turn on two-step sign-in in **My account** before opening the admin panel. Admins need it in production.

**Updating.**
- Pull the new code and run the same `up -d --build`. Migrations run automatically before the API starts.
- Or use published images: push a tag such as `git tag v1.0.0 && git push origin v1.0.0`.
  - The **Release** workflow publishes the images to GHCR.
  - On the server, set `BACKEND_IMAGE` / `WEB_IMAGE` to those tags.
  - Then run `... pull` and `... up -d`.

**Backups.**
- The `backup` service writes `backups/nis-YYYY-MM-DD.dump` every day and keeps 14 days.
- Copy `backups/` off the server regularly (rsync, rclone to S3 or Google Drive). A backup on the same disk doesn't survive losing the server.
- To restore: `./ops/restore.sh backups/nis-YYYY-MM-DD.dump`. It asks for confirmation, stops the app, restores, migrates and restarts.

**Monitoring.**
- **Uptime:** point a free monitor (UptimeRobot, Better Stack) at `https://DOMAIN/api/health/ready`. It returns 503 if the database, Redis or the news pipeline is down.
- **Errors:** set `SENTRY_DSN` to get crash reports from the API, worker and Telegram poller. Passwords, cookies, request bodies and emails are never sent.
- **Logs:** `docker compose logs -f api worker`. Logs rotate at 5 × 20 MB per service.

**Data retention.** Every night at 03:30 IST the worker deletes:
- news older than 90 days, unless someone saved it
- fetch logs, sent emails and ended sessions after 30 days
- used or expired email links after 7 days
- Ask-the-news history after a year

Users, payments, billing events and the admin audit log are kept. The settings are `RETENTION_*`.

**Before going public (not code):**
- legal review of the Terms and Privacy pages
- permission or licences for the news sources
- Razorpay live mode with GST invoicing on
- a sender domain with SPF/DKIM for email

## Repository layout

```text
backend/
  app/
    api/          HTTP routes
    agent/        Ask the news (Claude tool loop) and the daily brief
    ai/           shared AI summaries, importance check, budgets
    alerts/       alert creation and Telegram delivery
    auth/         JWT + sessions, passwords, two-step sign-in
    billing/      plans, limits, Razorpay
    email/        outbox, templates, SMTP
    ingestion/    sources and feed fetching
    processing/   tagging and story grouping
    scoring/      per-profile scores
    worker/       Taskiq broker, scheduler, tasks
    maintenance.py  nightly data retention
  alembic/        migrations
  tests/
frontend/
  src/            React app
  nginx.conf      web server: app, /api proxy, security headers
ops/              Caddyfile (HTTPS), backup.sh, restore.sh
docker-compose.yml        development (and the base for production)
docker-compose.prod.yml   production overrides: Caddy, backups, no exposed ports
.github/workflows/        ci.yml (every push), release.yml (version tags)
```
