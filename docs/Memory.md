# Memory — working context

Running log so any new chat or tool can pick up without re-reading the whole codebase. Update the top section whenever state changes; leave the history below it.

---

## Current state — after improvement round 3

**Status: complete and green.** 95 tests passing. 11 golden samples, **7 checks**. Two evals: `make eval` (11 golden → 100%/100%/0) and `make eval-volume` (250 generated → 100.0% precision, 100.0% recall, 0.0% FP). `make calibrate` shows the screening-threshold tradeoff (88 is the point that rejects no namesakes yet catches every hit).

**Round-3 improvements (all done):**
1. **Registry verification** — new 7th check `checks/registry.py` + `backend/seed/company_registry.json`. Confirms the reg number exists/active/name-matches against a source outside the submission. Fabricated vendors (VS-11) fail `REGISTRY_NOT_FOUND` → PENDING_REVIEW; real ones get `REGISTRY_VERIFIED` (INFO). Has `set_registry_override()` for the eval. Legit fixture identities are seeded so only fabricated ones fail.
2. **Subtle name-fraud** — `consistency._added_entity_tokens` uses a **multiset** diff; a bank account named "<Company> Holdings/Group/Trading" now escalates even at high similarity. VS-10 covers it. (The multiset — not set — matters when the added word duplicates an existing token; the volume eval caught that at 96% and it was fixed.)
3. **Volume eval** — `scripts/eval_volume.py` (`make eval-volume`) generates labelled cases across 11 categories incl. plausible fraud; uses the pure `runner.assess()` (no DB/LLM). 
4. **Calibration** — `scripts/calibrate.py` (`make calibrate`) sweeps a threshold via `rules.set_common_override()`. Default sweep = screening.
5. **Override report** — `cases.override_report()` + `GET /v1/overrides` + a card in Queue. Flags where reviewers disagreed with the automated decision, tallied by check.
6. **Regex tests** — `tests/test_rules_regex.py`, per-country valid/invalid IDs.
7. **Queue aging** — `ageOf`/`ageDays` in api.ts; oldest-open stat + per-row age + stale (>7d) highlight.

**Key new invariants:** registry not-found never auto-approves; `assess()` is the pure decision core (use it for any batch scoring); the added-token check must stay a multiset diff.

## SIMPLIFICATION ROUND (current state) — read this first

The project was over-engineered; it has been **deliberately scoped back** to a
clean Zamp vendor-onboarding platform. **DELETED** (do not re-add): DAG/pipeline
config, outcomes dispatcher, SoR/lookup layer, `/v1/ingest` endpoints, AP/GTM
templates, multi-tenancy (`org_id`), `enterprise.py` (auth/metrics/middleware),
`scripts/calibrate.py`, `scripts/eval_volume.py`, `PRODUCTIZATION.md`,
`Plan-ConfigurableOnboarding.md`, tests for all of the above.

**Zamp adaptation added:**
- Branding: `APP_TITLE=Zamp`, `APP_SUBTITLE="Vendor Onboarding & Verification"` (config.py → /health → UI header).
- **India-first**: `rules/in.yaml` gains a `pan` block + `pan_card` document; `VendorSubmission.pan`; classifier recognises `pan_card`; default template for IN emits a PAN field verified against `pan_card.number`. Docs for IN: tax_form (GST), pan_card, incorporation, bank_proof.
- **AI confidence score** — `pipeline/confidence.py`. `compute()` builds an explainable score from document_read, document_classification, form_corroboration, certainty (weighted 0.25/0.15/0.4/0.2). `route()` then decides: high+clean→Auto Approve, disqualifier→Auto Reject, else Manual Review. **Invariant: confidence can only move a case TOWARDS a human, never away.** Threshold `AUTO_DECIDE_CONFIDENCE=0.85`.
- `assess()` now returns **4** values: `(status, findings, results, confidence_dict)`. Confidence persisted on `onboarding_case.confidence`.
- **Verification report** — `components/VerificationReport.tsx`, the default tab in CaseDetail: recommendation + confidence + component bars, form-vs-document field table (MATCH/MISMATCH/NO EVIDENCE), document acceptance list, mismatches + missing panels.
- **Form builder** — `ProfileBuilder.tsx` rewritten as click-to-build (add field rows, type dropdown, mandatory checkbox, dropdown options, optional regex; add documents with an `expects` description). No JSON editor.
- Nav: New vendor / Review queue / Templates / Rules.

