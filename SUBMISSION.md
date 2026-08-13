# Submission — PS-2, Vendor onboarding

Two deliverables: a working process and a 5-minute video.

---

## Deliverable 1 — the working process

**Live:** `<paste your Render URL>`
**Repo:** https://github.com/owaishjamal/vendor-onboarding-agent

A vendor submission goes in — form fields and real document uploads. Nine
checks run and stream live. A decided status comes out, with every reason
visible and an audit trail behind it.

Not a mockup. The uploads are read for real (PDF text layer, OCR fallback),
the checks compare against seeded registry, sanctions and vendor-master data,
and the verdict is computed. Type a vendor that isn't in any fixture and it
runs exactly the same way.

### Against the brief

| Asked for | Where it is |
|---|---|
| Accepts real inputs | Form + PDF/image upload, verified on attach before submitting |
| Runs live | `/m/onboard` — nine checks stream over SSE, one event per stage |
| Produces a real output | Four verdicts, every finding with evidence, drafted vendor email |
| Live run view, stage by stage | The run view names each check, its kind, its finding and its duration |
| Dashboard: history, status, outputs | `/queue` — every run, status, touch rate, full report per case |
| Intuitive, well-designed UI | Monochrome system, one account menu, prefilled cases so nothing needs typing |
| Edge cases you designed | Four, one click each from the form — see below |

### The four edge cases

Each breaks a *different* obvious rule and reaches a *different* verdict. All
four are one click from the vendor form, and each states the verdict it expects
before it runs.

| Case | The obvious rule it breaks | Verdict |
|---|---|---|
| Bank account belongs to another vendor | every field valid → approve | `PENDING_REVIEW` |
| Sanctions namesake (+ its rejected twin) | name matches list → reject | `APPROVED` / `REJECTED` |
| Freelancer with no incorporation | vendors must supply incorporation | `APPROVED` |
| Insurance expiring in 21 days | expired blocks, valid passes | `APPROVED_WITH_CONDITIONS` |

`tests/test_scenarios.py` re-runs all of them against the real pipeline and
fails the build if any stops reaching its stated verdict — so the table can't
go stale.

### Numbers

```
334 tests           pytest tests/ -q
11/11 eval          100% accuracy, 100% recall, 0 false positives
9 checks            7 deterministic, 2 AI, rendered separately
6 categories        JSON files — adding one ships no Python
3 LLM providers     Groq, Cerebras, Gemini, with automatic failover
```

### Runs with no API key

`LLM_PROVIDER=offline` composes the two generated documents from templates.
Every check, finding, status and screen is identical — only the wording of the
vendor email and reviewer summary differs. Every result in the README is
reproducible with no key and no network.

---

## Deliverable 2 — the video

Script with timings: **`DEMO.md`**. Rehearse it once, record in one take.

Shape: problem (25s) → happy path (60s) → three edge cases (2m15) → ops
dashboard (45s) → how it's built (35s).

---

## Before you send

- [ ] **Deploy and open the URL in a private window.** A deploy that works on
      your laptop and not on the link is the single most common way this fails.
- [ ] Sign in as ops, run one case end to end on the live URL.
- [ ] Check `/queue` shows history — `SEED_DEMO_CASES=1` fills it on a cold
      start so a visitor never lands on an empty dashboard.
- [ ] **Rotate the API keys.** Groq, Cerebras and Gemini keys were exposed
      during development. Paste fresh ones into Render's Environment tab
      (`GROQ_API_KEY`, `CEREBRAS_API_KEY`, `GEMINI_API_KEY`) — never into a file.
- [ ] Record the video. Five minutes, hard stop.

### Deploying

```bash
git push                       # already current as of the last commit
```

Render → the service → **Manual Deploy** → *Deploy latest commit*.
Add the three keys under **Environment**. `render.yaml` declares all three with
`sync: false`, so Render prompts for the values and stores them encrypted.

Free tier has an ephemeral filesystem, so case history resets on redeploy —
`SEED_DEMO_CASES=1` handles that.

---

## What is deliberately not built

Worth saying out loud rather than being asked:

- **Live registry and sanctions APIs.** Both sit behind provider adapters with
  seeded data. Swapping in GLEIF, Companies House or a Dow Jones feed is an
  adapter, not a redesign. Wiring live credentials would prove integration
  plumbing, not decision quality.
- **A trained document model.** Extraction is text-layer-first with OCR
  fallback and content-signal classification. The point being demonstrated is
  the cross-referencing and confidence handling, not layout-robust OCR.
- **Real authentication.** The ops routes have none, and the bundled API key
  ships in the JavaScript — abuse deterrence, not a credential.
- **Horizontal scale.** One process, one SQLite file. The seams are there
  (storage interface, provider adapters, Redis-ready rate limiter), but the
  scale is not, and this is a case study.
