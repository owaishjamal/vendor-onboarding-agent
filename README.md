# Vendor Onboarding

**A vendor submission goes in. A decided status comes out — approved, pending, or rejected — with every reason visible and, where appropriate, a drafted reply to the vendor.**

Built for Zamp's AI Solutions Associate case study, PS-2.

---

## The problem

Before a company can pay a supplier, someone has to verify they are legitimate: company details, banking information, tax registration, compliance documents — collected, cross-checked, and confirmed to be complete, consistent and credible.

In practice vendors submit incomplete forms, attach the wrong documents, and supply details that contradict each other in ways only visible when you put two fields side by side. When something is missing, someone chases it manually. The review is manual, the follow-up is manual, and the only audit trail is whatever is in somebody's inbox.

A bad onboarding — a vendor who slips through with inconsistent details — causes payment fraud, compliance breaches, or both.

---

## Two design decisions everything else follows from

### 1. Nothing stops early

An invoice pipeline short-circuits: the moment a check is decisive, it stops. Correct there — once you know an invoice is a duplicate, nothing else matters.

Onboarding is the opposite. If a submission has four problems and you stop at the first, the vendor fixes one thing, resubmits, and gets told about the next. **Four round trips, days of latency each.** The entire cost of onboarding is round trips.

So every check runs on every submission — even after a rejection is already certain — and the vendor receives **one message listing everything at once**.

The cost is that every submission does the full amount of work. Onboarding is dozens of vendors per quarter, not thousands per day, so that is the right trade. It would be the wrong trade for invoices.

### 2. Severity determines who acts, not how bad it is

```
status = SEVERITY_TO_STATUS[ max(severity of every finding) ]
```

That is the whole decision rule. No weighted score, no tuned threshold. Each check decides how serious its own finding is, where it has the context to judge, and the status falls out of the most serious thing present.

The important distinction is between the two middle severities, and it is about **who can fix it**:

| Severity | Meaning | Goes to |
|---|---|---|
| `ADVISORY` | Worth noting on the file | Nobody — recorded |
| `NEEDS_INFO` | The vendor can fix this themselves | The vendor, in an email |
| `NEEDS_REVIEW` | A human on our side must judge | Internal reviewer only |
| `REJECT` | Terminal | Compliance |

A missing tax certificate and a bank account belonging to somebody else are both "not approved" — but routing them to the same place would be a serious mistake.

---

## The disclosure rule

**A vendor email is generated only for `PENDING_INFO`.** Never for `PENDING_REVIEW`, never for `REJECTED`.

This is not politeness. It is enforced in one place and covered by tests:

- A **rejected** vendor is rejected because of a denied-party match. Emailing them a friendly request for a bank letter tells a sanctioned party which control caught them, and opens correspondence with someone the business is legally barred from transacting with.
- A **pending-review** vendor is under investigation for something like a bank account that does not match their name. Contacting the submitter mid-review can tip off a fraudster and taints the review.

So a rejected case can carry a perfectly ordinary "your bank letter is missing" finding and still send nothing at all. `VS-06` is exactly that case, and a test asserts the email stays empty.

There is a second gate too: only `NEEDS_INFO` findings ever become vendor-facing text. Filtering on severity rather than on "does this finding happen to have a vendor message" means a `NEEDS_REVIEW` finding cannot leak into an email even if someone later attaches vendor text to one by mistake.

---

## Where the LLM is used

Two places. Neither of them decides anything.

| Stage | Who |
|---|---|
| Completeness, format, consistency, documents, screening, duplicates | **Code** |
| The status decision | **Code** — a max over severities |
| Drafting the email to the vendor | **LLM** |
| Summarising the case for the reviewer | **LLM** |

By the time either prompt runs, the status and every finding are already fixed. The model writes two documents with different audiences and different disclosure rules — which is why they are two prompts and not one.

---

## Design & deployment docs

