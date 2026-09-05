# KiwiSystems AI

A web chatbot with real AI responses via NVIDIA's free NIM API, three separate capabilities (general chat, coding, image generation), admin-managed users with email-based 2FA, a live rules system for shaping how it speaks, and a dark "futuristic AI" interface — built to run on Vercel with a custom domain.

## Architecture at a glance

- **App:** Flask, deployed to Vercel as a serverless function (`api/index.py` + `vercel.json`)
- **Storage:** Upstash Redis (REST API) — accounts, taught facts, settings, and pending 2FA codes all live here instead of local files, since serverless hosts have no persistent disk
- **AI:** NVIDIA NIM API — one model each for General chat, Coding, and Image generation
- **2FA:** EmailJS — sends a 6-digit code to a user's email at login; anyone without an email on file simply skips 2FA
- **Retrieval:** The original TF-IDF engine didn't get thrown away — `/teach` still works, but taught facts are now injected into the AI's system prompt as reference context rather than being the literal reply

## Setting it up

### 1. Storage: Upstash Redis (free)

1. Go to **upstash.com**, sign up free, create a Redis database (any region close to where you'll deploy).
2. On the database page, copy the **REST URL** and **REST Token**.
3. You'll set these as environment variables in Vercel (below) — `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.

Without these set, the app still starts and serves pages, but login/signup won't work until storage is configured (it fails gracefully rather than crashing).

### 2. Deploying to Vercel

1. Push this project to a GitHub repo (Vercel deploys from Git).
2. On **vercel.com**, import the repo as a new project.
3. Before the first deploy, add these **Environment Variables** in the project settings:
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`
   - `FLASK_SECRET_KEY` — any long random string (without this, a new one is generated on every cold start, which would randomly log people out)
4. Deploy. Vercel reads `vercel.json`, which routes every path through `api/index.py` (the thin entry point that imports the real Flask app from the repo root).
5. Once live, go to **Project Settings > Domains** and add your custom domain — Vercel's free tier supports this, unlike most free hosts.

**Two things I couldn't fully verify from my side**, since I can't run an actual Vercel deployment or reach Upstash's API from the environment I built this in:
- **Function size:** `scikit-learn` + `scipy` + `numpy` (used for the TF-IDF retrieval layer) are moderately large. Vercel's Python functions have a size limit (around 250MB unzipped as of when this was built, but check Vercel's current docs). If the deploy fails on size, the fix would be replacing scikit-learn's TF-IDF with a small hand-written version (no heavy dependencies) — let me know and I can build that if it comes up.
- **Exact `vercel.json` schema:** Vercel's Python runtime configuration has changed over time. If the build fails with a config-related error, check Vercel's current Python deployment docs against what's in `vercel.json` here.

If either of these trips you up, send me the exact error and I'll adjust it.

### 3. NVIDIA API keys (free)

1. Go to **build.nvidia.com**, sign in free.
2. Open any model's page, click **Get API Key** (starts with `nvapi-`).
3. One key generally works across NVIDIA's whole catalog — you can reuse it for all three categories, or use separate keys.
4. In the admin **Settings** tab, paste the key + model name for each of General, Coding, and Image. Suggested starting models are pre-filled as placeholders.

**On image generation specifically:** NVIDIA's hosted image endpoint has shifted around their catalog before, so it's a configurable field in Settings (not hardcoded). If image mode errors out, check your chosen model's code sample on build.nvidia.com and update the Endpoint field to match.

### 4. Email 2FA: EmailJS (free, ~200 emails/month)

1. Go to **emailjs.com**, sign up free.
2. **Email Services** → connect an email account (e.g. Gmail) → gives you a **Service ID**.
3. **Email Templates** → create one with variables `{{to_email}}` and `{{code}}` in the body. **Important:** set the template's "To Email" field to `{{to_email}}` — if you leave it as a fixed address, every code goes to the same inbox regardless of who's logging in.
4. **Account > API Keys** → copy your **Public Key** and **Private Key**.
5. Paste all four values into the **Email / 2FA** card in admin Settings.

Users without an email on file skip 2FA automatically — admins can add/edit anyone's email from the **Users** tab.

## Admin console

Log in at `/admin` with an admin account (tima or Hbelbs — same credentials work for both the public chat and admin console). Three tabs:

- **Teach Console**
  - `/teach`, `question => answer` (or `q1 | q2 => answer` for multiple phrasings), `/correct`, `/list`, `/forget n`, `/unteach`
  - `</> Code answer` button — a textarea for multi-line code snippets, formatting preserved exactly
  - `/rule <instruction>`, `/rules`, `/unrule n` — shape how the AI speaks, across all modes
- **Users** — every account, **+ Add user**, inline **Edit** for email, **Reset password**
- **Settings** — NVIDIA keys/models per capability, EmailJS config, read-only view of active rules

## Public chat

At `/`, after logging in: a segmented control switches between **General**, **Code**, and **Image** modes, each using its own configured model. A category with no API key set shows a clear amber notice instead of erroring out.

## Running it locally

```bash
cd chatbot
pip install -r requirements.txt
export UPSTASH_REDIS_REST_URL="..."
export UPSTASH_REDIS_REST_TOKEN="..."
export FLASK_SECRET_KEY="something-long-and-random"
python app.py
```

Then open `http://localhost:5000`.

## Design

Dark, glassmorphic panels over a near-black background with a faint grid backdrop, a cyan-to-violet gradient as the primary accent, Space Grotesk for headings, Inter for body text. The kiwi cross-section mark carries over from the previous design, recolored into the gradient with a soft glow, reading as an "AI core."

## A few things worth knowing

- **API keys and EmailJS credentials are stored in plain text** in the Upstash database. Normal for a small self-hosted app, but treat access to that database with the same care as a password.
- **Costs:** NVIDIA's and Upstash's free tiers have request limits that can change — check current limits on their sites. EmailJS's free tier is capped around 200 emails/month.
- **This is a personal/small-scale project, not a production auth system** — no login rate-limiting, and 2FA codes aren't rate-limited on resend either. Fine for a small team; tighten this up before wider public exposure.
- **Not tested against live NVIDIA, EmailJS, or Upstash endpoints** from my side (sandboxed environment, no outbound access to any of them). Everything was built against each service's documented API shape and tested end-to-end using local mock servers standing in for each one — all core logic paths pass, but the very first real request to each service is unverified. If something errors out on first use, send me the exact message and I'll help fix it quickly.

## Files

- `app.py` — Flask server: auth, 2FA, mode-aware chat, admin routes, settings API, legal pages
- `api/index.py` — Vercel serverless entry point (imports `app.py`)
- `vercel.json` — routing config for Vercel
- `accounts.py` — signup/login, admin flags, password/email management, user tracking
- `chatbot_engine.py` — TF-IDF engine, used as retrieval/context for the AI
- `ai_settings.py` — NVIDIA keys/models, EmailJS config, rules (stored via `kv_store`)
- `kv_store.py` — Upstash Redis REST client (the storage backend for everything above)
- `nvidia_client.py` — NVIDIA NIM API client (chat completions + image generation)
- `emailjs_client.py` — EmailJS API client (2FA codes)
- `intents.json` — base training data, bundled with the code (read-only at runtime)
- `templates/` — all pages + `_brand.html` (shared logo/wordmark partial)
- `static/style.css` — the design system
- `static/cookie-consent.js` — the cookie consent flyout
- `requirements.txt` — dependencies (`flask`, `scikit-learn`, `requests`)
- `.vercelignore` — keeps local dev artifacts out of deployment