**Gotchas learned:** (1) deleting `providers/lookup.py` left an import in `field_verification` that silently crashed the check → every case became PENDING_REVIEW; (2) my block-deletion surgery on `app.py` also removed the profile GET/PUT/DELETE routes (restored); (3) confidence double-counted `FIELD_UNEVIDENCED` (both corroboration and certainty) which bounced clean vendors — it is now excluded from `_AMBIGUITY_CODES`.

**Verified:** 127 tests pass, golden 11/11, `scripts/evaluate.py` 100% accuracy / 100% precision / 100% fraud recall / 0 FP, frontend builds, full-stack smoke (health, confidence, templates CRUD, vendor portal) all green.

---

**Generalization round (workflow-agnostic engine) — SUPERSEDED, see above. Most of this was deleted in the simplification round.**
- **Profile schema v2** (`profiles/models.py`): `ingestion_method` (form|webhook|email|api), `pipeline[]` (DAG node names), `entities[]` (EntitySpec — schema ontology alongside the typed core, NOT a replacement), `outcomes[]` (OutcomeSpec), and `evidence` → **`validation_source`** (legacy `evidence` key still loads via a model_validator; `.evidence` property kept for callers). Sources are namespaced: `doc.field` | `lookup:name.field` | `field:other`.
- **DAG pipeline** (`pipeline/runner.py`): `NODE_REGISTRY` + `DEFAULT_PIPELINE` + `resolve_pipeline(profile)` + `plan_for(sub)`. A profile picks/orders any subset; unknown names ignored. `CHECKS` kept as the default graph for existing callers. SSE `plan` event is now per-submission.
- **Outcomes** (`pipeline/outcomes.py`): terminal routing — `draft_email | webhook | slack | log | sor_write`, filtered by `on:[statuses]`. Dispatch failures are captured (never raise), persisted to `onboarding_case.outcomes` via `cases.record_outcomes`, shown in CaseDetail.
- **Systems of record** (`providers/lookup.py`): generic named-lookup layer. `SeedLookupProvider` reads `backend/seed/lookup_<name>.json` and **matches on identity VALUES across vocabularies** (CRM `account_name` ↔ our `legal_name`); `HttpLookupProvider` for real CRM/ERP via `LOOKUPS` env JSON. Wired into field_verification so a field can be verified against a CRM with no document at all.
- **Trigger-agnostic ingestion**: `POST /v1/ingest/{profile_id}` normalises three payload shapes (bare submission / wrapped `data|payload|record|fields` / inbound-email `from|subject|attachments`), unknown keys → `custom_fields`. Runs to completion and returns the case (no SSE — async callers want a result).
- **Templates**: `backend/seed/templates/{ap-invoice,gtm-outreach}.json`, served at `/v1/profile-templates`, loadable in the Profile Builder. AP = email ingestion, 5 nodes, amount_within vs PO ledger, sor_write outcome. GTM = webhook, 4 nodes, **zero documents**, CRM-verified fields.
- **`extends: "blank"` now honoured**: completeness skips ALL country vendor requirements (W-9/bank/directors) for non-onboarding workflows — was a real bug, an invoice was being asked for a bank mandate. `documents._accepted_kinds_for` reads the profile first, country pack as fallback.
- **New rule kind** `amount_within` (tolerance_pct) — the invoice-vs-PO shape, generalised.
- **Branding toggle**: `APP_TITLE`/`APP_SUBTITLE` env → `/health` → UI header. Defaults to "Vendor Onboarding" (PS-2 flagship); set `APP_TITLE="Process Validation Engine"` to rebrand with no code change.
- **Tests:** `tests/test_generalization.py` (18). Total **148**. Golden 11/11, volume eval 100/100/0 — evidence story intact.

