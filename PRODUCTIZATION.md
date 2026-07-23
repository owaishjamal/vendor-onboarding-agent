# Productization — from case study to enterprise product

An honest map of what stands between this build and a sale to a large
enterprise, and where the code already meets that path. Written so nobody
mistakes a working prototype for a shippable product — and so the gap is
concrete rather than hand-waved.

---

## The one-line truth

You cannot sell *any* prototype to an industry leader "without changes."
Enterprise procurement alone — security review, data-protection assessment,
legal, integration — is months, and most of it is not code. What you *can* do
is build the architecture so every enterprise requirement is a **swap-in, not a
rewrite**. That's what this codebase is set up for, and this document is the
evidence and the gap.

## Three buckets

Everything below is one of:

- **DONE (seam in place)** — implemented as a pluggable interface / toggle; a
  customer deployment fills in the credential or implementation.
- **BUILD** — real engineering work, but the design already accommodates it.
- **NON-CODE** — cannot be written; it's legal, audit, or commercial.

---

## 1. Document processing

| Item | State | Notes |
|---|---|---|
| Text-layer + OCR extraction with confidence | DONE | `document_reader.py` |
| Image preprocessing (deskew / denoise / threshold) before OCR | DONE | OpenCV, two-pass: plain then preprocessed |
| Read caching by file hash | DONE | avoids re-OCR on re-runs |
| Pluggable extractor (`DOC_EXTRACTOR=offline|vision`) | DONE | vision path implemented for Anthropic/OpenAI; needs a key |
| Cloud Document-AI (Textract / Google Doc AI / Azure) | BUILD | add a provider next to the vision one; same interface |
| Async OCR on a worker (not the request thread) | BUILD | move extraction to a queue; the check already tolerates latency |
| Malware scanning of uploads | BUILD | ClamAV / a SaaS scanner before persistence; hook is in `enterprise.validate_upload` |

## 2. Reference data (registry, screening, duplicates)

| Item | State | Notes |
|---|---|---|
| Registry provider interface | DONE | `providers/registry_provider.py` |
| Companies House (UK) live provider | DONE (needs key) | `REGISTRY_PROVIDER=companies_house` + `COMPANIES_HOUSE_API_KEY` |
| Screening provider interface | DONE | `providers/screening_provider.py` |
| Global registries (D&B, GLEIF, per-country) | BUILD | new providers behind the same interface |
| Licensed sanctions/PEP feed (ComplyAdvantage, World-Check, Dow Jones) | BUILD + NON-CODE | code is a provider; the data is a paid contract |
| Ongoing (not just onboarding) monitoring | BUILD | re-screen on a schedule; the case model supports revisions |

## 3. Security

| Item | State | Notes |
|---|---|---|
| Bearer-token auth on the API | DONE (toggle) | `API_TOKEN`; off in demo |
| Upload validation (type + size) | DONE | `enterprise.validate_upload` |
| Bank details stored as salted hashes, not raw | DONE | `duplicates.bank_fingerprint` |
| Real IdP / SSO (OIDC, SAML), RBAC | BUILD | replace the token check with an auth dependency + roles |
| Encryption at rest (documents, PII) | BUILD | KMS-backed volume / field encryption; per-tenant keys |
| Secrets management | BUILD | vault / cloud secret manager instead of env |
| Penetration test, threat model | NON-CODE | external assessment |

## 4. Compliance

| Item | State | Notes |
|---|---|---|
| Append-only audit trail (checks, findings, actions) | DONE | the storage layer never updates or deletes these |
| PII inventory (director DOB, bank details) | BUILD | classify + tag fields for handling |
| GDPR: retention, right-to-erasure, data residency | BUILD + NON-CODE | code the controls; the policy is legal |
| SOC 2 / ISO 27001 | NON-CODE | audit program, months |
| DPA / sub-processor agreements | NON-CODE | legal |

## 5. Scale & reliability

| Item | State | Notes |
|---|---|---|
| Deterministic, reproducible decisions | DONE | the whole design premise |
| Pure `assess()` core (no I/O) for batch scoring | DONE | used by the volume eval |
| Structured JSON logging | DONE (toggle) | `LOG_JSON=1` |
| Prometheus metrics endpoint | DONE | `/metrics` |
| SQLite → Postgres | BUILD | storage is a thin layer with raw SQL; swap the connection + migrations |
| Background workers / queue | BUILD | for OCR + high throughput |
| Horizontal scaling, autoscaling, HA | BUILD | stateless API + shared DB + object storage |
| Real migrations (vs the current additive ALTERs) | BUILD | Alembic or equivalent |
| Observability (tracing, SLOs, alerting) | BUILD | OpenTelemetry |

## 6. Multi-tenancy

| Item | State | Notes |
|---|---|---|
| Tenant tagging + case isolation | DONE (seam) | `X-Org-Id` header → `org_id` on cases; queries scope to it |
| Per-tenant rules / thresholds | BUILD | rule packs are already data; load per tenant |
| Per-tenant data isolation (schema / DB / keys) | BUILD | the `org_id` column is the starting point |

## 7. Human workflow & integrations

| Item | State | Notes |
|---|---|---|
| Reviewer actions + append-only action log | DONE | approve / reject / request-info / resolve |
| Override reporting (calibration signal) | DONE | `/v1/overrides` |
| Roles, assignment, SLAs, notifications, escalation | BUILD | the action model is the foundation |
| Actually send the vendor email / write to ERP | BUILD | the email is drafted; wire an email + ERP connector |
| ERP / P2P integration (SAP Ariba, Coupa, Oracle) | BUILD + NON-CODE | connectors + partner agreements |

---

## What I'd sequence first for a design-partner pilot

1. **Live registry + a licensed sanctions feed** (providers exist; add credentials + one screening integration). This is what makes the verification *real*, not simulated.
2. **Cloud Document-AI extractor** behind the existing interface — generalises document reading to arbitrary real files.
3. **Auth via the customer's IdP + Postgres + a worker queue** — the minimum to run someone else's data safely at volume.
4. **Retention/erasure controls + a PII inventory** — the floor for touching regulated data.

Everything else is a fast-follow, because the seams are already cut.

## What a pilot cannot skip (and no code fixes)

A signed DPA, a security review, and a data-processing agreement with whatever
sanctions/registry vendor you license. Budget for those in weeks, not commits —
they are the real gate, and pretending otherwise is how pilots die.
