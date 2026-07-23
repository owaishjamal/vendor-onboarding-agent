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
