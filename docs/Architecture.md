# Architecture — Vendor Onboarding

How the system is put together, and why.

---

## 1. Technical stack

| Layer | Choice | Why |
|---|---|---|
| Backend language | Python 3.10+ | Fast to write validation logic; good library ecosystem. |
| API framework | FastAPI | Async, typed, first-class streaming responses (SSE). |
| Validation model | Pydantic v2 | The submission and every result are typed models; parsing is validation. |
| Fuzzy matching | RapidFuzz | Entity-name comparison and denied-party screening. |
| Rules | PyYAML | Country rule packs are data, not code. |
| Storage | SQLite (stdlib `sqlite3`) | Zero-setup, file-based, append-only tables. |
| Frontend | React 18 + TypeScript + Vite | Component model suits the live check view; instant HMR. |
| Styling | Tailwind CSS 3 | Utility classes; the whole palette is themeable from config. |
| LLM (optional) | Anthropic / OpenAI / Gemini, or offline | Provider-agnostic; the two generated documents only. |

No state manager, no component library, no ORM. The app is small enough that adding them would cost more than it saves.

## 2. The two decisions the architecture is built around

### 2.1 No check stops early
Unlike an invoice pipeline (which short-circuits at the first decisive failure), every check runs on every submission — even after a `REJECT` is certain. This is why the runner is a simple loop that always completes, and why the decision is computed *after* all checks, not during.

### 2.2 Status is a pure function of severity
```
status = SEVERITY_TO_STATUS[ max(finding.severity for finding in all_findings) ]
```
There is no scoring engine, no weights, no per-check veto logic. Each check assigns a severity to each finding it raises; the runner takes the maximum. This keeps the decision reproducible and trivially auditable — the reason for any status is "the most severe finding present."

## 3. Folder structure

```
vendor-onboarding-agent/
├── backend/
│   ├── app/
│   │   ├── models.py          # Severity, Status, FindingCode (closed enums),
│   │   │                      #   Finding, CheckResult, VendorSubmission, CaseRecord
│   │   ├── config.py          # paths, LLM provider, the few global settings
│   │   │
│   │   ├── rules/             # DATA, not code
│   │   │   ├── __init__.py    #   YAML loader (lru-cached)
│   │   │   ├── common.yaml    #   name-match bands, screening thresholds, freshness
│   │   │   ├── us.yaml gb.yaml de.yaml in.yaml sg.yaml
│   │   │
│   │   ├── checks/            # one module per check, all pure functions
│   │   │   ├── base.py        #   name normalisation + fuzzy scoring helpers
│   │   │   ├── completeness.py
│   │   │   ├── formats.py     #   regex + IBAN mod-97 + ABA 3-7-1 checksum
│   │   │   ├── consistency.py #   the cross-field checks (the heart of PS-2)
│   │   │   ├── documents.py
│   │   │   ├── screening.py
│   │   │   └── duplicates.py
│   │   │
│   │   ├── llm/
│   │   │   ├── prompts.py     #   two prompts, versioned
│   │   │   ├── offline.py     #   deterministic template composer (no key)
│   │   │   └── client.py      #   provider-agnostic interface + cache
│   │   │
│   │   ├── pipeline/
│   │   │   └── runner.py      #   runs all checks, aggregates, enforces disclosure
│   │   │
│   │   ├── storage/
│   │   │   ├── db.py          #   schema; checks + findings are append-only
│   │   │   └── cases.py       #   persistence + queue/stats queries
│   │   │
│   │   └── api/
│   │       └── app.py         #   FastAPI routes + SSE streaming
│   │
│   └── seed/
│       ├── vendor_master.json     # existing vendors (for duplicate detection)
│       └── denied_parties.json    # screening list
│
├── frontend/
│   └── src/
│       ├── api.ts             # typed client + SSE reader + presentation maps
│       ├── App.tsx            # tab shell
│       ├── components/
│       │   ├── Badges.tsx         # status + severity chips, check icons
│       │   ├── CheckTimeline.tsx  # the live check view
│       │   └── FindingCard.tsx    # one finding, with disclosure labelling
│       └── views/
│           ├── Intake.tsx         # submit + watch checks resolve
│           ├── Queue.tsx          # reviewer queue + stats
│           ├── CaseDetail.tsx     # findings grouped by who acts
│           └── Rules.tsx          # rule packs + reference data
│
├── scripts/build_fixtures.py  # generates reference data + 7 submissions
├── tests/test_golden_cases.py # 44 tests: outcomes + checksum algorithms
├── docs/                      # these documents
├── Makefile · README.md · DEMO.md · .env.example
```

## 4. Request flow

