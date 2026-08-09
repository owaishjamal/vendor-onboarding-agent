# Zamp — Vendor Onboarding & Verification

**A vendor submission goes in. A decided status comes out — approved, approved with conditions, pending, or rejected — with every reason visible, every decision attributable to a rule, and a drafted reply to the vendor where disclosure is appropriate.**

Built for Zamp's AI Solutions Associate case study, **PS-2: Vendor onboarding — from submission to approval**.

---

## Contents

1. [The problem](#1-the-problem)
2. [What it does](#2-what-it-does)
3. [Try it in two minutes](#3-try-it-in-two-minutes)
4. [Architecture](#4-architecture)
5. [The decision model](#5-the-decision-model)
6. [Generalisation: categories as data](#6-generalisation-categories-as-data)
7. [Deterministic vs AI](#7-deterministic-vs-ai-and-why-the-line-is-drawn-where-it-is)
8. [The nine checks](#8-the-nine-checks)
9. [Document verification](#9-document-verification)
10. [Edge cases](#10-edge-cases)
11. [The ops copilot](#11-the-ops-copilot)
12. [Design decisions and trade-offs](#12-design-decisions-and-trade-offs)
13. [What is deliberately not built](#13-what-is-deliberately-not-built)
14. [Testing](#14-testing)
15. [Running and deploying](#15-running-and-deploying)
16. [Repository map](#16-repository-map)

---

## 1. The problem

Before a company can pay a supplier, someone has to verify the supplier is legitimate: company details, banking information, tax registration, compliance documents — collected, cross-checked, and confirmed complete, consistent and credible.

In practice vendors submit incomplete forms, attach the wrong documents, and supply details that contradict each other in ways only visible when you put two fields side by side. When something is missing, someone chases it. The review is manual, the follow-up is manual, and the audit trail is whatever is in somebody's inbox.

A bad onboarding causes payment fraud, compliance breaches, or both. **The specific failure that costs the most money is not a missing document — it is a submission where every field is valid and the vendor is still not who they say they are.**

That observation drives most of what follows.

---

## 2. What it does

```
Vendor picks a category
        │
        ▼
Sees only the fields and documents that apply to THEM      ← profile-driven, not one giant form
        │
        ▼
Attaches documents — each verified on attach               ← wrong file caught before submitting
        │
        ▼
Nine checks run and stream live                            ← 7 deterministic, 2 AI, labelled as such
        │
        ▼
Verdict + confidence + every finding with evidence
        │
        ├─ APPROVED                  → onboarded, no human involved
        ├─ APPROVED_WITH_CONDITIONS  → onboarded, obligations recorded and chased
        ├─ PENDING_INFO              → back to the vendor, everything wrong in ONE message
        ├─ PENDING_REVIEW            → to a human, with the conflicting evidence attached
        └─ REJECTED                  → terminal, and the vendor is not told why
        │
        ▼
Ops dashboard: full report, verification matrix, audit trail, grounded copilot
```

---

## 3. Try it in two minutes

The fastest way to understand the system is to run the prepared cases. Open the vendor form and pick one from **"Or load a prepared case"** — the form fills, you press submit, and you watch nine checks decide.

Each case states the verdict it expects **before** it runs, so you are watching a prediction succeed rather than reading a narration afterwards.

| Case | Category | Expected | What it shows |
|---|---|---|---|
| Clean goods supplier | Goods | `APPROVED` | The baseline — nine checks, no findings, no human |
| Missing paperwork | Services | `PENDING_INFO` | Everything wrong reported in **one** message |
| **Bank account already belongs to another vendor** | Logistics | `PENDING_REVIEW` | A perfect submission that is still fraud |
| **Director shares a name with a sanctioned individual** | Goods | `APPROVED` | A 100% name match, correctly cleared |
| **Director confirmed on a sanctions list** | Goods | `REJECTED` | The same machinery, opposite outcome |
| **Individual professional, no incorporation** | Professional | `APPROVED` | Requirements that must *not* be asked for |
| **Insurance valid today, expires in 3 weeks** | Construction | `APPROVED_WITH_CONDITIONS` | Neither pass nor fail |

These are **not** mocks. Each is an ordinary set of form values submitted through the ordinary endpoint. `tests/test_scenarios.py` re-runs all seven against the real pipeline and fails the build if any stops reaching its advertised verdict — so the table above cannot quietly go stale.

---

## 4. Architecture

### 4.1 The shape of the system

```
┌────────────────────────────────────────────────────────────────────────┐
│  BROWSER                                                               │
│                                                                        │
│   Vendor form  ──────┐                        ┌────── Ops dashboard    │
│   (dynamic, driven   │                        │       queue → case     │
│    by the profile)   │                        │       report, matrix,  │
│                      │                        │       audit, copilot   │
└──────────────────────┼────────────────────────┼────────────────────────┘
                       │  multipart + SSE       │  REST
┌──────────────────────▼────────────────────────▼────────────────────────┐
│  FastAPI  (single process — also serves the built SPA)                 │
│                                                                        │
│   /v1/categories   /v1/requirements   /v1/scenarios                    │
│   /v1/cases/form/stream        ← submission, streams each check        │
│   /v1/documents/preflight      ← one document, verified on attach      │
│   /v1/cases/{id}  /action  /chat                                       │
└──────────────────────┬─────────────────────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────────────────────┐
│  PIPELINE RUNNER                                                       │
│                                                                        │
│   resolve requirements  →  run 9 checks  →  aggregate findings         │
│                                          →  severity ⇒ status          │
│                                          →  confidence ⇒ routing       │
│                                          →  compose vendor + ops prose │
└───┬───────────────┬───────────────┬───────────────┬────────────────────┘
    │               │               │               │
┌───▼─────┐   ┌─────▼──────┐  ┌─────▼──────┐  ┌─────▼───────┐
│ Profiles│   │  Document  │  │ Reference  │  │  LLM        │
│ (JSON)  │   │  reader    │  │ data       │  │  Gemini or  │
│ country │   │  text→OCR  │  │ registry   │  │  offline    │
│ category│   │  classifier│  │ sanctions  │  │  templates  │
│ client  │   │  DVA agent │  │ vendor mstr│  │             │
└─────────┘   └────────────┘  └────────────┘  └─────────────┘
                       │
                 ┌─────▼──────┐
                 │  SQLite    │  cases, findings, checks, audit
                 └────────────┘
```

**One process, one database file, no queue, no broker.** That is a deliberate choice, discussed in [§12](#12-design-decisions-and-trade-offs).

### 4.2 A submission, end to end

```
Vendor                 API                 Runner              Checks           Store
  │                     │                    │                   │                │
  ├─ pick category ────►│                    │                   │                │
  │◄─ fields + docs ────┤ (resolved against  │                   │                │
  │   that apply         │  country+category)│                   │                │
  │                     │                    │                   │                │
  ├─ attach document ──►│── preflight ──────────────────►│       │                │
  │◄─ "that's a cover    │   classify + name check       │       │                │
  │    letter, not ID"   │                    │           │      │                │
  │                     │                    │                   │                │
  ├─ submit ───────────►│── create case ─────────────────────────────────────────►│
  │                     ├── run_pipeline ───►│                   │                │
  │                     │                    ├─ completeness ───►│                │
  │◄═ SSE: check result ═══════════════════════════════════════════════           │
  │                     │                    ├─ formats ────────►│                │
  │◄═ SSE: check result ═══════════════════════════════════════════════           │
  │                     │                    │   … 9 checks, none short-circuits  │
  │                     │                    │                   │                │
  │                     │                    ├─ severity ⇒ status                 │
  │                     │                    ├─ confidence ⇒ routing              │
  │                     │                    ├─ compose prose ───┤                │
  │                     │                    ├── persist ───────────────────────► │
  │◄═ SSE: done + verdict + findings + conditions ═════════════════════           │
```

The stream matters for more than cosmetics: onboarding checks take seconds, and a vendor watching nine named checks resolve understands *what was verified*. A spinner followed by "rejected" does not build that understanding.

### 4.3 Requirement resolution — three layers

```
        country defaults (in.yaml, gb.yaml, us.yaml, de.yaml, sg.yaml)
                 │        GSTIN format, IFSC scheme, baseline documents
                 ▼
        category profile (goods, services, construction,
                 │         logistics, professional, other)
                 │        adds fields + documents; MAY WAIVE country ones
                 ▼
        client profile (optional per-tenant overrides)
                 │
                 ▼
        resolve conditionals against THIS submission
                 │        "carrier licence, because fleet_size > 0"
                 ▼
        the concrete ask for this vendor
```

Each layer only overrides what it names. A category that says nothing about `tax_id` inherits the country pack unchanged.

---

## 5. The decision model

### 5.1 Severity determines status. That is the whole rule.

```python
status = SEVERITY_TO_STATUS[max(f.severity for f in findings)]
```

No weighting, no tuned threshold, no score to argue about. Each check decides how serious its own finding is, at the point where it has the context to judge, and the status falls out of the most serious thing present.

| Severity | Meaning | Status |
|---|---|---|
| `INFO` | Recorded, affects nothing | `APPROVED` |
| `ADVISORY` | Worth knowing, not worth blocking | `APPROVED` |
| `CONDITION` | Fine now, must be resolved later | `APPROVED_WITH_CONDITIONS` |
| `NEEDS_INFO` | The vendor can fix this | `PENDING_INFO` |
| `NEEDS_REVIEW` | A human must judge | `PENDING_REVIEW` |
| `REJECT` | Terminal | `REJECTED` |

`CONDITION` was inserted *between* `ADVISORY` and `NEEDS_INFO` deliberately. Because status is `max(severity)`, ordering alone guarantees the invariant: **a condition can never upgrade a case, only hold it or be overtaken by something worse.**

### 5.2 Confidence is a second, one-way gate

Severity answers *what is wrong*. Confidence answers *how sure are we*, and it is built from things that can be measured rather than a vibe:

| Component | Weight | Measures |
|---|---|---|
| Form corroboration | 0.40 | How much of the form documents actually back |
| Document read quality | 0.25 | Text layer vs marginal OCR |
| Classification confidence | 0.15 | How sure we are each document is what it claims |
| Certainty | 0.20 | Penalty for findings we could not resolve |

```
                       ┌── disqualifying finding? ──────────► REJECTED
                       │
severity status ───────┼── PENDING_REVIEW / PENDING_INFO ───► unchanged
                       │   (a human is already involved)
                       │
                       └── APPROVED / _WITH_CONDITIONS
                                    │
                            confidence ≥ 0.85 ? ──── yes ───► as decided
                                    │
                                    └───────────── no ─────► PENDING_REVIEW
```

**Confidence can only ever move a case towards a human, never away from one.** A low score blocks an auto-approval; it can never turn a flagged case into an approval. Every component is reported, so a reviewer sees *why* confidence was 0.76 rather than being handed an opaque number.

### 5.3 What the vendor is told

Two gates, both load-bearing:

1. **Status gate** — vendor-facing text is produced only for `PENDING_INFO` and `APPROVED_WITH_CONDITIONS`. Those are the two states where the vendor can *act*.
2. **Per-finding gate** — a finding must carry an explicit `vendor_message` to be disclosed.

A rejected vendor is told the application was unsuccessful and nothing more. Telling someone they matched a sanctions list is **tipping off** — a criminal offence in several jurisdictions, and it also teaches an adversary exactly which name to change. `tests/test_scenarios.py` asserts that the words "sanction", "OFAC", "denied" and "watchlist" never reach vendor-facing text.

---

## 6. Generalisation: categories as data

The brief asked for a system that works beyond one vendor type. The test of that is not whether it *handles* many categories — it is whether adding one requires **shipping code**. Here it does not.

A category is a JSON file:

```json
{
  "category": "logistics",
  "extends": "country_defaults",
  "fields": [
    { "key": "fleet_size", "type": "number", "requirement": "required",
      "why": "Owned or contracted vehicles available for our lanes." }
  ],
  "documents": [
    { "key": "carrier_licence", "requirement": "conditional",
      "when": "fleet_size > 0",
      "why": "Operating vehicles commercially requires a valid transport licence." }
  ]
}
```

Four tiers: `required`, `conditional`, `optional`, `na`. Conditionals carry a `when` expression and every item carries a `why` that is shown to the vendor — *"Asked because you operate vehicles"* rather than an unexplained upload box.

**The `when` grammar is deliberately not `eval`.** It is a small parser supporting `== != >= <= > <`, `in`, `not in`, `is present`, `is absent`, joined by `and`/`or`. Anything it cannot parse evaluates to `false`, so a malformed profile asks for *less*, never executes something. Profiles are configuration; configuration that can run arbitrary code is not configuration.

### Waiving, not just adding

The interesting half of generalisation is **removing** requirements. The India pack demands a GSTIN from every vendor — correct for a company, wrong for a freelancer below the registration threshold who cannot obtain one. Without a waiver such a vendor is parked in `PENDING_INFO` forever: they cannot supply what does not exist, and no reviewer can conjure it.

So a category profile may waive a country-pack field by declaring it `na`. Two constraints keep that safe, and both are enforced by tests:

* **Only the category layer may waive**, and **only via explicit `na`**. The country-defaults profile already lists `tax_id` as `optional` for form-layout purposes; honouring `optional` here would have silently dropped the GSTIN requirement for *every* Indian vendor. That bug was written, caught by `test_a_company_in_the_same_country_is_still_asked_for_its_tax_id`, and fixed.
* **Silence inherits.** A category that says nothing about a field gets the country pack unchanged.

---

## 7. Deterministic vs AI, and why the line is drawn where it is

Every check declares how it decides:

| | Deterministic | AI |
|---|---|---|
| **How** | Regex, checksum, set comparison, registry lookup | Reads unstructured content, makes a judgement |
| **Reproducible** | Same input, same answer, forever | Confidence-scored, can be wrong |
| **Testable** | To the character | Statistically, against fixtures |
| **Count** | 7 of 9 | 2 of 9 |
| **May alone reject** | Yes (sanctions) | **Never** |

The ops report renders them in separate groups, because *"the IBAN checksum failed"* and *"the model thinks this looks like a resume"* warrant completely different levels of trust — and a reviewer who cannot tell them apart will either over-trust the model or ignore genuine signals.

**An IBAN checksum does not need a language model.** Using one there would be slower, more expensive, non-reproducible and less correct. The model earns its place in exactly two places — reading documents, and judging whether a business description matches its claimed category — plus composing prose, which is a presentation concern and never touches a decision.

If the model is unavailable, the pipeline still decides. `LLM_PROVIDER=offline` swaps in deterministic templates; every verdict in this README is reproducible with no API key and no network.

---

## 8. The nine checks

None short-circuits. Every check runs on every submission, even after a rejection is certain.

| # | Check | Kind | Catches |
|---|---|---|---|
| 1 | Completeness | deterministic | Missing fields and documents, resolved per category |
| 2 | Format validation | deterministic | GSTIN/PAN/IFSC/IBAN/ABA shape, IBAN mod-97 checksum |
| 3 | Cross-field consistency | deterministic | IBAN country ≠ claimed country, free-email domains, bank name mismatch |
| 4 | Document verification | **AI** | Is this document what it claims? Whose name is on it? Has it expired? |
| 5 | Form vs document | **AI** | Every claim corroborated, contradicted or unevidenced |
| 6 | Client rules | deterministic | Per-tenant custom field rules |
| 7 | Registry verification | deterministic | Does the company exist? Is it active? Does the name match? |
| 8 | Denied-party screening | deterministic | Sanctions, two-factor on DOB + nationality |
| 9 | Duplicates & shared banking | deterministic | Same account, registration or tax ID as an existing vendor |

### Why nothing short-circuits

An invoice pipeline stops at the first decisive check — correct there, because further work cannot change the answer and costs money.

Onboarding is the opposite. If a submission has four problems and you stop at the first, the vendor fixes one thing, resubmits, and is told about the next. **Four round trips, days each.** The entire cost of onboarding is round trips. So every check runs, and the vendor gets one message listing everything.

The cost is that every submission does full work. Onboarding volume is dozens per quarter, not thousands per day — the right trade here, and the wrong trade for invoices.

---

## 9. Document verification

```
file ─► read ──► classify ──► verify ──► admit as evidence
        │         │            │
        │         │            ├─ is it the type the slot expects?
        │         │            ├─ does the name match the legal name?
        │         │            ├─ has it expired? does it expire soon?
        │         │            └─ is it stale, if freshness applies?
        │         │
        │         └─ content signals, not filename
        │            ("bank_letter.pdf" containing a CV is a CV)
        │
        └─ PDF text layer → OCR fallback → confidence score
```

Three details worth calling out:

**Freshness applies only to documents attesting to a current state.** A bank letter proves an account exists *now*; eighteen months later it proves nothing. A certificate of incorporation records a one-time event and never goes stale — demanding a "recent" one asks for something that does not exist. Blanket age limits on every document is a common and annoying onboarding failure.

**Preflight runs the same agent on one file at attach time**, so a wrong document is caught before submission rather than after a full round trip.

**Not recognising a document is not the same as the document being right.** Preflight originally only ran its type checks when a slot declared an accepted-types list — and category-added documents declare none, so a cover letter dropped into the photo-ID slot came back with a green tick. Unrecognised now always warns. The classifier was then taught the types the categories actually ask for, because warning on everything is as useless as approving everything.

---

## 10. Edge cases

> *"Design and build 2–4 edge cases of your own. They should be realistic scenarios where the process has to behave differently from the happy path."*

An edge case is only interesting if it is a case where **the obvious rule gives the wrong answer**. Each of these breaks a different obvious rule, and each is one click away in the form.

### 10.1 Bank account already belongs to another vendor → `PENDING_REVIEW`

> **Obvious rule:** every field valid → approve.

Continental Freight's submission is flawless. Every format valid, every document corroborates the form, the company is in the registry, nobody is on a sanctions list. And the bank account they supplied is already on the master file under **Atlas Haulage Group Inc**.

Nothing *inside* the submission is wrong. The fraud is only visible by comparing against records we already hold — this is the signature of invoice-redirection fraud, where an attacker onboards a plausible supplier whose account they control.

**Why not reject?** Group treasury, a parent collecting for a subsidiary, and factoring arrangements all produce this exact pattern legitimately. Auto-rejecting breaks real suppliers; auto-approving is how money leaves. A human decides, with the conflicting record attached so it can be resolved in one sitting.

*The collision is real — the account number hashes to a fingerprint already in `vendor_master.json`, not a flag set by hand.*

### 10.2 Sanctions namesake → `APPROVED` (and its twin → `REJECTED`)

> **Obvious rule:** name matches a sanctions list → reject.

Meridian Rail's director is **Dmitri Volkov**. So is an OFAC SDN entry. A 100% name match.

He is cleared, because his date of birth (1984-03-22, US) does not match the listing (1971-08-14, RU). The near-match is still written to the audit trail — *"we saw it and cleared it, here is why"* is a materially better record than never having looked.

Names are not unique, especially transliterated ones. Screening on names alone means turning away legitimate suppliers because someone shares a surname with a designated person: a commercial loss and a fairness problem. Secondary identifiers are what make screening a *decision* rather than a name search.

**Its twin is essential.** `sanctions-confirmed` supplies the same name with the *listed* DOB and nationality and is rejected outright — one of very few places an automated system should refuse rather than escalate, because paying a sanctioned party is a criminal offence and there is no commercial judgement to exercise. Without this second case, "we clear namesakes" is indistinguishable from "we never reject anyone".

### 10.3 Individual professional, no incorporation → `APPROVED`

> **Obvious rule:** vendors must supply a certificate of incorporation.

Ananya Krishnan is a freelance architect. She has no certificate of incorporation, and no GSTIN — she is below the registration threshold.

A one-size form demands both, and she cannot produce either. This is the single most common reason good freelancers abandon onboarding, and it also generates `PENDING_INFO` cases **no reviewer can ever resolve**. The professional profile marks incorporation `na`, waives the GSTIN, and lets her government ID stand in as proof of identity.

Watch the form shrink when you load this one. That is the generalisation claim being demonstrated rather than asserted — and the waiver lives in JSON, not in an `if category == "professional"` branch.

### 10.4 Insurance valid today, expires in three weeks → `APPROVED_WITH_CONDITIONS`

> **Obvious rule:** expired → block; valid → pass.

Girish Constructions' public liability cover is valid today and lapses in 21 days. A binary test gets this wrong in both directions: blocking a vendor whose cover is currently valid is how teams learn to route around procurement when they have a deadline; waving it through is how a contractor ends up on site next month with lapsed liability cover.

So it becomes the fourth verdict — onboarded now, with the renewal recorded against the vendor, chased before the date, and disclosed to the vendor because it is one of the two states where they can act.

### Why these four

They cover four **distinct decision shapes**: `NEEDS_REVIEW` on clean data, `APPROVED` despite an alarming signal, `APPROVED` on a reduced requirement set, and `APPROVED_WITH_CONDITIONS`. Four variations on "a field is missing" would demonstrate nothing. `test_edge_cases_cover_distinct_verdicts` enforces this.

---

## 11. The ops copilot

A reviewer can ask questions about a case in plain language: *"why was this flagged?"*, *"what should I ask the vendor for?"*, *"can I approve this?"*

It is **grounded in the case record**. Intents are matched against the actual findings, documents and verification matrix, and answered from them. The model is only consulted for questions the grounded layer cannot match, and only ever with the case as context.

**When it does not know, it says so and offers what it can answer** — rather than inventing a plausible-sounding compliance opinion. A copilot that hallucinates on a case that is about to be approved is worse than no copilot.

---

## 12. Design decisions and trade-offs

| Decision | Why | Cost |
|---|---|---|
| **Nothing short-circuits** | One message to the vendor; complete picture for the reviewer | Full work on every submission — fine at onboarding volume |
| **Severity → status, no scoring** | Auditable and arguable; every verdict traces to one finding | Less expressive than a weighted model; the right trade for compliance |
| **Confidence is one-way** | A score can block an approval, never create one | Some clean-but-thin submissions go to a human |
| **`CONDITION` between `ADVISORY` and `NEEDS_INFO`** | Ordering alone guarantees conditions never upgrade a case | Renumbering broke magic ints — now `BLOCKING_SEVERITY` |
| **Categories as JSON** | New category ships no code | A schema to keep honest; mitigated by tests |
| **Custom `when` grammar, not `eval`** | Profiles are config; config must not execute code | Small parser to maintain; unparseable ⇒ `false` |
| **7 deterministic / 2 AI, labelled** | Reviewers must know what to trust | Two code paths to explain |
| **Offline provider parity** | Every result reproducible with no key, no network | Templates to maintain alongside prompts |
| **SQLite, no queue, one process** | Deploys as one container from one URL | Not horizontally scalable — see below |
| **Documents on disk** | No object-store dependency | Ephemeral filesystems lose them on redeploy |
| **Streamed checks (SSE)** | The vendor sees *what* was verified | Long-lived connections need idle timeouts |

### On the single-process choice

This is the decision most likely to be questioned, so: it is deliberate, and it is a **demo-fidelity** trade rather than a claim about production.

An earlier revision had Redis, RQ workers, Postgres and S3. It was strictly worse *for the purpose this system has to serve* — it could not be handed to someone as one URL, and every reviewer who could not run `docker compose` saw nothing. The current build is one container: FastAPI serves the built SPA and the API, SQLite holds the cases, documents sit on disk.

The seams that would matter are still seams. Storage is behind an interface, the registry and screening providers are adapters, the LLM client is provider-agnostic. Moving to Postgres and object storage is a configuration change at those boundaries, not a rewrite. **What is genuinely absent is horizontal scale and durable document storage** — stated plainly rather than implied otherwise.

### On authentication

The ops routes have **no real authentication**. The bundled `VITE_API_KEY` ships inside the JavaScript, which makes it abuse deterrence, not a credential. The vendor portal's per-case token is the closest thing to a real one, and it is unguessable but not revocable.

This is a case-study build. Saying so is more useful than implying otherwise; a reviewer who assumed these endpoints were protected would draw exactly the wrong conclusion about what is production-ready.

---

## 13. What is deliberately not built

* **Live registry / sanctions APIs.** Both sit behind provider adapters with seeded data. Swapping in GLEIF, Companies House or a Dow Jones feed is an adapter, not a redesign. Wiring live credentials would prove integration plumbing, not decision quality.
* **A trained document model.** Extraction is text-layer-first with OCR fallback and content-signal classification. A production system pairs this with a fine-tuned or vision model. The point being demonstrated is the *cross-referencing and confidence handling*, not layout-robust OCR.
* **Real user accounts.** Role switching is a demo mechanism.
* **Notifications, SLA timers, bulk actions, reviewer assignment.** Real needs; none teaches anything about the verification problem.
* **Multi-tenancy beyond profile overrides.** The profile layer is where it would go.

---

## 14. Testing

```
257 tests   pytest tests/ -q
 11/11      python scripts/evaluate.py   — 100% accuracy, 100% recall, 0 false positives
```

| Suite | Covers |
|---|---|
| `test_generalized.py` | Conditions grammar, category resolution, verdicts, check kinds, copilot, Gemini parsing, preflight |
| `test_scenarios.py` | Every prepared case reaches its advertised verdict — **and why** |
| `test_end_to_end.py` | A real uvicorn process, real multipart uploads, real SSE, all six categories |
| `scripts/evaluate.py` | Labelled fixtures with expected outcomes; reports precision and recall |

The scenario tests assert **mechanism, not just status**. `shared-bank-account` reaching `PENDING_REVIEW` only counts if it got there on the shared-account finding — if it started failing for a missing document, the status would still pass while the scenario stopped demonstrating anything.

Several tests exist specifically to prevent regressions that already happened once:

* `test_a_company_in_the_same_country_is_still_asked_for_its_tax_id` — the waiver leaking globally
* `test_confirmed_match_does_not_tell_the_vendor_why` — tipping off
* `test_the_waiver_is_data_not_a_branch_in_the_code` — hardcoded category logic
* `test_scenario_documents_target_slots_the_category_actually_asks_for` — silently ignored documents

---

## 15. Running and deploying

### Local

```bash
# backend
pip install -r requirements.txt
uvicorn backend.app.api.app:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev
```

Works with **no API key** — `LLM_PROVIDER=offline` gives deterministic templates and every verdict in this document is reproducible.

For the AI checks and the copilot, add a Google AI Studio key:

```bash
echo 'GEMINI_API_KEY=your_key_here' > .env      # gitignored
python scripts/check_env.py                      # verifies without printing the key
```

### Docker — one container, one URL

```bash
docker build -t zamp-onboarding .
docker run -p 8000:8000 -e GEMINI_API_KEY=... zamp-onboarding
```

FastAPI serves the built SPA and the API from the same origin, so there is no CORS setup and nothing to configure. See `DEPLOY.md` and `render.yaml`.

> **Note on free-tier quotas.** Demo seeding composes its prose offline. Eleven cases at boot previously meant twenty-two model calls before the port opened — enough to exhaust a free-tier quota and stall startup past a platform's port scan. Seeding now completes in ~3.5s even with a dead key.

---

## 16. Repository map

```
backend/app/
  models.py            Severity, Status, FindingCode — the vocabulary
  scenarios.py         The prepared cases, with their expected verdicts
  config.py            Env + .env loading, provider inference
  api/app.py           FastAPI: endpoints, SSE, static SPA
  pipeline/
    runner.py          Runs 9 checks, aggregates, decides
    confidence.py      Score components and one-way routing
  checks/              The nine checks, one module each
    document_reader.py Text layer → OCR → field extraction
  dva/
    classifier.py      Content-signal document classification
    agent.py           Per-document verdict: type, name, expiry, freshness
    preflight.py       Same agent, one file, at attach time
  profiles/
    store.py           Three-layer resolution
    conditions.py      The safe `when` grammar
  llm/
    client.py          Gemini + offline, one interface
    ops_copilot.py     Grounded intent matching
  rules/*.yaml         Country packs
  storage/             SQLite via SQLAlchemy Core

data/profiles/categories/*.json    Six categories — the generalisation surface
backend/seed/                      Registry, sanctions list, vendor master
frontend/src/views/vendor/Wizard.tsx   Category → dynamic form → live run → verdict
frontend/src/views/CaseDetail.tsx      Report, checks, documents, copilot
tests/                             257 tests
docs/                              PRD, Architecture, Rules, compliance matrix
```

---

## In one paragraph

A vendor picks what they supply and is asked only for what that actually requires. Every claim is checked nine ways — seven deterministically, two with a model, and the report says which is which. The verdict is the most serious thing found, with a confidence score that can send a case to a human but never rescue one. What the vendor is told is gated separately from what the reviewer sees, because a rejected applicant should not learn why. The interesting cases are not the missing documents — they are the flawless submission using someone else's bank account, the innocent man who shares a name with a sanctioned one, the freelancer asked for a certificate that cannot exist, and the certificate that is valid today and worthless next month.
