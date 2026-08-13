# Demo runbook

The 5-minute video, and the live interview after it.

**The rule that makes this script work:** never explain architecture with a
static screen. Every technical point below is said *while something is running
or visible*. A demo that pauses to describe its design is a slide deck with
extra steps.

---

## Before you record

```bash
CHECK_DELAY_MS=400 SEED_DEMO_CASES=1 uvicorn backend.app.api.app:app --port 8000
```

Two tabs: **1** `/m/onboard` signed in as ops, **2** `/queue`.

- Header must show `● live`. If it says `mock`, no key loaded — the verdict is
  identical either way, so say so rather than restarting.
- Close everything else. Notifications off. Zoom ~110% so check names survive
  compression.
- **Do not rehearse at `CHECK_DELAY_MS=0`.** You want the stages to land
  visibly.

---

# The script — 5:00

---

### 0:00 · The problem, and the claim (25s)

> "Before a company pays a supplier, someone verifies they're real — company
> details, bank account, tax registration, documents. That review is manual,
> chasing missing paperwork is manual, and the audit trail is somebody's inbox.
>
> This does it in about fifteen seconds and shows its working. But what I want
> to show you isn't that it approves good vendors — any form with validation
> does that. It's the four cases where the **obvious rule gives the wrong
> answer**."

---

### 0:25 · Happy path — the architecture, while it runs (60s)

`/m/onboard` → **Clean goods supplier**.

*While the form renders:*

> "The vendor picks what they supply, and only gets asked what that category
> actually needs. This form isn't hardcoded — it's a JSON profile resolved in
> three layers: a country pack, then the category, then any client overrides.
> Six categories, six files. Adding one ships no Python."

**Submit.** Now talk over the nine checks landing:

> "Nine checks streaming over server-sent events — one event per stage, so you
> see *what* was verified rather than a spinner.
>
> Seven of these are deterministic: regex, an IBAN mod-97 checksum, a registry
> lookup. Two use a model. The report tells you which is which, because 'the
> checksum failed' and 'the model thinks this looks like a resume' deserve
> completely different levels of trust.
>
> And nothing short-circuits. Even once a rejection is certain every check
> still runs — because if a submission has four problems and you stop at the
> first, the vendor fixes one thing, resubmits, and gets told about the next.
> Four round trips, days each. The entire cost of onboarding is round trips."

*Verdict lands: **APPROVED**, confidence 100%.*

> "Status is one line of code: the maximum severity of any finding. No
> weighting, no tuned threshold — so every verdict traces to a single finding
> you can point at."

---

### 1:25 · Edge case 1 — the flawless fraud (55s)

Back → **Bank account already belongs to another vendor**.

> "Watch this one. Every single field is valid."

**Submit.** Point along the checks going green.

> "Formats pass. Documents corroborate the form. The company's in the registry.
> Nobody's on a sanctions list."

*Verdict: **PENDING REVIEW**.*

> "Needs review — because that bank account is already on our master file under
> a different company.
>
> Nothing *inside* the submission is wrong. It's only visible by comparing
> against what we already hold. That's the signature of invoice-redirection
> fraud, and it's the reason validation isn't verification.
>
> And notice it is **not rejected**. Group treasury, a parent collecting for a
> subsidiary, factoring — all produce this exact pattern legitimately.
> Auto-rejecting breaks real suppliers; auto-approving is how money leaves. So
> a human decides, with the conflicting record attached."

*If asked whether it's faked:* the account number hashes to a fingerprint
already sitting in `vendor_master.json`. Real collision, not a flag.

---

### 2:20 · Edge case 2 — the innocent namesake (50s)

Back → **Director shares a name with a sanctioned individual**.

> "This director's name matches an OFAC entry. Exactly. One hundred percent."

**Submit.**

*Verdict: **APPROVED**.*

> "Approved — because his date of birth and nationality don't match the listing.
>
> Names aren't unique, especially transliterated ones. Screening on names alone
> means turning away legitimate suppliers because someone shares a surname with
> a designated person. That's a fairness problem as much as a commercial one.
>
> The near-match is still written to the audit trail. 'We saw it and cleared it,
> here's why' is a much better record than never having looked."

*If comfortably inside time:*

