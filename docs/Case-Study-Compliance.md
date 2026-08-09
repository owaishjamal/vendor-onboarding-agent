# PS-2 Compliance & Readiness Audit

**Submission:** Zamp — Vendor Onboarding & Verification
**Problem statement:** PS-2 · Operations — *Vendor onboarding, from submission to approval*
**Audited:** 6 August 2026 · against the ASA Case Study candidate guide (2026)

This is a deliberately unflattering read of the build. Scores are justified with
evidence and are not rounded up. Where something is weak, it says so and says
what it would cost to fix.

---

## 1. Requirement compliance matrix

### 1.1 Explicit requirements from the problem statement

| # | Requirement (PS-2 text) | Status | Evidence |
|---|---|---|---|
| R1 | Takes **a vendor submission as input** | ✅ Full | Three real intake paths: the vendor form with file uploads (`POST /v1/cases/form/stream`), raw JSON paste, and the bundled samples. All go through one `VendorSubmission` model. |
| R2 | Produces **a clear status: approved, pending, or rejected** | ✅ Full | `Status` enum: `APPROVED`, `PENDING_INFO`, `PENDING_REVIEW`, `REJECTED`. "Pending" is deliberately split by *who is blocked* — the vendor or us — because that is the single most useful distinction to a procurement team. |
| R3 | **Reasoning visible** | ✅ Full | Every status derives from typed `Finding` objects with a code, severity, field path, human message and evidence. The UI shows all 9 checks, per-check findings, and the field-vs-document matrix. Nothing is a black box. |
| R4 | For anything not approved, **communicate back what's needed** | ✅ Full | `build_vendor_items()` turns only `NEEDS_INFO` findings into vendor-facing text; a vendor email is drafted and a tokened vendor portal (`/v1/vendor/{token}`) lists the exact items. Disclosure is gated: only `PENDING_INFO` cases generate vendor-facing content. |
| R5 | **You decide** what the submission looks like, what rules apply, output structure | ✅ Full | Documented in `docs/Rules.md` and `docs/PRD.md`; requirement profiles make the submission shape configurable per client rather than hardcoded. |
| R6 | **2–4 edge cases**, non-trivial, handled deliberately | ✅ Exceeds | 11 labelled cases, 6 of which are genuine edge cases (see §4). Each is a *different class* of failure, not a variation of one. |

### 1.2 Deliverable requirements from the candidate guide

| # | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | A process that **actually executes**, not a mockup | ✅ Full | Real PDF parsing, OCR fallback, IBAN mod-97 and ABA checksums, fuzzy name matching, registry and denied-party lookups. Verified live this session: 11/11 labelled cases, 127 tests. |
| D2 | Accepts **real inputs** (PDF, form submission) | ✅ Full | Multipart upload with real files written to disk and read by the document agent. Verified: a resume uploaded into the `bank_proof` slot is caught pre-submission. |
| D3 | **Intuitive, well-designed interface** | 🟡 Strong, not flawless | Six views, consistent design language, empty/loading/error states, `ErrorBoundary`. Not audited for WCAG; no keyboard-navigation pass; not tested below 375px. |
| D4 | **Live run view** showing each stage as it executes | ✅ Full | SSE streams a `check` event per stage; `CheckTimeline.tsx` renders queued → running → done with per-stage duration and findings expanding inline. Verified: 9 stages streamed in order. |
| D5 | **Dashboard** showing history, status and outputs across runs | ✅ Full | `Queue.tsx`: KPI strip (submissions, auto-approved, awaiting review, oldest open), status filters with counts, per-case top finding, severity counts, staleness flag, reviewer-override analytics, most-common blocking findings. |
| D6 | **Live and runnable link** | 🟡 Ready, not yet deployed | Dockerfile (multi-stage, single URL) and `render.yaml` are correct and now reference the right env vars. `SEED_DEMO_CASES=1` guarantees a populated dashboard on a cold start. **You still have to actually deploy it and paste the URL into the submission.** |
| D7 | 5-minute demo video | ⬜ Not started | Script in §5. |

### 1.3 Implicit expectations

