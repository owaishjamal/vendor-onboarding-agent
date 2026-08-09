# Deploying

**One service, one URL.** The Docker image builds the React app and FastAPI
serves it alongside the API from the same origin. There is no separate
frontend to deploy, no CORS to configure, and nothing to provision first — no
database server, no Redis, no object store. State is a SQLite file; documents
are on local disk.

That was a deliberate choice. At onboarding volumes — dozens of vendors a
quarter, and a sub-second pipeline — a queue and a managed database buy
nothing but services to keep alive.

---

## Render (recommended, free)

1. Push the repo to GitHub.
2. In Render: **New → Blueprint**, pick the repo. It reads `render.yaml`.
3. **Environment → Add** `GEMINI_API_KEY` (optional, see below).
4. Wait for the build. You get `https://<name>.onrender.com`. Send that link.

Check it with `/health`. `llm_provider` tells you which mode you are in.

**The free plan sleeps after ~15 minutes idle**, and a cold start takes 30–60
seconds. If you are demoing to someone, open the link a minute beforehand.

**The free plan's filesystem is ephemeral** — the SQLite file is wiped on every
restart and redeploy. `SEED_DEMO_CASES=1` re-runs the eleven labelled
submissions on a cold start, so a visitor never lands on an empty dashboard.
For history that survives, attach a Render disk mounted at `/app/data`.

---

## Anywhere else that runs a container

```bash
docker build -t vendor-onboarding .
docker run -p 8000:8000 -e GEMINI_API_KEY=your-key vendor-onboarding
```

Then open <http://localhost:8000>. The same image runs on Fly.io, Railway,
Cloud Run, or any VPS. `$PORT` is honoured where the platform injects it.

---

## The LLM is optional, and this matters

**Every verification decision is deterministic.** All nine checks — format
rules, checksums, cross-field consistency, registry lookups, denied-party
screening, duplicate detection — and the verdict that follows from them run
with no API key and no network. The labelled-case evaluation scores 11/11
either way.

A key adds three things, all of them prose:

- vendor emails written rather than templated,
- reviewer summaries written rather than templated,
- copilot answers to open-ended questions. The common questions ("what is
  missing", "which checks failed", "why was this flagged") are answered
  straight from the case record and need no key at all.

Set `GEMINI_API_KEY` and the provider is inferred — you do **not** also need
`LLM_PROVIDER`. Get a free key at <https://aistudio.google.com/apikey>.

To force fixture mode for a demo even with a key present, set
`LLM_PROVIDER=offline` explicitly.

Diagnose configuration with:

```bash
python scripts/check_env.py
```

It prints which `.env` files were found, what the app resolved, and makes a
real call to Google. It never prints your key.

---

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `GEMINI_API_KEY` | — | Enables AI prose. Provider is inferred from it. |
| `LLM_PROVIDER` | inferred | `offline` forces fixtures. Also `anthropic`, `openai`. |
| `APP_API_KEY` (build arg) | `dev_secret` | Sets the API key in *both* the browser bundle and the server. |
| `SEED_DEMO_CASES` | `0` | Run the labelled submissions into an empty database on boot. |
| `CHECK_DELAY_MS` | `400` | Pacing so the live run view is watchable. `0` for throughput. |
| `VO_DB_PATH` | `data/cases.db` | SQLite location. |
| `DATABASE_URL` | — | Point at Postgres instead, if you outgrow one file. |

### About that API key

`APP_API_KEY` guards the write and reporting endpoints. Pass it as a Docker
**build argument**, not just an environment variable:

```bash
docker build --build-arg APP_API_KEY=something-long -t vendor-onboarding .
```

The browser bundle needs it at build time (Vite inlines it) while the server
reads it at run time. The Dockerfile derives both from that one argument
precisely so they cannot drift — set them separately and the UI returns 401
against its own backend, which is a thoroughly unpleasant thing to debug on a
fresh deploy.

**Be clear-eyed about what this is.** The key ships inside the JavaScript, so
anyone can read it out of devtools. It deters casual scripted abuse of a public
URL; it is not authentication. Anything handling real vendor data needs SSO in
front of the ops routes. The vendor portal is separate and genuinely
credentialed — each case has its own unguessable token.

---

## Before you share the link

- [ ] Open `/health` — confirm `llm_provider` is what you expect.
- [ ] Submit one vendor end to end and watch the checks stream.
- [ ] Open the ops queue; confirm the seeded cases are there.
- [ ] Ask the copilot "why was this flagged?" on any case.
- [ ] Open the link once shortly before any live demo, to beat the cold start.