**Configurable-onboarding round (plan: docs/Plan-ConfigurableOnboarding.md) — ALL BUILT:**
- **Requirement Profiles** — `backend/app/profiles/` (models + store). A client declares fields (typed, with per-field `evidence` mapping), documents (`expects` description for custom kinds), and rules (declarative + semantic). Stored as JSON under `data/profiles/` (override: `VO_PROFILE_DIR`); "default" is synthesized from country packs (100% backward compatible; `extends: country_defaults` merges). CRUD: `/v1/profiles`. Versioned on save.
- **Evidence-first verification** — `checks/field_verification.py`: DVA validates docs first → admissible-evidence store → every mapped field gets CORROBORATED (INFO) / CONTRADICTED (NEEDS_REVIEW for id-like, ADVISORY for names — DVA owns name mismatches) / UNEVIDENCED (ADVISORY; the missing doc itself is already NEEDS_INFO elsewhere). Matrix rendered in CaseDetail. NEEDS_REVIEW doc verdicts stay admissible (their evidence IS the signal); NEEDS_INFO docs (wrong/irrelevant/unreadable) contribute nothing.
- **Custom validation** — `checks/custom_rules.py`: Tier 1 typed validators (number/date/email/iban/aba/select/url/phone/regex), Tier 2 declarative rules (field_match/equals/date_before/country_consistent), Tier 3 semantic asserts via LLM — **escalate-only**; offline → NO_MODEL → escalates at rule's on_fail (unevaluated control ≠ approval).
- **Vendor portal** — per-case `vendor_token` (secrets.token_urlsafe), `GET /v1/vendor/{token}` returns the structurally-safe view (`cases.vendor_view`: status in vendor language; items ONLY when PENDING_INFO; includes vendor's own submission for prefill). `POST /v1/vendor/{token}/resubmit` = full pipeline under the original tenant (supersedes + diffs). Auth middleware exempts `/v1/vendor/*`. UI route `#/vendor/<token>` renders a standalone VendorPortal shell (status, requested items, fix-and-resubmit via VendorForm with `initial` + `hideSamples`).
- **UI** — Intake gains a profile selector; VendorForm is profile-driven (docs + custom fields from `/v1/profiles/{id}?country=`); new **Profiles** tab = ProfileBuilder (JSON editor + template, save/version/delete); CaseDetail shows the vendor-portal link (copy button) + the verification matrix table.
- **Fixed en route:** doc-reader number extraction kept only the first token ("HRB 84721"→"HRB", false CONTRADICTED) — now multi-token regex; EXTRACTOR_VERSION bumped to read.v3 to invalidate cached reads.
- **Tests:** `tests/test_configurable.py` (13). Total **130**. Golden 11/11, volume eval 100/100/0 unchanged.
- **Pipeline is now 9 checks** (field_verification + custom_rules added) — anything asserting check count uses len(CHECKS).

**Gemini wired (REST, no SDK):** `llm/client.py::GeminiClient` calls the v1beta REST endpoint (`X-goog-api-key` header), default model `gemini-flash-latest`, overridable via `LLM_MODEL`. Vision path in `document_reader._extract_vision` has a Gemini branch (inline_data image). Enable: `LLM_PROVIDER=gemini` + `GEMINI_API_KEY` in `.env`; `DOC_EXTRACTOR=vision` for model-read documents. Any failure falls back to offline templates (verified). Still offline by default; only email+summary use the LLM, never the decision.

**Document Verification Agent (DVA) round:**
- `backend/app/dva/` — a real per-document verification agent (mirrors the GrabHack MAO DVA):
  - `classifier.py` — classifies a document by **content signals** (IBAN/account→bank, VAT/GST→tax, incorporation-no→registry, "work experience/skills/education"→resume/irrelevant), NOT heading keywords. Generalises to unseen layouts. Returns detected_type / irrelevant_as / confidence / reasons.
  - `agent.py` — `verify(doc, sub, accepted, ...)` → `DocumentVerdict`: read → classify (relevance) → cross-reference name/number → authenticity/currency (confidence, expiry). Emits findings. `DocStatus` = VERIFIED/NEEDS_INFO/NEEDS_REVIEW.
  - `preflight.py` — verify ONE uploaded file instantly (submission-time gate), returns {status, level ok/warn/error, message}.
- `checks/documents.py` is now a thin orchestrator over `dva.agent.verify`.
- `POST /v1/documents/preflight` (multipart: file + doc_type + country + legal_name) → instant verdict.
- UI: `VendorForm` calls preflight on file attach → inline green/amber/red flag ("This looks like a resume / CV, not a bank proof"). New `api.preflight`, `Preflight` type.
- **Gotcha:** provided/pasted documents (source="provided") and vision reads trust their own detected_type; only text_layer/OCR reads run the content classifier (else empty-text classify would wrongly flag pasted submissions — this broke the volume eval once, now fixed). Bank docs skip name+number cross-check (account holder / account number, not entity name/reg).
- Tests: `tests/test_dva.py` (10). Total **117**. Evals still 100%.

**Productization round (round 4) — all default-off / backward-compatible:**
- **Doc processing:** `document_reader.py` now has OpenCV image preprocessing (two-pass: plain OCR, then preprocessed only if the plain read is thin), read-caching by file hash (`data/.llm_cache/docreads/`), and a pluggable extractor — `DOC_EXTRACTOR=offline|vision`. Vision path implemented for Anthropic/OpenAI, falls back to OCR/offline with no key. `EXTRACTOR_VERSION` in the cache key.
- **Provider adapters:** `providers/registry_provider.py` (SeedRegistryProvider default + CompaniesHouseProvider real API, gated on `COMPANIES_HOUSE_API_KEY`, `REGISTRY_PROVIDER=companies_house`; `set_registry_override` moved here, re-exported from `checks/registry.py`) and `providers/screening_provider.py` (Seed + ComplyAdvantage stub). Checks call `get_*_provider()`.
- **Enterprise layer:** `backend/app/enterprise.py` — optional bearer auth (`API_TOKEN`), tenancy (`X-Org-Id` → `org_id` column, cases isolated per tenant, default 'demo'), upload validation (ext allowlist + `MAX_UPLOAD_MB`), `/metrics` (Prometheus text), `LOG_JSON=1` structured logs, timing middleware. All wired in `api/app.py`.
- **Docs:** `PRODUCTIZATION.md` — honest DONE/BUILD/NON-CODE enterprise map.
- **Tests:** `tests/test_productization.py` (12 tests). Total now **107**. Both evals + calibration still 100%.
- **Deps:** added `opencv-python-headless`; Dockerfile adds `libglib2.0-0`.

**Key gotchas:** tenancy defaults to 'demo' and `list_cases` returns all for 'demo' (so demo UI unaffected); auth is off unless `API_TOKEN` set; registry override still works via the provider module.

**Vendor form + real uploads (UI round):**
- `components/VendorForm.tsx` — real multi-section onboarding form (company / address / contact / directors with DOB+nationality / scheme-aware banking / document uploads driven by the country's required docs). Prefill-from-example fills text fields. Primary Intake tab; "Sample vendors" and "Paste JSON" remain.
- `POST /v1/cases/form/stream` (multipart) — saves uploaded files to `data/documents/uploads/<uid>/`, matches by filename to document entries, sets `path`, streams SSE. Uploaded docs are read for real by the same `document_reader`.
- `api.ts`: `readSSE()` extracted; `streamForm(submission, files, handlers)` added.
- Verified: a real uploaded PDF is parsed (text_layer 0.97) and cross-checked live. This is the "any new test case / any data verifiable" path.

---

## Prior state — after round 2

**58 tests, 9 samples, 6 checks.** `make eval` 100% on 9 golden.

**Second-round improvements added (all done):**
1. **Real document reading** — `checks/document_reader.py` opens each attachment (PDF text layer → OCR fallback for scans), extracts name/number/dates with a confidence, and detects the document type. Documents are rendered to real files by `scripts/render_documents.py` (called from `build_fixtures.py`). VS-07's bank letter is a `.png` scan → OCR path. Low-confidence reads and wrong document types now produce findings (`DOCUMENT_LOW_CONFIDENCE`, `DOCUMENT_TYPE_MISMATCH`). Pasted JSON with no file falls back to `extracted` at full confidence.
2. **Two-factor screening** — denied parties and `director_details` now carry DOB + nationality. A name hit is confirmed or cleared by the secondary identifier. VS-08 = exact-name namesake cleared by different DOB → APPROVED.
3. **Resubmission handling** — `entity_key` (reg/tax/name) links a vendor's attempts; a resubmission supersedes the prior case and stores a `change_summary` diff (resolved/new/remaining). VS-09 = corrected VS-02.
4. **Reviewer actions** — `case_action` append-only table; `record_action()` in cases.py; `POST /v1/cases/{id}/action` (approve/reject/request_info/resolve/reopen). New human-decided statuses `APPROVED_BY_REVIEWER`/`REJECTED_BY_REVIEWER`. UI: `components/ReviewerActions.tsx`.
5. **Eval harness** — `scripts/evaluate.py`, `make eval`. Labels in the script are ground truth.

---

## Prior state — initial build

**44 tests, 7 samples.** Backend, frontend, fixtures, docs done.

**What works right now:**
- Six checks run on every submission (completeness, formats, consistency, documents, screening, duplicates).
- Decision = max severity → one of APPROVED / PENDING_INFO / PENDING_REVIEW / REJECTED.
- Vendor email generated only for PENDING_INFO; reviewer summary for every case.
- Offline mode is default and needs no API key. Anthropic/OpenAI/Gemini pluggable via `.env`.
- FastAPI backend with SSE streaming on `:8001`; React UI on `:5174`.
- Append-only SQLite audit trail.

**How to run:**
```
make install && make seed
make api      # terminal 1  → :8001
make ui       # terminal 2  → :5174
make test     # 44 tests
make reset    # clear case history
```

**Environment gotcha:** SQLite uses `journal_mode=DELETE` (not WAL) so it works on synced/network folders. If the DB errors on a locked filesystem, set `VO_DB_PATH` to local disk in `.env`.

---

## Key facts to not re-derive

- **Ports:** backend 8001, frontend 5174 (offset from the PS-1 build on 8000/5173 so both run together).
- **The decision rule is one line:** `status = SEVERITY_TO_STATUS[max(f.severity for f in findings)]`. No scoring. Do not add scoring.
- **Disclosure gate lives in `pipeline/runner.py::build_vendor_items(findings, status)`** — two gates: status must be PENDING_INFO, and finding severity must be NEEDS_INFO. Both required.
- **Severity means WHO ACTS, not how bad:** NEEDS_INFO = vendor fixes; NEEDS_REVIEW = we judge; REJECT = terminal.
- **Banking details in fixtures are computed** (real IBAN mod-97 + ABA 3-7-1), not typed. Regenerate with `scripts/build_fixtures.py`. The Pinnacle "typo" IBAN is a genuine transposed-digit checksum failure.
- **Reference data (vendor master, denied parties) is JSON, read per-check, never in the DB.** Onboarding produces a case, it doesn't mutate the master.
- **Bank accounts stored as salted SHA-256 fingerprint,** not raw numbers. The shared-account check compares fingerprints.

## The 7 samples and what each proves

| File | Expected | Point |
|---|---|---|
| VS-01 Northwind (US) | APPROVED | happy path |
| VS-02 Brightline (GB) | PENDING_INFO | one email lists all gaps |
| VS-03 Kessler (DE) | PENDING_REVIEW | bank holder ≠ company (fraud signal), not disclosed |
| VS-04 Sundara (IN) | PENDING_REVIEW | one field → two severities; max wins |
| VS-05 Continental (US) | PENDING_REVIEW | shared bank account vs master; never auto-reject |
| VS-06 Volkov (SG) | REJECTED | denied party; no email even though a doc is missing |
| VS-07 Pinnacle (GB) | PENDING_INFO | IBAN typo → vendor, not reviewer |

## Bugs found & fixed during build (don't reintroduce)

1. **VS-06 originally generated a vendor email to a sanctioned party.** Fixed by gating email generation on `status == PENDING_INFO` in `build_vendor_items`. Now covered by `test_denied_party_rejects_and_suppresses_all_vendor_contact`.
2. **Certificates of incorporation were flagged "expired" under a blanket 12-month freshness rule.** Fixed: only doc types in `common.yaml::document_rules.freshness_required` (currently `bank_proof`) get an age limit; explicit `expiry_date` is always honoured. A CoI is a permanent record and never goes stale.
3. **German IBAN was 21 chars (invalid — DE needs 22).** Fixed the BBAN length in the fixture generator.

## Open limitations (documented, not bugs)

- Document text is supplied by fixtures, not OCR'd. Cross-referencing is real; extraction is stubbed.
- Denied-party list is a 4-entry stub matching on name only (→ near-matches escalate, don't reject).
- No reviewer actions in the UI (read-only queue).
- No resubmission history (corrected submission = new case).
- No registry verification (formats checked, existence not confirmed).
- A client that abandons the SSE stream mid-run leaves a case stuck in RUNNING (`make reset` clears it).

## File map (where things live)

- Decision + disclosure: `backend/app/pipeline/runner.py`
- The interesting checks: `backend/app/checks/consistency.py`, `duplicates.py`, `screening.py`
- Checksum algorithms: `backend/app/checks/formats.py`
- Severity/status/finding-code definitions: `backend/app/models.py`
- Country rules: `backend/app/rules/*.yaml`
- LLM boundary: `backend/app/llm/` (offline default)
- Tests: `tests/test_golden_cases.py`
- Interview prep + demo script: `DEMO.md`; design docs: `docs/`

---

## Change log

- **Initial build** — all phases 0–8 complete. 44 tests green. Two safety bugs and one fixture bug found and fixed (see above). Docs written (PRD, Architecture, Rules, Phases, Design, Memory).

<!-- Append new entries above this line as work continues.
     Format: date/session — what changed, what to know, tests affected. -->
