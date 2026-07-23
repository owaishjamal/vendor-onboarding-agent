# Deploy — getting a working link

The submission asks for a **live, runnable link**. The whole product is one
container (React app served by the FastAPI process), runs **offline with no API
key**, and exposes a single URL. Pick whichever path fits.

---

## Option A — Render (recommended, free, gives a public URL)

1. Push this folder to a GitHub repo.
2. On [render.com](https://render.com): **New → Blueprint**, pick the repo.
   Render reads `render.yaml`, builds the `Dockerfile`, and deploys.
3. You get a URL like `https://vendor-onboarding-xxxx.onrender.com`. That's the
   link you submit. `/health` is the health check.

Free instances sleep after inactivity and take ~30s to wake — open the link a
minute before a demo. To use a real model instead of the offline composer, add
`ANTHROPIC_API_KEY` in the Render dashboard and set `LLM_PROVIDER=anthropic`.

## Option B — Docker anywhere (Railway, Fly.io, a VM, your laptop)

```bash
docker build -t vendor-onboarding .
docker run -p 8000:8000 vendor-onboarding
# open http://localhost:8000
```

The image builds the frontend, installs the backend + `tesseract-ocr`, and
renders the sample documents, so the demo cases work the moment it starts.
Railway and Fly both deploy a `Dockerfile` directly and inject `$PORT`, which
the container honours.

## Option C — Local + a tunnel (fastest, for a scheduled call)

```bash
make install && make seed
make api          # terminal 1 → :8001
make ui           # terminal 2 → :5174
```

Then expose it with a tunnel if you need an external URL:
`npx cloudflared tunnel --url http://localhost:5174` (or `ngrok http 5174`).
Good for a live call; not "always on" like Option A.

---

## How it works on NEW tests (the important part)

The interviewer will submit inputs you've never seen. Here's exactly how the
system handles them, honestly:

### It genuinely generalises — the checks are algorithms, not lookups
Use the **"Paste JSON"** tab on the Intake screen to run any submission live.
Five of the seven checks work on *arbitrary* input because they compute, they
don't match against a fixture:

- **Completeness** — reads the country's required-field/document list from the
  rule pack and checks presence. Works for any supported country.
- **Format** — real **IBAN mod-97** and **ABA 3-7-1** checksums and per-country
  regex. A brand-new valid IBAN passes; a typo'd one fails. Nothing memorised.
- **Consistency** — cross-field logic (bank-holder vs legal name, country vs
  IBAN/tax-ID/address, the subtle "…Holdings" detector). Pure computation.
- **Screening** — fuzzy name match + DOB/nationality second factor against the
  denied-party list. Any name is screened.
- **Duplicates** — fingerprints the submitted bank account and compares to the
  vendor master.

So a new, well-formed vendor flows through all of these correctly on the first
try. This is the part that proves the process is real.

### Two checks are only as complete as their reference data — by design
- **Registry** and parts of **screening/duplicates** compare against seeded
  lists (`backend/seed/company_registry.json`, `denied_parties.json`,
  `vendor_master.json`). A brand-new legitimate company won't be in the
  registry, so it lands on **PENDING_REVIEW (`REGISTRY_NOT_FOUND`)** — which is
  the *correct* answer: "we can't confirm this company exists." It does not
  crash and it does not wrongly approve.
- If you want a new company to reach **APPROVED** in a demo, add one line to
  `company_registry.json` (its country + registration number + name) — that's
  the analogue of "the company really is on Companies House." In production
  these files are swapped for live registry / screening API adapters; the check
  logic is unchanged.

### Nothing an interviewer submits will break it
Every check that can't complete degrades to a `NEEDS_REVIEW` finding rather
than an exception, malformed JSON returns a clean `422`, an unsupported country
escalates instead of approving, and unreadable documents ask for a resend. The
system's worst case on a strange input is "ask a human," never a crash or a
wrong approval.

### Prep checklist for a live "new test"
- Deploy via Option A so the link is warm.
- Have the **Paste JSON** tab ready; keep one sample JSON open to edit from.
- If they want to see a *new* company approve, pre-add it to the registry seed
  (or redeploy with it added).
- `POST /v1/reset` (or the "Clear case history" button) resets the queue between
  runs. Reference data is untouched by a reset.

---

## What's in the image vs. what resets

- **Baked into the image:** rule packs, registry / denied-party / vendor-master
  seeds, the 11 rendered sample documents. These are always present.
- **Ephemeral:** the SQLite case history (`data/cases.db`). It resets on
  redeploy or `make reset`. That's fine — cases are demo runs, not the system
  of record for reference data.
