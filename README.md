# Parent Insight Assistant

An AI assistant that turns a child's Schoology data into clear answers and advice for parents: what is missing, which topics they keep losing marks on, whether it looks like effort or understanding, and what to do next.

This repository is the **backend** (Python, FastAPI, Supabase Postgres). It serves a JSON API plus three simple pages: a sign-in page (`/`), a dashboard of everything calculated without AI (`/dashboard`), and the admin panel (`/admin`).

**The core rule:** grades, dates and averages come from plain code. The AI explains the numbers but never produces them.

## How it works

```
Schoology API ──> sync ──> Supabase ──> rule layer (code) ──> AI layer ──> JSON API
                                         numbers, flags,        labels topics,
                                         likely cause           writes explanations
```

| Layer | Folder | What it does |
|---|---|---|
| Sign-in and sync | `backend/app/auth.py`, `sync.py`, `schoology.py` | Parent signs in on Schoology, data is pulled into Supabase |
| Rule layer | `backend/app/analytics/` | Averages, missing and late work, trends, homework vs test gap, weak topics, likely cause, alerts, weekly changes |
| AI provider layer | `backend/app/llm/` | One interface over Hugging Face and Claude |
| AI features | `backend/app/ai/` | Topic labels, weekly summary, alert lines, advice, gap explanation, chat agent |
| API and jobs | `backend/app/api.py`, `jobs.py` | Routes for parents, scheduled sync and weekly summaries |
| Admin | `backend/app/admin.py` | Provider, model and BYOK settings |

### Features in this build

| # | Feature | Rule layer | AI |
|---|---|---|---|
| 2 | Weak topics | Count lost marks per topic, flag when missed in 3+ assignments | Label each lost mark with a topic |
| 3 | Weekly summary | What changed in the last 7 days | Write the update |
| 4 | Alerts | Missing work, average down 10+, test under 60%, new weak topic | One line and a next step |
| 5 | Effort or understanding | Score the signals, rules pick the cause | Explain it as a possible reason |
| 6 | Homework vs test gap | Flag gaps of 20+ points | Explain it neutrally |
| 7 | Chat | Tools return stored facts | Answer only from those facts |

Thresholds are starting values in `backend/app/config.py`, to be tuned in the pilot. Not in this build: photo upload, teacher message drafts, multi-language, tutor matching, class scanning.

## Sign-in

Parents never give this app a password. Schoology uses three-legged OAuth 1.0a:

1. On the sign-in page (`/`), the parent picks their district from a dropdown, or chooses **Other address** and types it. The dropdown comes from `SCHOOLOGY_DISTRICTS` in `.env` (default: Irving ISD and Grand Prairie ISD, whose Schoology login pages name the district). Typed addresses must match `SCHOOLOGY_ALLOWED_DOMAINS`. A district's address is what the browser shows after signing in to Schoology, not the district's SSO page.
2. `GET /auth/start?domain=...` gets a request token and sends the parent to **their district's own Schoology login page** (which may use district single sign-on).
3. The parent approves. Schoology returns them to `GET /auth/callback`.
4. The backend swaps the request token for an access token, stores it **encrypted**, reads the parent and their children (`child_uids`), starts a signed session cookie and opens `/dashboard`. The first visit syncs grades automatically. A student account has no children, so it is shown as its own child.
5. Schoology access tokens last **90 days**. `GET /api/me` reports `schoology.state` as `ok`, `expiring` (within 7 days) or `reconnect`. A rejected token also switches to `reconnect`.

The sign-in page also offers **Try the demo** (`POST /auth/demo`), a sample child with no Schoology needed, while `DEMO_MODE=true`.

The redirect back from Schoology happens in the parent's own browser, but Schoology's firewall refuses return addresses on `localhost` or `127.0.0.1` (HTTP 403). Schoology also refuses to finish an approval that returns to a plain `http://` address ("no valid return URL found"), so local testing needs an `https://` address. A free Cloudflare quick tunnel gives one, with no account needed:

```bash
# once: download cloudflared into .tools/ (git-ignored), from
# https://github.com/cloudflare/cloudflared/releases/latest (cloudflared-darwin-arm64.tgz on Apple silicon)
.tools/cloudflared tunnel --no-autoupdate --url http://localhost:8000
```

Put the `https://<random>.trycloudflare.com` address it prints into `PUBLIC_URL` in `.env`, restart the server, and open that address. The address changes every time the tunnel restarts. A sign-in started on any other name for the server (e.g. `localhost`) moves to `PUBLIC_URL` first, so the session cookie is where Schoology returns.

If Schoology still lands on its own page after **Approve**, the sign-in page offers **I've approved, finish signing in**, which completes the sign-in without the return trip. For a quick test with one real account, a consumer key and secret from that user's `https://<district>.schoology.com/api` page can go in `SCHOOLOGY_CONSUMER_KEY` / `SCHOOLOGY_CONSUMER_SECRET`. It can read only that user's own data.

