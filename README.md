# Job Radar

[![CI](https://github.com/KIB101D/job-radar/actions/workflows/ci.yml/badge.svg)](https://github.com/KIB101D/job-radar/actions/workflows/ci.yml)

Job Radar watches the [DOU](https://jobs.dou.ua) vacancy feeds, matches new vacancies against your filters and tells you about them on a website and in Telegram ([@job_radar_dou_bot](https://t.me/job_radar_dou_bot)).

- **Website** (Ukrainian UI): keywords, excluded words, 18 DOU categories, level (Intern ... Lead, read from the title), remote or cities (the ones vacancies actually list), minimum salary in USD (with or without vacancies that name none) and how fresh the list should be. Changes save at once, the list polls for new vacancies every minute and marks fresh ones. No account: a radar is a private link. A one-paragraph «Дані» page says what is stored and why, and a radar can be deleted there, which also unlinks the bot.
- **Telegram bot**: one tap links the chat to the radar. Digests arrive on a clock-aligned schedule in your time zone (hourly ... daily, with quiet hours), and the bot can check right now, show the latest matches and edit the same filters in a single settings message.
- **Background jobs** fetch the feeds every 30 minutes, send due digests and once a day mark vacancies that were closed on DOU.

Stack: Python 3.13, Django 5.2, HTMX, Postgres (Neon) / SQLite, httpx, feedparser, BeautifulSoup, GitHub Actions, Vercel.

## Architecture

```mermaid
flowchart LR
    subgraph GA[GitHub Actions]
        F[fetch_vacancies<br/>every 30 min]
        D[send_digests<br/>every 30 min]
        C[check_active<br/>daily]
    end
    DOU[(DOU RSS feeds<br/>and vacancy pages)]
    DB[(Neon Postgres)]
    W[Django on Vercel<br/>site + webhook]
    TG[Telegram Bot API]
    U((User))
    CJ[cron-job.org<br/>every 30 min]

    CJ -- workflow_dispatch --> GA

    F -- RSS --> DOU
    C -- vacancy pages --> DOU
    F & D & C --> DB
    D -- sendMessage --> TG
    U -- browser --> W
    U -- chat --> TG
    TG -- webhook + secret header --> W
    W -- Bot API via httpx --> TG
    W <--> DB
```

The website never scrapes. Web requests only read the database; everything that talks to DOU runs in management commands on a schedule. The site and the jobs share nothing but the database, so the site can run as a serverless function on Vercel while the jobs run on a schedule elsewhere.

| App | What it owns |
| --- | --- |
| `radar` | Sources (DOU feeds), vacancies, RSS parsing and fetching, keyword matching, search, closed-vacancy checks, DB locks |
| `subscriptions` | A radar (filters, schedule, linked chat), delivery records, the website (views, forms, HTMX fragments) |
| `bot` | Telegram client, webhook, command and button handlers, the settings panel, digests |

## Decisions worth explaining

- **Scheduled jobs on GitHub Actions, not in the web process.** The site is a serverless function on Vercel: nothing runs between requests, and there's no free scheduler for it. GitHub's own `schedule` turned out to skip most runs on a quiet repo, so cron-job.org starts the jobs through the GitHub API every 30 minutes, and the GitHub schedules stay as a fallback. Actions runs `manage.py` against the same Neon database. Every job takes a **DB-backed lock** (one conditional `UPDATE` with a TTL) and is safe to run late, twice or in parallel, which matters because GitHub's scheduler is best-effort.
- **Dedupe by canonical vacancy URL.** The DOU `guid` carries a changing timestamp, so the same vacancy would come back as "new". A vacancy can be in several feeds (Python and Front End overlap), so feeds are a many-to-many relation, not a column.
- **Level, salary and city are read at match time.** `radar/attributes.py` parses "Middle+ / Senior" from the title, "$4000–7000" or "до $5000" from the salary text and cities from the location, as pure cached functions. No extra columns, so every stored vacancy gets the filters and a better rule needs no backfill. A title without a level and a vacancy without a salary pass by default: most DOU vacancies name neither.
- **Matching in Python after a SQL prefilter.** Cheap conditions (active, remote, category) run in SQL; keywords use Unicode-aware word boundaries (`c++`, `.net`, `node.js`, Cyrillic) that SQLite and Postgres don't share. The table holds a few thousand rows, so this stays fast and identical everywhere.
- **Digests are idempotent.** A slot is claimed with one conditional `UPDATE` on `last_digest_at`; if Telegram fails, the claim is released for the next run. Vacancies are marked delivered only after Telegram accepted the message. Telegram webhook retries are de-duplicated by `update_id`.
- **The schedule is clock-aligned in the user's time zone.** "Every 3 hours" means 09:00, 12:00, 15:00 ... local time, not "3 hours after the last one". Quiet hours move a slot to the end of the window, DST included (`subscriptions/schedule.py`).
- **No lost updates between the site and the bot.** Both edit the same radar. Every save writes only its own columns (`update_fields`); the website auto-saves one field per change and takes the rest from the database; bot buttons carry absolute values (`remote:on`, not "toggle"); removing a word checks a fingerprint of the list the button was drawn for. A site tab re-reads the radar when it becomes visible again.
- **One message for settings in the bot.** Filters and notification settings live in a single message edited in place (`editMessageText`), so the chat doesn't fill up with menus. Saving on the website re-renders that message.
- **Closed vacancies.** A vacancy that left the feed isn't necessarily closed. `check_active` opens pages of vacancies gone from the feed for over a day (rate-limited): 404/410 or "(вакансія неактивна)" in the title means closed.
- **Plain Bot API over httpx** instead of a bot framework: the webhook is an ordinary Django view with a secret-header check, and the client is easy to fake in tests.
- **HTMX instead of a SPA.** Server-rendered templates with partial swaps: live polling, out-of-band updates of counters, auto-save. Everything still works as plain forms without JavaScript.

## Running locally

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # DEBUG=True, SQLite, no secrets needed for the site
python manage.py migrate        # also creates the DOU feeds
python manage.py fetch_vacancies
python manage.py runserver      # http://localhost:8000
```

The bot locally (use a separate dev bot, see [docs/deploy.md](docs/deploy.md#2-telegram-the-bots)): put `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` and `TELEGRAM_WEBHOOK_SECRET` into `.env`, then

```bash
python manage.py run_bot_locally   # long polling instead of the webhook, same handlers
```

Other commands: `send_digests`, `check_active`, `set_telegram_webhook [--info | --delete]`, and `createsuperuser` for `/admin/`.

## Tests

```bash
pytest                      # in-memory SQLite
ruff check . && ruff format --check .
TEST_DATABASE_URL=postgres://user:pass@localhost:5432/jobradar pytest   # on Postgres
```

About 340 tests: RSS parsing on saved DOU fixtures (including broken entries), fetch retries and politeness with a mocked transport, matching edge cases, the schedule across time zones and DST (`time-machine`), digests and the bot against a fake Telegram client that records every call, HTMX views, and the closed-vacancy check on saved DOU pages. CI runs the suite on both SQLite and Postgres 16 and fails on missing migrations or a failing `check --deploy`.

## Deploy

Vercel (site + webhook) + Neon (Postgres) + GitHub Actions (jobs), all on free plans. Migrations and the Telegram webhook are set by the production build only, so a preview deploy can't touch the shared database or take the bot. Step by step: **[docs/deploy.md](docs/deploy.md)**.

## Project layout

```
config/            settings (env-driven), urls, test settings
radar/             feeds, vacancies, fetcher, parser, matching, search, check_active
subscriptions/     radars, schedule, delivery, website views/forms, template tags
bot/               Telegram client, webhook, handlers, settings panel, digests
templates/ static/ server-rendered UI, HTMX, small vanilla JS, CSS
tests/             pytest suite and DOU fixtures
bin/vercel_build.py  Vercel build: migrate and set the webhook (production only)
vercel.json        Vercel: region, deploy only main, function timeout
.github/workflows/ CI and the three scheduled jobs
```