> "The twin case — same name, but the listed DOB and nationality — is rejected
> outright with no human involved. Sanctions are one of very few places an
> automated system should refuse rather than escalate."

---

### 3:10 · Edge case 3 — valid today, worthless next month (30s)

Back → **Insurance valid today, expires in three weeks**.

**Submit.** *Verdict: **APPROVED WITH CONDITIONS**.*

> "A binary valid-or-expired test gets this wrong in both directions. Blocking a
> vendor whose cover is valid today is how teams learn to route around
> procurement. Waving it through is how a contractor ends up on site next month
> with lapsed liability cover.
>
> So there's a fourth verdict. Onboarded now, renewal recorded against them and
> chased before the date. And the severity for it sits *between* advisory and
> needs-info deliberately — because status is the maximum severity, that
> ordering alone guarantees a condition can never upgrade a case. The enum
> enforces it, not a rule."

---

### 3:40 · Ops dashboard (40s)

Tab 2 → `/queue`.

> "Every run, its status, and the touch rate — how often a human was actually
> needed."

Open the shared-bank-account case.

> "The full report. Every finding with its evidence. Deterministic and AI checks
> rendered in separate groups. And the verification matrix — every claim on the
> form marked corroborated, contradicted, or unevidenced against the documents."

Copilot tab → type **why was this flagged?**

> "A copilot grounded in this case record. Most questions get answered straight
> from the data rather than from a model, because that can't hallucinate. When
> it doesn't know, it says so and lists what it *can* answer, instead of
> inventing a compliance opinion on a case about to be approved."

---

### 4:20 · How it's built (40s)

*Stay on the case detail — do not switch to an editor.*

> "Two things on the build.
>
> **Categories are data.** Six JSON files. The interesting half isn't what a
> category adds — it's what it *stops* asking for. A freelance architect has no
> certificate of incorporation, and demanding one parks her in 'more information
> needed' forever: she can't supply it and no reviewer can conjure it. That
> waiver is a field in JSON, not an if-statement.
>
> **And every LLM call goes through a router I wrote** over Groq, Cerebras and
> Gemini. The app asks for a *task* — reasoning, classification, vision — and
> the router picks a model per request from live rate-limit and health state.
> Sliding-window limiting, circuit breakers, honours Retry-After on a 429, and
> fails over to a different provider rather than a different model on the same
> key.
>
> I tested it with three deliberately invalid keys: all nine models fail, all
> nine breakers open, and every one of these cases still reaches the right
> verdict on templates. **The compliance decision never depended on a model
> being up.**"

**Stop at 5:00.**

---

## Cuts, in this order

1. The sanctions twin (2:20, last block)
2. The copilot (3:40, last block)
3. The router paragraph — but keep the last sentence
4. Edge case 3 — but keep *"there's a fourth verdict for obligations satisfied
   now and not later"*

**Never cut** the shared bank account. It is the strongest thing here.

---

## If they ask, mid-demo

**"Is that scripted?"**
Type a vendor by hand — any name, any GSTIN. Same nine checks. Or attach a
random PDF to the photo-ID slot and watch preflight refuse it before submission.

**"What if the model is down?"**
`LLM_PROVIDER=offline`, re-run anything. Same verdict, template prose.

**"How would I add a category?"**
`data/profiles/categories/professional.json`. Point at `"requirement": "na"`.
No Python anywhere.

**"How do you know the tests aren't vacuous?"**
Delete the disclosure gate in `build_vendor_items`, run
`pytest tests/test_scenarios.py`. Two go red. Restore. One test in this repo
*was* vacuous — it read a field that's never persisted, so it asserted "no leak"
against `None` and passed with the gate removed entirely.

**"Why not one LLM call to decide everything?"**
An IBAN checksum doesn't need a model — it'd be slower, non-reproducible and
less correct. The two AI checks are never the sole basis for an approval.

**"What's not built?"**
Live registry and sanctions APIs, both behind provider adapters with seeded
data. No real auth on the ops routes. Single process, so no horizontal scale.
Say it plainly — it's in the README.

---

## Deeper reference

- [`docs/HLD.md`](docs/HLD.md) — system diagrams, decision model, trade-offs
- [`docs/LLM-Router.md`](docs/LLM-Router.md) — routing, limiting, failover
- [`README.md`](README.md) — full design doc
