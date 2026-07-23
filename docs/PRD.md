# PRD — Vendor Onboarding

Product Requirements Document · PS-2 · Zamp AI Solutions Associate case study

---

## 1. Problem

Before a company can pay a supplier, someone has to verify the supplier is legitimate: collect company details, banking information, tax registration and compliance documents, then confirm the whole submission is complete, consistent and credible.

In practice this is slow and error-prone:

- Vendors submit incomplete forms and attach the wrong documents.
- Details contradict each other in ways only visible when fields are cross-referenced — the name on the bank account differs from the legal name, the tax ID is in the wrong format for the claimed country.
- When something is missing, a human tracks down the vendor, explains what's needed, and waits.
- The review is manual, the follow-up is manual, and the only audit trail is whatever is in someone's inbox.

A bad onboarding — a vendor who slips through with inconsistent details — causes payment fraud, compliance breaches, or both.

## 2. Goal

Take a vendor submission as input and produce a **clear status** — `APPROVED`, `PENDING_INFO`, `PENDING_REVIEW`, or `REJECTED` — with the reasoning fully visible, and for anything not approved, communicate back exactly what is needed, without a human doing the reading.

The system does not replace the human reviewer for genuine judgement calls. It removes the manual reading, the manual triage, and the manual drafting, and it routes each case to the right place.

## 3. Target users

**Primary — procurement / AP operations reviewer.** Handles dozens of new vendors a quarter alongside everything else. Wants the obvious cases handled automatically and a short, honest brief on the ones that actually need them. Non-technical: the output has to be readable, and the reasoning has to be in plain English.

**Secondary — compliance.** Sees only the cases that hit screening or a serious inconsistency. Needs a complete, non-rewritable record of what was checked and what was found.

**Tertiary — the vendor.** Never uses the system directly, but receives its output: a single, specific, courteous request for whatever was missing or malformed — not a drip of one item at a time.

## 4. Core user stories

1. As a reviewer, I submit a vendor's details and get back a status and a plain-English summary, so I don't have to read the whole submission myself.
2. As a reviewer, when a submission is incomplete, the system drafts one message to the vendor listing everything needed, so I'm not sending three emails across three weeks.
3. As a reviewer, when a submission has a subtle inconsistency I would have missed, the system surfaces it with the evidence, so a payment-redirection attempt doesn't get approved.
4. As compliance, when a vendor matches a denied-party list, the case is rejected and no communication is sent to the vendor, so we don't tip off a sanctioned party.
5. As a reviewer, I can see every check that ran and every finding, so I can trust the decision and defend it in an audit.
6. As an operations lead, I can see the queue filtered to the cases that actually need a human, so nothing sits waiting in an inbox.

## 5. Functional requirements

### 5.1 Input
- Accept a vendor submission as structured JSON: legal name, trading name, country, entity type, registration number, tax ID, address, contact, directors, bank details, and a list of attached documents (with the text already extracted from each).
- Accept submissions from a set of supported countries (US, GB, DE, IN, SG at launch). Adding a country must not require code changes.

### 5.2 Checks (all six run on every submission)
- **Completeness** — every required field and required document is present, per the country's rules. Wrong document type is distinguished from missing document.
- **Format** — tax ID and registration number match the country's pattern; IBAN passes ISO 13616 mod-97; ABA routing number passes the 3-7-1 checksum; SWIFT/BIC and email are well-formed.
- **Consistency** — legal name vs bank account holder; claimed country vs IBAN country, tax-ID country, and address country; email domain vs website.
- **Documents** — the name and identifiers on each attachment match the form; documents that attest to a current state are not expired or stale.
- **Screening** — entity, trading name, every director, and the bank account holder are screened against denied-party lists, in two confidence bands.
- **Duplicates** — the bank account is not already registered to a different vendor; the registration number and tax ID are not duplicates.

### 5.3 Decision
- Each finding carries a severity from a fixed, ordered set.
- The case status is a pure function of the highest severity present. No weighted scoring.
- A submission with no findings is `APPROVED`.

### 5.4 Output
- A status and a reviewer-facing summary for every case.
- A drafted vendor email **only** when the status is `PENDING_INFO`, listing every vendor-fixable item.
- A complete, append-only record of every check and every finding, with evidence.

### 5.5 Interfaces
- An intake surface to submit a vendor (from bundled samples or pasted JSON) and watch each check resolve live.
- A review queue showing all cases, ordered so that "needs our decision" surfaces first, filterable by status.
- A case detail view showing the summary, the drafted email (or an explanation of why none was sent), the findings grouped by who acts on them, and the raw submission.
- A view of the country rule packs and reference data, so a reviewer can see where a rule came from.

## 6. Non-functional requirements

- **Runs with no external dependencies.** The full system — every check, the decision, the UI, the generated documents — works offline with no API key. This is a hard requirement, both for the live demo and because the checks that matter are deterministic and must not depend on a model being reachable.
- **Reproducible.** The same submission and the same reference data produce the same decision every time. The decision must be expressible as a finite set of enumerated states.
- **Auditable.** Checks and findings are append-only; they are never updated or deleted.
- **Explainable to a non-technical reader.** Every status carries prose a procurement clerk can act on.
- **Fast enough to feel instant.** Sub-second per submission at real throughput; an artificial pace is added only so the live view is legible.
- **Safe by default.** Every ambiguous or failed check degrades toward "ask a human", never toward approval.

## 7. Success criteria

- Every one of the seven test submissions reaches its intended status, for the intended reasons.
- A reviewer can look at any case and understand the decision without reading the raw submission.
- No vendor-facing message is ever generated for a case under review or rejected.
- Adding a new country is a single YAML file.
- The test suite pins both the outcomes and the algorithms underneath them.

## 8. Out of scope (deliberately)

- Real document parsing / OCR. Document text is supplied with the fixtures so cross-referencing can be exercised; a production build adds OCR + a vision model behind the same interface.
- Live registry verification (Companies House, Handelsregister). Formats and internal consistency are checked; existence is not confirmed.
- Reviewer actions in the UI (approve / reject / request info). Cases surface in the queue but are read-only.
- Resubmission history — a corrected submission is a new case.
- Authentication, multi-tenancy, notifications, and real email sending.

## 9. Key product decisions and their rationale

| Decision | Why |
|---|---|
| No check stops early | The cost of onboarding is round trips; the vendor must be told everything in one message. |
| Status = max severity | An auditable decision must be a finite, reproducible function — not a tunable score. |
| Severity means *who acts*, not *how bad* | A missing form (vendor fixes) and a mismatched bank account (we investigate) are both "not approved" but must route differently. |
| Vendor email only for `PENDING_INFO` | Emailing a rejected or under-review vendor tips off fraud and breaches sanctions handling. |
| LLM writes, never decides | The decision must be reproducible and auditable; prose generation is the only place a model is safe. |
| Rules in YAML | The people who own onboarding rules are not engineers. |