| # | Implicit expectation | Status | Note |
|---|---|---|---|
| I1 | "Handles real inputs **end-to-end**" — no manual steps | ✅ Full | Submission → decision → vendor email is one uninterrupted run. |
| I2 | "Deals with edge cases **gracefully**" — degrades, doesn't crash | ✅ Full | A crashing check becomes a `NEEDS_REVIEW` finding rather than failing the run: *an unevaluated control is never grounds for approval*. Verified by design and by test. |
| I3 | Explainable to **a non-technical buyer** | ✅ Full | Every finding carries a plain-English message; the reviewer summary is prose; the decision reason states the confidence and threshold in words. |
| I4 | Audit trail (PS-2 calls out "the only audit trail is someone's inbox") | ✅ Full | Append-only case + check + finding tables; reviewer resolutions captured with actor and timestamp. |
| I5 | Judgment about **what not to build** | ✅ Full | An earlier over-engineered round (SoR lookups, outcome dispatcher, DAG engine, enterprise module) was deliberately deleted. Worth saying out loud in the interview — it reads as senior. |

**Explicit requirements: 6/6 full. Deliverables: 5/7 full, 2 pending your action.**

---

## 2. What was broken, and is now fixed

Found and fixed during this audit:

1. **Every document upload returned HTTP 500.** `documents_preflight` called `enterprise.validate_upload` — a module deleted in the simplification round. The preflight feature, one of the more impressive things to demo, was completely dead. Now calls `_validate_upload`. *Verified: a resume in the bank-proof slot is now correctly rejected before submission.*
2. **Zombie cases stuck in `RUNNING` forever.** A client disconnect (closed tab, dropped wifi) raises `GeneratorExit`, which derives from `BaseException` and so was never caught by `except Exception`. The case row stayed `RUNNING` permanently and the queue slowly filled with rows nobody could action. Now recorded as interrupted with a re-submit prompt. *This would have been visible on your dashboard during a live demo.*
3. **Test pollution into the real data directory.** `PROFILE_DIR` was resolved at import time, so pytest's module import order decided whether the env override applied. Four junk profiles (`t-acme`, `t-rule`, `t-sem`, `t-typed`) were sitting in `data/profiles/` and would have appeared in the Templates dropdown mid-demo. Directory is now resolved per call; junk deleted.
4. **A "Grab Food Merchants" template** in a Zamp submission. Replaced with a `Logistics & freight vendors` template that fits the AP-vendor narrative.
5. **Deploy config pointed at the wrong LLM.** Dockerfile and `render.yaml` documented `ANTHROPIC_API_KEY` while the client uses Gemini.
6. **Empty dashboard on a cold deploy.** Render's free tier has an ephemeral filesystem, so a redeploy wiped all history — an interviewer opening your link days later would see nothing. `SEED_DEMO_CASES=1` now re-runs the labelled submissions into an empty database only.

---

## 3. Remaining weaknesses (honest)

