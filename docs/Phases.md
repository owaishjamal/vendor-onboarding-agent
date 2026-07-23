# Phases — build plan

The project broken into phases small enough to build and verify one at a time. Each phase ends in something runnable with a clear acceptance check, so progress is never "80% built and can't run." This mirrors the order the system was actually built.

All phases are **complete** — this document records the plan and doubles as a re-build guide.

---

## Phase 0 — Foundations · ✅

Scaffold and the type system everything else depends on.

- Folder structure, `config.py`, `.env.example`, `Makefile`.
- `models.py`: `Severity` (ordered), `Status`, `FindingCode` (closed enum), `Finding`, `CheckResult`, `VendorSubmission`, `CaseRecord`, and the `SEVERITY_TO_STATUS` mapping.

**Acceptance:** models import; `SEVERITY_TO_STATUS` covers every severity; `VendorSubmission` parses a hand-written JSON blob.

---

## Phase 1 — Rules as data · ✅

Country rule packs and the loader.

- `rules/common.yaml` (name-match bands, screening thresholds, document freshness).
- `rules/{us,gb,de,in,sg}.yaml` (tax ID + registration regex, payment scheme, required documents).
- `rules/__init__.py`: cached loader, `supported_countries()`, `required_documents()`.

**Acceptance:** every pack parses; `supported_countries()` returns the five codes; a bad country raises cleanly.

---

## Phase 2 — The checks · ✅

Six independent pure functions. Built completeness → format → consistency → documents → screening → duplicates, verifying each against a hand-made submission before moving on.

- `checks/base.py` — name normalisation + fuzzy scoring + verdict bands.
- `checks/formats.py` — regex per country **plus the real algorithms**: IBAN mod-97 (ISO 13616), ABA 3-7-1 checksum.
- `checks/consistency.py` — the cross-field checks (the core of PS-2).
- `checks/completeness.py`, `documents.py`, `screening.py`, `duplicates.py`.

**Acceptance:** each check returns a `CheckResult` with correctly-severitied findings on a crafted bad submission and none on a clean one. IBAN/ABA validators pass canonical valid values and reject transposed-digit values.

---

## Phase 3 — Aggregation, decision, communication · ✅

The runner that turns six check results into one decision and two documents.

- `pipeline/runner.py` — runs all six (no early exit), `decide()` = max severity, `build_vendor_items()` with the two disclosure gates.
- `llm/prompts.py`, `llm/offline.py`, `llm/client.py` — provider-agnostic, offline fallback, cache.

**Acceptance:** a clean submission → `APPROVED`, no email; an incomplete one → `PENDING_INFO` with an email listing every gap; a rejected one → `REJECTED` with no email even when a vendor-fixable finding exists.

---

## Phase 4 — Persistence · ✅

Append-only storage and queue queries.

- `storage/db.py` — schema, `DELETE` journal mode, `reset_db()`.
- `storage/cases.py` — `create_case`, `append_check`, `complete_case`, `get_case`, `list_cases`, `stats`.

**Acceptance:** a run writes one case row, six check rows, N finding rows; `list_cases` returns them newest-first with finding counts; `reset_db` clears cleanly.

---

## Phase 5 — API · ✅

FastAPI surface with SSE streaming.

- `api/app.py` — `/v1/cases/stream`, `/v1/cases/sample/{name}/stream`, `/v1/cases`, `/v1/cases/{id}`, `/v1/stats`, `/v1/countries`, `/v1/policy`, reference endpoints, `/v1/reset`, `/health`.

**Acceptance:** `curl` the SSE endpoint and see `plan` → six `check` frames → one `done` frame; every sample returns its expected status.

---

## Phase 6 — Test data · ✅

Seven submissions with **computed** banking details.

- `scripts/build_fixtures.py` — vendor master, denied-party list, and the seven submissions. IBANs/ABAs generated with the real algorithms; the one broken IBAN is a genuine transposed-digit checksum failure.

**Acceptance:** all seven files generate; the seed script's self-asserts pass (valid IBANs validate, the typo IBAN fails mod-97, ABA checksums are divisible by 10).

The seven, and what each proves:

| # | Vendor | Tests | Expected |
|---|---|---|---|
| 01 | Northwind (US) | happy path | `APPROVED` |
| 02 | Brightline (GB) | one email, many gaps | `PENDING_INFO` |
| 03 | Kessler (DE) | bank-holder ≠ company (fraud signal) | `PENDING_REVIEW` |
| 04 | Sundara (IN) | one field → two severities | `PENDING_REVIEW` |
| 05 | Continental (US) | shared account vs master | `PENDING_REVIEW` |
| 06 | Volkov (SG) | denied party + disclosure suppression | `REJECTED` |
| 07 | Pinnacle (GB) | IBAN typo routed to vendor | `PENDING_INFO` |