> **Before real parents can sign in:** register a developer app in the Schoology App Center and get it approved for the district. Since June 2025, only approved apps can read another user's data, which is what a parent reading their child's grades needs. The Schoology calls marked `UNVERIFIED` in `schoology.py` and `sync.py` still need testing with a real parent account.

## AI providers and the admin panel

Open `/admin` and sign in with `ADMIN_PASSWORD`.

- **Provider:** Hugging Face (default) or Claude. Every AI feature uses the selected one.
- **Model:** set per provider, with suggestions. Any Hugging Face Inference Providers chat model or Claude model id works.
- **BYOK (bring your own key):** one switch per provider.
  - **On:** parents may save their own key (`PUT /api/ai-keys/{provider}`), and their requests use it.
  - **Off:** everyone uses the app's key from `.env`, and saved parent keys are ignored.
  - Parent keys are encrypted at rest.
- **Test saved model:** sends a tiny prompt with the app's key to check the provider, model and key.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp .env.example .env        # then fill it in, see below
```

Fill in `.env`:

| Setting | Where it comes from |
|---|---|
| `SESSION_SECRET` | Any long random string |
| `TOKEN_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Do not lose or change it, or stored tokens and keys cannot be read. |
| `ADMIN_PASSWORD` | Your choice |
| `DATABASE_URL` | Supabase → Project Settings → Database → Connection string (Session pooler) |
| `TEST_DATABASE_URL` | Same, from a **separate, empty** Supabase project used only by tests |
| `SCHOOLOGY_CONSUMER_KEY`, `SCHOOLOGY_CONSUMER_SECRET` | Your Schoology developer app |
| `HF_TOKEN` | huggingface.co → Settings → Access Tokens (with Inference Providers permission) |
| `ANTHROPIC_API_KEY` | platform.claude.com → API keys |
| `SMTP_*` | Optional. Without it, summaries and alerts stay in the app. |

Create the tables (once, and again after adding a migration):

```bash
cd backend
../.venv/bin/python -m app.jobs migrate
```

Or paste `supabase/migrations/001_init.sql` into the Supabase SQL editor. Row Level Security is enabled on every table with no policies, so Supabase's public REST API cannot read children's data. Only the backend's direct connection can.

Run the server:

```bash
cd backend
../.venv/bin/uvicorn app.main:app --reload
```

- Sign in: your `PUBLIC_URL` (see Sign-in for testing Schoology locally)
- API docs: http://localhost:8000/docs
- Admin: http://localhost:8000/admin

## Scheduled jobs

Run from `backend/`, e.g. with cron:

| Command | When | What |
|---|---|---|
| `python -m app.jobs sync` | Every few hours | Pull Schoology data, label new lost marks, raise and email alerts |
| `python -m app.jobs weekly` | Once a week | Write and email weekly summaries |
| `python -m app.jobs cleanup-demo` | Daily | Delete demo sign-ins older than a day |

## API

All child routes check that the child belongs to the signed-in parent. AI-backed fields come with an `ai_error`, so the rule-based facts are always returned even when an AI feature is missing or failing.

| Route | |
|---|---|
| `GET /api/me` | Parent, children, Schoology connection state, AI provider |
| `POST /api/sync` | Sync now, then label topics and raise alerts |
| `GET /api/children/{id}/dashboard` | Everything the rule layer calculates, in one response (used by `/dashboard`) |
| `GET /api/children/{id}/overview` | Courses, averages, trends, missing, late, upcoming |
| `GET /api/children/{id}/weak-topics` | Ranked topics with examples |
| `POST /api/children/{id}/weak-topics/label` | Label unlabelled lost marks now |
| `GET /api/children/{id}/gap` | Homework vs test gap, with explanations for flagged courses |
| `GET /api/children/{id}/advice` | Likely cause, evidence and explanation |
| `GET /api/children/{id}/alerts` | Alerts; `POST .../alerts/refresh`, `POST .../alerts/{alert_id}/read` |
| `GET /api/children/{id}/summaries` | Weekly summaries; `POST .../summaries/generate?force=true` |
| `GET/POST/DELETE /api/children/{id}/chat` | Chat history, ask a question, clear |
| `GET /api/ai-keys`, `PUT/DELETE /api/ai-keys/{provider}` | Parent's own AI keys (BYOK) |

## Tests

```bash
cd backend
../.venv/bin/python -m pytest -m "not ai"   # rule layer, provider layer, agent tools, API
../.venv/bin/python -m pytest -m ai         # AI feature contracts
```

- API tests run only against `TEST_DATABASE_URL` and skip when it is not set. They empty that database's tables on every test, so never point it at real data.
- AI contract tests use a scripted fake model: no keys, no cost.

## Contributing

- AI features (`backend/app/ai/`) have their own guide: [backend/app/ai/README.md](backend/app/ai/README.md).
- Keep numbers in the rule layer. If an AI feature needs a new number, add it to `analytics/` and pass it in.
- Never commit `.env`.