| Severity | Weakness | Why it matters | Cost to fix |
|---|---|---|---|
| **Critical** | **Not deployed yet.** | D6 is half the submission. A local-only build cannot be graded. | 30 min |
| **Critical** | **No demo video.** | Explicit deliverable. | 1 hour |
| **High** | **No authentication on the reviewer UI.** Anyone with the URL can approve vendors, read every case, and edit rules. Only the vendor portal is credentialed (per-case token). | For a hackathon demo this is defensible; for the "enterprise-grade" claim it is the single biggest hole. Say it out loud before they find it. | 3–4 h for basic auth + roles |
| **High** | **Single-process, synchronous pipeline.** The run happens inside the HTTP request; there is no queue or worker. Throughput is bounded by one process, and a restart mid-run loses the run. | Fine at "dozens of vendors per quarter" (the PS's own volume). Breaks at thousands. | 1–2 days for a task queue |
| **Medium** | **SQLite, single node.** No connection pooling, no replication, no backup. | Correct choice for the assignment; a real deployment needs Postgres. The storage layer is isolated enough to swap. | 1 day |
| **Medium** | **n=11 evaluation set.** 100% precision/recall on eleven hand-built cases is a design check, not a statistical claim. | Do **not** present it as a model accuracy number — a sharp interviewer will call it out. Present it as a regression suite. | — |
| **Medium** | **No CI.** No `.github/workflows`. Tests exist but nothing runs them on push. | Cheap credibility. | 20 min |
| **Medium** | **No rate limiting or upload virus scanning.** Unbounded uploads by extension/size only. | Abuse vector on a public URL. | 2 h |
| **Low** | **Accessibility unverified.** No axe pass, no keyboard-nav audit, colour contrast unchecked. | You claim a designed UI; nobody will check this in an interview, but the claim is unproven. | 3 h |
| **Low** | **Registry and denied-party data are seeded fixtures**, not live feeds. | Honest in the docs already. Be upfront: the *adapter seam* exists, the data is synthetic. | — |

---

## 4. The edge cases — and why each one is non-trivial

The guide says *"the edge cases you choose tell us how well you understand the problem."* These are chosen so that each one breaks a **different** assumption. Six of the eleven are genuine edge cases:

| Case | The trap | Why it is not trivial |
|---|---|---|
| **VS-03** Kessler | Every field is individually valid; the bank account holder is a person, not the company. | No single-field validator can catch it. Only cross-referencing form ↔ document ↔ form finds it. This is the PS's own example: *"the name on the submission doesn't match the name on the bank account."* |
| **VS-05** Continental | A clean submission whose bank account already belongs to a different vendor. | Requires state across submissions — you cannot detect it by looking at one form. This is the classic payment-fraud pattern. |
| **VS-08** Meridian | Director's name matches a sanctions entry **exactly** — but DOB and nationality clear him. | Tests that screening is two-factor, not a name-substring match. A naive system rejects a legitimate vendor. **The interesting demo: the system does *not* flag it.** |
| **VS-10** Harbourstone | Real, verified company; bank account held by "*\<Company\> Holdings*". | Fuzzy name matching says "close enough". A set-vs-multiset token diff catches the added entity token. This one nearly slipped through in an earlier volume eval — worth telling that story. |
| **VS-11** Ashcroft | Internally flawless in every respect; the registration number exists in no registry. | Consistency checking alone approves it. Only an external lookup catches a wholly fabricated company. |
| **VS-07** Pinnacle | Everything correct; the IBAN has two transposed digits. | Format-valid but checksum-invalid — a vendor typo, not fraud, so it routes to the **vendor** (`PENDING_INFO`), not to compliance. Shows the routing distinction. |

**The judgment to emphasise:** VS-08 and VS-07 demonstrate the system is tuned against *false positives*, not just against fraud. Anyone can build something that rejects everything suspicious. The hard part is not wasting a reviewer's time.

---

## 5. Demo script

### 5.1 Five-minute video

| Time | Beat | What to say |
|---|---|---|
| 0:00–0:30 | **The problem** | "A procurement team onboards dozens of vendors a quarter. Review is manual, follow-up is manual, and the audit trail is someone's inbox. A vendor with inconsistent details slipping through means payment fraud." |
| 0:30–1:45 | **Happy path — VS-01 Northwind** | Fill the form, attach the documents. Show the **preflight chip** confirming each document as it attaches. Submit. Let all 9 stages stream. Land on APPROVED, 88% confidence, auto-approved above the 85% threshold. |
| 1:45–3:15 | **Edge case 1 — VS-03 Kessler** | "Every field here is individually valid." Run it. Stop on *Form vs document comparison*. "The bank account holder is a person, not the company. No single-field check finds this." → PENDING_REVIEW, routed to a human, **no vendor email sent** — we don't tell a suspected fraudster what tripped us. |
| 3:15–4:15 | **Edge case 2 — VS-02 Brightline** | Missing VAT number and bank proof. → PENDING_INFO. Open the **drafted vendor email** and the **vendor portal** — plain English, exactly what's needed, no internal vocabulary. "This is the follow-up that used to be manual." |
| 4:15–5:00 | **The dashboard + the point** | Show the queue: statuses, oldest open case, most common blocking findings, reviewer overrides. "72% of these needed a human. The three that didn't were auto-approved with zero false approvals across the labelled set." |

**Cut if short on time:** the Templates builder and the Rules view. They are good, but they are not what PS-2 asks for.

### 5.2 Live interview run order

1. **VS-01** — happy path. Warm up, prove it runs.
2. **VS-08 Meridian** — *lead with this one.* Exact sanctions name match that the system correctly clears on DOB + nationality. It is counter-intuitive and it shows judgment.
3. **VS-03 Kessler** — the cross-reference catch.
4. **Upload a resume as a bank proof** — live, unrehearsed-looking. Preflight catches it instantly. This is the moment that proves it isn't scripted.
5. Have **VS-11 Ashcroft** in reserve if they ask "what if someone just makes a company up?"

**Rehearse the failure:** if the deployed instance is cold (Render free tier sleeps ~50s), open the link *before* the call starts.

---

## 6. Scores

Scored against *"could this realistically compete with a commercial product"*, not against *"is this good for a hackathon"*. On the hackathon bar every number would be 15–20 points higher.

| Dimension | Score | Justification |
|---|---:|---|
| **PS-2 requirement compliance** | **96** | All six explicit requirements fully met; the output contract, the reasoning visibility and the vendor communication loop are exactly what was asked. −4 because the live link is not yet up, and that is an explicit deliverable. |
| **Production readiness** | **62** | Runs reliably, degrades gracefully, has an audit trail, containerised, health check, 127 passing tests. But: no auth, no CI, no backups, no rate limiting, single node. It would survive a demo and a pilot; it would not survive a security review. |
| **Enterprise readiness** | **55** | Strong on the things enterprises actually ask about in procurement software — auditability, explainability, configurability per client, disclosure control. Fails the basics of access control, SSO, RBAC, data retention policy and tenancy. |
| **Scalability** | **48** | Synchronous in-request pipeline, SQLite, single process, no queue. Adequate for the volume the PS describes (dozens/quarter) and honestly scoped as such — but "enterprise-grade" implies headroom this does not have. The storage and provider seams are clean, so the path exists. |
| **Security** | **42** | Lowest score and correctly so. No authentication on the reviewer surface at all. Mitigating: the vendor portal is token-scoped, disclosure gating is enforced structurally, uploads are extension/size validated, secrets are env-only and gitignored, no secret has ever been hardcoded. But an unauthenticated approve button is disqualifying for the enterprise claim. |
| **UI / UX** | **80** | Genuinely well-considered: the live run view, the field-vs-document matrix, the "who is blocked" status split, empty and error states, an ErrorBoundary. −20 for unverified accessibility, no mobile pass, and no usability testing with an actual procurement person. |
| **Maintainability** | **84** | Small, well-named modules; checks are independent and uniformly shaped; rules are YAML data not code; comments explain *why*; 127 tests including regression tests for specific past bugs. −16 for no CI and a few functions that have grown long. |
| **AI quality** | **72** | The important architectural decision is right: **the LLM never makes the decision.** It writes the vendor email and the reviewer summary; verification is deterministic and testable. Confidence is explainable with weighted components, and can only ever move a case *towards* a human. −28 because prompt regression tests are thin, there is no hallucination check on generated vendor emails, and the offline composer means the LLM path is not exercised in CI. |
| **Innovation** | **70** | The evidence-first ordering (validate the document, *then* verify fields against it), the disclosure gate, and confidence-driven routing with a one-way safety invariant are genuinely thoughtful. Not novel research — but well-judged product engineering. |
| **Overall** | **74** | A strong, honest, well-reasoned build that answers the actual question asked, with real edge-case judgment. Held back from the 80s by deployment, authentication, and the absence of CI. |

### If you only do three more things

1. **Deploy it and get the URL** (30 min) — without it, D6 fails outright.
2. **Record the video** (1 h) — explicit deliverable; use §5.1.
3. **Add basic auth to the reviewer UI** (30 min for a shared-secret header, 3 h done properly) — or, if you skip it, *say so first* in the interview: "there's no auth on the reviewer surface; here's exactly how I'd add it." Naming your own gap before the panel does converts a weakness into evidence of judgment.

---

## 7. Assumptions and limitations to state out loud

State these in the video or the interview. Unstated limitations look like blind spots; stated ones look like judgment.

- Registry and denied-party data are **seeded fixtures**, not live feeds. The adapter interface exists; the data is synthetic.
- The **85% auto-approve threshold** is a calibrated product decision, not a learned parameter. It is deliberately conservative: confidence can only ever route a case *towards* a human, never away.
- The evaluation set is **11 hand-built cases** — a regression suite, not a statistical accuracy claim.
- **The LLM does not decide anything.** Swapping it for a different model changes only the prose. This is a deliberate reliability choice.
- Vendor emails are **drafted, not sent**. Wiring SMTP is a 30-minute change; a human approving outbound vendor communication is the safer default.