---

## Phase 7 — UI · ✅

React app: intake, live check view, review queue, case detail, rules.

- `Intake.tsx` (submit + watch), `CheckTimeline.tsx` (live), `FindingCard.tsx` (with disclosure labelling), `Queue.tsx` (ordered, filtered), `CaseDetail.tsx` (findings grouped by who acts), `Rules.tsx` (packs + reference data).

**Acceptance:** `tsc` clean, `vite build` succeeds; running a sample streams each check into the timeline and shows the verdict, the drafted email (or the suppression banner), and the grouped findings.

---

## Phase 8 — Verification · ✅

Lock the behaviour down.

- `tests/test_golden_cases.py` — 44 tests: every sample's outcome, the disclosure rule directly, the severity-max aggregation, and unit tests for the IBAN and ABA algorithms and the name-matching bands.

**Acceptance:** `make test` green (44 passed); full-stack smoke via `curl` matches expectations.

---

## Improvement round — Phases 9–13 · ✅

Built after a critique pass. All complete; 58 tests, `make eval` clean.

- **Phase 9 — Real document reading.** `checks/document_reader.py` opens each attachment (PDF text layer → OCR fallback), extracts fields with confidence, detects type. Documents rendered to real files by `scripts/render_documents.py`. Low-confidence and wrong-type now produce findings. **Acceptance:** VS-07's scanned bank letter reads via OCR; VS-03's "K. Weber" is read off the real file; golden outcomes unchanged.

- **Phase 10 — Two-factor screening.** DOB + nationality on denied parties and `director_details`; a name hit is confirmed or cleared by the second factor. **Acceptance:** VS-08 exact-name namesake clears to APPROVED; VS-06 stays REJECTED, confirmed on DOB.

- **Phase 11 — Resubmission handling.** `entity_key` links attempts; a resubmission supersedes the prior case with a resolved/new/remaining diff. **Acceptance:** VS-09 supersedes VS-02, 2/2 resolved; prior marked superseded.

- **Phase 12 — Reviewer actions.** Append-only `case_action` log; approve/reject/request-info/resolve/reopen via API and UI; human-decided statuses sit alongside the automated trail. **Acceptance:** approving a pending case logs who/when/note and moves it to APPROVED_BY_REVIEWER.

- **Phase 13 — Eval harness.** `scripts/evaluate.py` / `make eval` reports status accuracy, auto-approve precision, fraud recall, false-positive flags. **Acceptance:** 100% / 100% / 100% / 0.

## Improvement round 2 — Phases 14–20 · ✅

Built after a second critique pass. 95 tests; two eval harnesses clean.

- **Phase 14 — Registry verification.** 7th check confirming existence/active/name against an external source; a fabricated-but-consistent vendor (VS-11) fails it. **Acceptance:** VS-11 → PENDING_REVIEW on REGISTRY_NOT_FOUND; VS-01 → REGISTRY_VERIFIED.
- **Phase 15 — Subtle name-fraud.** Multiset added-token detection; "<Company> Holdings" (VS-10) escalates despite high similarity. **Acceptance:** VS-10 → PENDING_REVIEW; duplicate-token case handled.
- **Phase 16 — Volume eval.** 250 generated labelled cases incl. plausible fraud via pure `assess()`. **Acceptance:** 100% precision, 100% recall, 0% FP; found & fixed a real 96% miss.
- **Phase 17 — Calibration.** Threshold sweep with in-memory override. **Acceptance:** screening curve shows namesakes wrongly rejected below 88, clean at 88+.
- **Phase 18 — Override report.** Disagreement tally by check, endpoint + UI card. **Acceptance:** approving a held case registers one override on the consistency check.
- **Phase 19 — Regex validation.** Per-country real-world valid/invalid IDs. **Acceptance:** 27 cases pass.
- **Phase 20 — Queue aging.** Age per row, oldest-open stat, stale highlight.

## Still not built (interview roadmap)

- **Live registry & screening feeds** — swap the seeded files for Companies House / Handelsregister / a licensed screening API. The adapters change; the check logic doesn't.
- **Real side effects on reviewer actions** — actually send the vendor email / write to an ERP.
- **Layout-robust document OCR** — pair the reader with a vision model for arbitrary real documents.