- **[docs/HLD.md](docs/HLD.md)** — one-page high-level design with a system diagram.
- **[docs/Architecture.md](docs/Architecture.md)** — full component detail.
- **[DEPLOY.md](DEPLOY.md)** — how to get a live URL, and exactly **how it behaves on new/unseen inputs**.
- **[PRODUCTIZATION.md](PRODUCTIZATION.md)** — honest map from case study to enterprise product (what's a swap-in vs a rebuild vs non-code).

## Productization seams (all default-off)

The architecture is built so enterprise requirements are swap-ins, not rewrites. Each is off/permissive by default so the demo and tests are unaffected:

- **Document processing** — OpenCV preprocessing before OCR, read-caching by file hash, and a pluggable extractor (`DOC_EXTRACTOR=vision` uses a vision-language model for arbitrary layouts; falls back to OCR with no key).
- **Data providers** — registry and screening are behind interfaces; a real Companies House provider ships in-code (`REGISTRY_PROVIDER=companies_house` + key), sanctions feeds plug in the same way.
- **Enterprise** — optional bearer auth (`API_TOKEN`), per-tenant isolation (`X-Org-Id`), upload validation, `/metrics`, structured logs (`LOG_JSON=1`).

## Quickstart

```bash
make install      # python deps + npm install
make seed         # generate reference data, test submissions, documents
```

Two terminals for development:

```bash
make api          # backend on http://127.0.0.1:8001
make ui           # frontend on http://127.0.0.1:5174
```

Or run the whole product as **one URL** (the deployable shape):

```bash
make serve        # builds the UI and serves everything on http://127.0.0.1:8001
# or:  make docker     (build + run the container on :8000)
```

For a public live link (offline, no API key needed), see **[DEPLOY.md](DEPLOY.md)** — one-click on Render via the included `render.yaml` + `Dockerfile`.

Open **http://localhost:5174**.

No API key required — `LLM_PROVIDER=offline` composes both documents from templates. Every check, finding, status and screen behaves identically; only the wording differs. Set `LLM_PROVIDER=anthropic` (or `openai` / `gemini`) in `.env` to use a real model.

Ports are 8001/5174 so this runs alongside the PS-1 build on 8000/5173.

```bash
make test          # 95 tests
make eval          # metrics on the 11 golden cases
make eval-volume   # the same metrics on 250 generated cases, incl. plausible fraud
make calibrate     # sweep the screening threshold and show the tradeoff curve
make reset         # clear case history
```

`make eval-volume` prints the number a buyer actually trusts — measured at scale, not on the handful of cases the author designed:

```
  Status accuracy .............. 250/250  (100.0%)
  Auto-approve precision ....... 100.0%   (25 correct, 0 WRONG of 25 auto-approvals)
  Fraud / compliance recall .... 100.0%   (125/125 signal cases caught)
  False-positive rate .......... 0.0%   (clean vendors wrongly sent to review)
```

The generated set includes the *plausible* fraud a similarity threshold alone would miss (an account named "&lt;Company&gt; Holdings", a fabricated-but-internally-consistent company). It found a real miss during development — subtle-name fraud slipped at 96% — which was root-caused and fixed; it now holds at 100%. `make calibrate` shows *why* the thresholds are set where they are.

---

## The seven checks

```
  Submission (JSON)
      │
      ├─ 1  COMPLETENESS ......... required fields + required documents,
      │                            driven by the country rule pack.
      │                            Collects every gap, never stops at the first.
      │
      ├─ 2  FORMAT ............... tax ID and registration regex per country,
      │                            IBAN mod-97 check digits (ISO 13616),
      │                            ABA routing checksum (weighted 3-7-1),
      │                            SWIFT/BIC, email.
      │
      ├─ 3  CONSISTENCY .......... legal name vs bank account holder,
      │                            claimed country vs IBAN country,
      │                            claimed country vs tax ID country,
      │                            claimed country vs address country,
      │                            email domain vs stated website.
      │
      ├─ 4  DOCUMENTS ............ each attachment is READ (PDF text layer or
      │                            OCR for scans), its type detected, and its
      │                            name/number/dates cross-referenced to the
      │                            form. Low-confidence reads route to the vendor.
      │
      ├─ 5  REGISTRY ............. confirms the registration number EXISTS, is
      │                            active, and is registered to this name —
      │                            against a source OUTSIDE the submission. The
      │                            check a fabricated-but-consistent vendor fails.
      │
      ├─ 6  SCREENING ............ entity, trading name, every director AND the
      │                            bank account holder against denied-party
      │                            lists, in two confidence bands, resolved by a
      │                            second factor (DOB / nationality).
      │
      └─ 7  DUPLICATES ........... bank account already held by another vendor,
                                   duplicate registration number, duplicate tax ID.
      │
      ▼
  status = max(severity)   →   vendor email (PENDING_INFO only) + reviewer summary
```

Order is presentational only — no check consumes another's output, so they could run in parallel. They are sequenced so the live view reads naturally: *is it all here, is it well formed, does it agree with itself, does the paperwork back it up, who are these people, have we seen them before.*

---

## Rules live in YAML, not in code

```
backend/app/rules/
  common.yaml    name-matching bands, screening thresholds, document freshness
  us.yaml  gb.yaml  de.yaml  in.yaml  sg.yaml
```

Because the people who own these rules are not engineers. A compliance lead can open `gb.yaml`, see that a VAT number must match `^GB(\d{9}|\d{12})$`, and tell you it is wrong. They cannot do that with a regex buried in a validator.

**Adding a country is adding a file.** No code change. The UI renders the packs under the Rules tab.

Each pack defines the tax ID and registration formats, the payment scheme (`iban` / `aba` / `ifsc` / `swift_account`), and the required documents.

---

## Test submissions

`make seed` generates eleven submissions **and renders their documents to real files** the pipeline reads for real (one is a scanned image, to exercise OCR). **Banking details are computed, not typed** — IBAN check digits with the real mod-97 algorithm, ABA routing numbers with the real 3-7-1 checksum. Hand-typed fixtures would pass a regex and fail a real checksum, making the validators look broken when they were right.

| # | Vendor | Country | Scenario | Expected |
|---|---|---|---|---|
| 01 | Northwind Components | US | Complete and consistent, registry-verified | `APPROVED` |
| 02 | Brightline Analytics | GB | Missing VAT number + bank document | `PENDING_INFO` |
| 03 | Kessler Industrietechnik | DE | **Bank account held by "K. Weber", not the company** (read off the real bank letter) | `PENDING_REVIEW` |
| 04 | Sundara Textiles | IN | **Claims India, supplies a UK VAT number** | `PENDING_REVIEW` |
| 05 | Continental Freight | US | **Bank account already belongs to another vendor** | `PENDING_REVIEW` |
| 06 | Volkov Maritime | SG | **Director on a denied-party list, confirmed on DOB** | `REJECTED` |
| 07 | Pinnacle Design | GB | IBAN checksum fails; bank letter is a scan (OCR path) | `PENDING_INFO` |
| 08 | Meridian Rail | US | **Director shares a sanctioned name, but DOB clears the namesake** | `APPROVED` |
| 09 | Brightline Analytics | GB | **Corrected resubmission of 02 — supersedes it, 2/2 items resolved** | `APPROVED` |
| 10 | Harbourstone Interiors | GB | **Subtle redirection — account is "&lt;Company&gt; Holdings"** (a threshold alone misses this) | `PENDING_REVIEW` |
| 11 | Ashcroft Medical | GB | **Internally flawless, but the registration number exists in no registry** | `PENDING_REVIEW` |

### The four edge cases that carry the most weight

**Bank account holder is not the company (03).** Every field is individually valid — the IBAN passes mod-97, the VAT ID is well-formed, the registration number is real. The only thing wrong is that the account holder is a person and the vendor is a company. This is the signature of payment redirection fraud, and it is invisible to any single-field validator. Internal review, and deliberately not disclosed.

**Country contradiction (04).** Produces findings at *two different severities* from the same field: the GSTIN regex fails (`NEEDS_INFO`, vendor-fixable) and the tax ID is in UK format on an Indian vendor (`NEEDS_REVIEW`, needs a human). The status comes from the higher one, and the lower one is held back rather than lost. This case exists specifically to show the aggregation working.

**Shared bank account (05).** The strongest fraud signal in the set, and completely invisible from the submission alone — it only exists *relative to the vendor master*. A clean-looking new supplier whose account already belongs to Atlas Haulage. Deliberately `PENDING_REVIEW` and never auto-reject: group treasury arrangements, parent companies collecting for subsidiaries, and factoring assignments all produce legitimately shared accounts. Never auto-approve, never auto-reject, always a human — with the conflicting record attached so it is resolvable in one sitting.

**IBAN typo (07).** The deliberate contrast with 03. Also a banking problem, but a transposed digit is a *mistake*, not a signal — so it goes to the vendor with "this is usually a typo", not to a reviewer. Getting that triage right is the difference between a one-line correction and an accusation.

**Innocent namesake (08).** A director whose name matches a UK sanctions entry *exactly*. On name alone this rejects — and that would be a legitimate supplier blocked because of a surname. A supplied date of birth and nationality differ from the listed person, so the hit clears to advisory and the vendor is approved. This is why screening runs two-factor and why the near/confirm bands exist.

**Corrected resubmission (09).** Brightline (02) comes back with the missing VAT number and bank letter. The system recognises the same Companies House number, re-runs, supersedes the prior case, and shows *2 of 2 items resolved, nothing new*. This is the wait-and-recheck loop the problem statement describes — the part that's actually slow in real onboarding.

---

## What each attachment actually does now (real document reading)

Documents are no longer trusted field-blocks — every attachment is a rendered file the pipeline opens and reads:

- **PDF text layer first, OCR fallback for scans** (Pinnacle's bank letter in 07 is an image and goes through OCR at a discounted confidence).
- **Type detected from the content**, then compared to the type the vendor claimed — so a delivery note submitted as a bank letter is caught (`DOCUMENT_TYPE_MISMATCH`).
- **Name / number / dates read off the document** and cross-referenced to the form (Kessler's bank letter in 03 genuinely reads "K. Weber").
- **Low-confidence reads route to the vendor** for a clearer copy rather than being trusted (`DOCUMENT_LOW_CONFIDENCE`), the same never-guess principle as everything else.

Pasted JSON submissions with no file fall back to a provided field-block at full confidence, so the UI's paste mode still works — the cross-referencing is identical either way.

---

## Reviewer actions — the tool is a system of record, not a viewer

The queue is no longer read-only. A reviewer can **approve, reject, request info, mark-sent, or reopen** any case. Each action:

- appends to an **append-only action log** (who, when, what, and a free-text note),
- moves the case to an explicit human-decided status (`APPROVED_BY_REVIEWER` / `REJECTED_BY_REVIEWER`) that sits *alongside* the automated finding trail, never overwriting it.

This is the answer to "the only audit trail is whatever's in someone's inbox": the resolution now lives in the case.

---

## Project layout

```
backend/app/
  models.py               severity, status, closed finding-code enum
  config.py               infrastructure + the few genuinely global settings
  rules/                  per-country YAML packs + loader
  checks/
    base.py               name normalisation and fuzzy matching
    completeness.py       required fields and documents
    formats.py            regex + IBAN mod-97 + ABA checksum
    consistency.py        cross-field contradictions + subtle-name-fraud (multiset)
    document_reader.py    reads a real file: PDF text layer / OCR + confidence
    documents.py          attachments vs the form, type detection
    registry.py           external existence check (fabricated vendors fail here)
    screening.py          denied-party, two bands, DOB/nationality second factor
    duplicates.py         shared banking and duplicate identity
  llm/                    provider-agnostic client, prompts, offline composer
  pipeline/runner.py      run_pipeline (persisted) + assess() (pure, for eval)
  storage/                SQLite; checks, findings, actions all append-only
                          + resubmission linking + reviewer-override report
  api/app.py              FastAPI + SSE + reviewer-action + overrides endpoints
backend/seed/             vendor master, denied parties (DOB), company registry
frontend/src/views/       Intake, Queue (aging + overrides), CaseDetail, Rules
frontend/src/components/  ReviewerActions, CheckTimeline, FindingCard, Badges
scripts/build_fixtures.py generates reference data, submissions, and documents
scripts/render_documents.py renders each document to a real PDF / scan
scripts/evaluate.py       metrics on the 11 golden cases
scripts/eval_volume.py    metrics on 250 generated cases incl. plausible fraud
scripts/calibrate.py      threshold sensitivity sweep
tests/                    95 tests
```

---

## Notes on reliability

- **Offline mode is the default.** No key, no network, no rate limit.
- **Generated text is cached by content hash.** A rehearsed demo never re-hits the API.
- **A check that crashes becomes a `NEEDS_REVIEW` finding**, not a silent pass. "We could not run this control" is never grounds for approval.
- **An unsupported country never approves** — we cannot validate what we have no rules for, so we must not imply that we did.
- **Every degradation goes toward asking a human**, never toward waving something through.

---

## Known limitations

Honest about what's still stubbed, after three rounds of work:

- **The registry and denied-party lists are seeded files, not live feeds.** The registry *check* is real — a fabricated registration number fails it, and that's the structural point — but production wires it to Companies House / Handelsregister / an aggregator API and screening to a licensed provider. The adapters are the swap; the logic doesn't change.
- **Document OCR is layout-simple.** Attachments are read for real (text layer + OCR + confidence + type detection), but the reader parses a clean labelled layout. A production reader pairs it with a vision model for arbitrary real-world documents.
- **Reviewer actions don't send anything.** Approving or requesting info updates the case and its audit log; it doesn't actually email the vendor or write to an ERP. The decision record is real; the side effect is not wired.
- **A client that disconnects mid-stream leaves a case stuck in `RUNNING`.** The pipeline runs on the request as a generator; production would use a background worker.

### Closed across the improvement rounds

Round 2: real document reading (was stubbed), two-factor screening (was name-only), reviewer actions + resolution capture (queue was read-only), resubmission handling (submissions were orphans), and a `make eval` harness.

Round 3: **external registry verification** (a fabricated-but-consistent vendor no longer auto-approves — the core "verify they're legitimate" gap); **subtle-name-fraud detection** (a "…Holdings" account no longer slips a similarity threshold); **volume evaluation** on 250 generated cases including plausible fraud (replacing "100% on nine I designed"); **threshold calibration** (the magic numbers now sit on a defensible curve); a **reviewer-override report** (the captured resolutions became a live calibration signal); **regex validation tests** (the format patterns tested against real-world IDs); and **queue aging** (cases waiting on the vendor surface instead of vanishing).