```
Browser (Intake)
   │  POST /v1/cases/stream   { submission JSON }
   ▼
FastAPI app.py
   │  VendorSubmission(**payload)          ← Pydantic validates the shape
   │  StreamingResponse(text/event-stream)
   ▼
pipeline/runner.py  run_pipeline()
   │  create_case()                        ← row written, status RUNNING
   │
   │  for each of the 6 checks:            ← NO early exit
   │      result = check.run(submission)
   │      append_check()                   ← append-only
   │      yield {"type":"check", ...}  ────┼──► SSE frame → browser renders the step
   │
   │  status = decide(all_findings)        ← max severity
   │  vendor_items = build_vendor_items(findings, status)   ← disclosure gate
   │  email   = llm.draft_vendor_email(payload)    ← PENDING_INFO only
   │  summary = llm.reviewer_summary(payload)
   │  complete_case()                      ← findings written, status set
   │  yield {"type":"done", "case": ...} ──┼──► SSE frame → browser renders verdict
   ▼
storage/cases.py → SQLite (cases.db)
```

The frontend reads the SSE stream frame by frame off the `fetch` body (EventSource can't POST a JSON body), so each check appears the instant it completes rather than when the whole response lands.

## 5. Data model (the important types)

```
Severity        (IntEnum, ordered)  INFO < ADVISORY < NEEDS_INFO < NEEDS_REVIEW < REJECT
Status          (str Enum)          APPROVED · PENDING_INFO · PENDING_REVIEW · REJECTED
FindingCode     (str Enum)          closed vocabulary, ~25 codes

Finding         code, severity, check, field, message,
                vendor_message?,  evidence{}
                    ── message is internal/reviewer-facing
                    ── vendor_message exists ONLY for NEEDS_INFO findings

CheckResult     check, label, findings[], summary, duration_ms, data{}

VendorSubmission  legal_name, country, tax_id, registration_number,
                  address*, contact*, directors[], bank{...}, documents[...]

CaseRecord      case_id, status, findings[], reviewer_summary,
                vendor_email?, checks[], timestamps
```

Two invariants encoded in the types:
- `SEVERITY_TO_STATUS` is the single mapping from severity to status. Nothing else decides status.
- `vendor_message` is a separate field from `message`. The internal note can describe a fraud concern; the vendor-facing text never does. Only `NEEDS_INFO` findings carry it.

## 6. Reference data flow

The vendor master and denied-party list are read from JSON on each relevant check, not loaded into the database. Unlike an invoice pipeline, an onboarding decision does **not** mutate reference data — it produces a case, not a change to the master file. Promoting an approved vendor onto the master is a separate, human-authorised step (out of scope here). This is why the DB holds only cases, and reference data stays as flat files the seed script owns.

Bank accounts in the master are stored as a **salted fingerprint** (SHA-256 of the normalised IBAN or routing:account), not raw numbers, so the shared-account check can detect collisions without spreading account data through storage or logs.

## 7. LLM boundary

Two calls, both after the decision is final:
- `draft_vendor_email(payload)` — only invoked when there are vendor items (i.e. `PENDING_INFO`).
- `reviewer_summary(payload)` — for every case.

The provider is chosen by `LLM_PROVIDER`. `offline` composes both documents from templates using the same structured findings a model would receive — which is why offline output closely resembles model output. Any provider failure (missing SDK, bad key, unparseable response, rate limit) is caught and falls back to offline, so a run can never fail on the model.

Generated text is cached by a content hash, so a rehearsed demo never re-hits the API.

## 8. Storage schema

```
onboarding_case   case_id (pk), legal_name, country, status,
                  reviewer_summary, vendor_email, submission(json), timestamps
case_check        case_id, seq, check_name, label, summary, data(json), duration_ms
                      ── APPEND ONLY
case_finding      case_id, code, severity, severity_name, check_name,
                  field, message, vendor_message, evidence(json)
                      ── APPEND ONLY
```

`journal_mode=DELETE` rather than WAL, because WAL needs shared-memory mapping that isn't available on network/synced/FUSE-mounted filesystems (OneDrive, mapped drives, bind mounts). Write volume is trivial, so the safer journal mode costs nothing.

## 9. Ports

Backend `8001`, frontend `5174` — deliberately offset from the PS-1 build (`8000` / `5173`) so both projects can run at once.

## 10. Extension points

- **New country** → add `rules/<cc>.yaml`. No code change.
- **New check** → add `checks/<name>.py` exposing `run(submission) -> CheckResult`, and register it in `runner.CHECKS`. Its findings' severities automatically feed the decision.
- **New finding type** → add a value to `FindingCode` and text to the reason map.
- **Real OCR** → replace the fixture `extracted` blocks with a document-parsing step upstream of the `documents` check; the cross-referencing logic is unchanged.
- **Real screening provider** → swap the JSON list in `screening.py` for an API call; the two-band threshold logic stays.
