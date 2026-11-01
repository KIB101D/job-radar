# Deploying Job Radar

Production is three free services:

| Piece | Where | Why there |
| --- | --- | --- |
| Website + Telegram webhook | **Vercel** (Hobby): the Django app runs as one Python function, static files from Vercel's CDN | Free HTTPS, no sleeping server (a request starts the function in a second or two), deploys on every push to `main` |
| Database | **Neon** Postgres | Free, reachable from both Vercel and GitHub Actions |
| Background jobs | **GitHub Actions** cron | `fetch_vacancies`, `send_digests`, `check_active`; a serverless function can't run on a schedule for free |

Order matters a little: database first, then the site, then the scheduled jobs.

## 1. Neon: the database

1. Create a project at <https://console.neon.tech>. Region: **AWS Europe Central (Frankfurt)**, next to the Vercel function (`fra1` in `vercel.json`).
2. On the project dashboard, open **Connect** and copy the connection string. It looks like
   `postgresql://user:password@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require`.
   Use the pooled (`-pooler`) one: serverless functions open a connection per request, and the pooler absorbs that. The settings already turn off server-side cursors, which PgBouncer doesn't support, and keep no connections between requests on Vercel.
3. Keep the compute size at the minimum. The free plan has a monthly compute quota, and the scheduled jobs wake the database about twice an hour (it sleeps after 5 idle minutes).

This string is `DATABASE_URL` below. Treat it like a password.

## 2. Telegram: the bots

Use **two bots**: the real one (`@job_radar_dou_bot`) for production and a second one from [@BotFather](https://t.me/BotFather) for your laptop. `run_bot_locally` removes the webhook of the bot it runs, so running it with the production token would cut production off until the next deploy.

Generate the webhook secret (letters, digits, `_` and `-` only, which this command produces):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 3. Vercel: the site

1. Push the repo to GitHub. Vercel reads two files from it: `pyproject.toml` (dependencies, the entrypoint `config.wsgi:application` and the build step) and `vercel.json` (Frankfurt region, deploy only `main`, function timeout).
2. <https://vercel.com/new> → import `KIB101D/job-radar`. Vercel detects Django from `manage.py`; leave Framework, Build and Output settings as detected.
3. Before the first deploy, open **Environment Variables** and add these for the **Production** environment only (previews don't get the database or the bot):

   | Variable | Value |
   | --- | --- |
   | `DEBUG` | `False` |
   | `SECRET_KEY` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
   | `DATABASE_URL` | the Neon string from step 1 |
   | `TELEGRAM_BOT_TOKEN` | production bot token |
   | `TELEGRAM_BOT_USERNAME` | `job_radar_dou_bot` (without `@`) |
   | `TELEGRAM_WEBHOOK_SECRET` | the secret from step 2 |

   No `SITE_URL` or host lists needed: the settings take the production domain from Vercel (`VERCEL_PROJECT_PRODUCTION_URL`, for example `job-radar.vercel.app`) and trust it. Set `SITE_URL` only to override that.
4. **Deploy.** The build installs dependencies, runs `collectstatic` (Vercel does it), then `bin/vercel_build.py`: for a production deploy it **applies migrations** (this also creates the DOU feeds, one per category) and **points Telegram at** `https://<production domain>/telegram/webhook/`. Look for `Webhook set for @job_radar_dou_bot` in the build log.
5. Open `https://<production domain>/healthz/` (should say `ok`), then the home page.

Only pushes to `main` deploy (`git.deploymentEnabled` in `vercel.json`). Even if a preview deploy happens (say, from the CLI), `bin/vercel_build.py` skips migrations and the webhook unless `VERCEL_ENV` is `production`, so a preview can never take the bot or change the shared database.

If the production domain changes (a renamed project, a custom domain), redeploy so the webhook follows.

### Admin user

There's no shell on Vercel, so create the admin from your laptop against Neon:

```bash
DATABASE_URL='postgresql://...neon.tech/neondb?sslmode=require' python manage.py createsuperuser
```

Then sign in at `/admin/`.

## 4. GitHub Actions: the scheduled jobs

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret | Value |
| --- | --- |
| `DATABASE_URL` | same Neon string as on Vercel |
| `SECRET_KEY` | any long random string (`python -c "import secrets; print(secrets.token_urlsafe(50))"`); the jobs sign nothing, it only has to exist |
| `SITE_URL` | `https://<production domain>`, for example `https://job-radar.vercel.app` (digests link to the site) |
| `TELEGRAM_BOT_TOKEN` | production bot token |
| `TELEGRAM_BOT_USERNAME` | `job_radar_dou_bot` |

Then **Actions** tab → **Fetch vacancies** → **Run workflow** to fill the database right away instead of waiting for the schedule.

| Workflow | Schedule (UTC) | Command |
| --- | --- | --- |
| `ci.yml` | every push and pull request | ruff, migrations check, `check --deploy`, tests on SQLite and Postgres |
| `fetch-vacancies.yml` | `:07` and `:37` every hour | `fetch_vacancies` |
| `send-digests.yml` | `:10` and `:40` every hour | `send_digests` |
| `check-active.yml` | daily at 03:23 | `check_active` |

Every scheduled workflow also has **Run workflow** for a manual run.

Things to know about GitHub's scheduler:

- Runs can start several minutes late at busy times. Every job is safe to run late, twice or in parallel (DB locks, atomic digest slots).
- Keep the repository **public**: Actions minutes are free for public repos, and ~100 short runs a day would use up the private-repo allowance.
- GitHub turns off scheduled workflows in a public repo after **60 days without commits**. If that happens, re-enable them on the Actions tab.

## 5. Check it end to end

1. Open the site, set filters, **Підключити бота**, press **Start** in Telegram.
2. Bot info from your laptop, with the production token in the environment:

   ```bash
   TELEGRAM_BOT_TOKEN=... python manage.py set_telegram_webhook --info
   ```

   `URL` should be your `SITE_URL/telegram/webhook/`, and there should be no `Last error`.
3. After the next `:10` or `:40`, the **Send digests** run log shows what was sent.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| "Перевірити зараз" takes up to a minute | The feeds were stale (no fetch in the last 10 minutes), so the bot fetches DOU right then, one feed after another. The next scheduled fetch makes it instant again. |
| `Bad Request (400)` on every page | The host isn't in `ALLOWED_HOSTS`. Vercel's domains are added automatically (System Environment Variables must stay enabled in the project settings); another domain goes into `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. |
| The build fails at `collectstatic` with `SECRET_KEY` missing | The environment variables were added for Preview only, or after the deploy started. Add them for Production and redeploy. |
| `--info` shows `Last error: Wrong response from the webhook: 403 Forbidden` | `TELEGRAM_WEBHOOK_SECRET` on Vercel differs from the one Telegram has. Redeploy, so the build sets the webhook again with the current secret. |
| Webhook URL is empty | Someone ran `run_bot_locally` with the production token. Redeploy, or run `set_telegram_webhook` with production variables. |
| Scheduled runs fail with a connection error | Neon's monthly compute quota is used up (the project is suspended until the next period), or `DATABASE_URL` differs from Vercel's. |
