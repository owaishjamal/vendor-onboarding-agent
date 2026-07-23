# Rules — engineering & AI guardrails

Boundaries for anyone (human or AI) working on this codebase. These are the rules that keep the system correct, auditable, and safe to demo. Breaking one is not a style nit — it changes the meaning of the output.

---

## 1. Invariants that must never break

These are load-bearing. Every one has a test. If a change makes one false, the change is wrong.

1. **The LLM never decides anything.** Status and every finding are fixed by deterministic code before either prompt runs. The model only writes the vendor email and the reviewer summary. Never route a decision, a severity, or a finding through a model.

2. **Status is exactly `max(severity)`.** Do not add weighting, scoring, per-check overrides, or special cases. If a new outcome is needed, it comes from a new severity or a new mapping in `SEVERITY_TO_STATUS`, nothing else.

3. **A vendor email is generated only for `PENDING_INFO`.** Never for `PENDING_REVIEW`, never for `REJECTED`, never for `APPROVED`. This is enforced in `build_vendor_items(findings, status)` and must stay enforced in that one place.

4. **Only `NEEDS_INFO` findings become vendor-facing text.** The filter is on severity, not on whether `vendor_message` happens to be set. This prevents a review-only finding leaking to the vendor even if someone attaches vendor text to it by mistake.

5. **No check stops the pipeline early.** All six run on every submission, always. A `REJECT` does not skip the remaining checks.

6. **Every non-approval degrades toward a human.** A crashed check, an unparseable model response, an unsupported country, a low-confidence match — all resolve to review or a request, never to approval. "We could not run this control" is never grounds for approval.

7. **Checks and findings are append-only.** Never `UPDATE` or `DELETE` rows in `case_check` or `case_finding`. To reset, drop and reseed via `make reset`.

## 2. Libraries — use these

- `fastapi`, `uvicorn`, `pydantic` — API and models.
- `pyyaml` — rule packs.
- `rapidfuzz` — all fuzzy name comparison. Do not hand-roll string similarity.
- `sqlite3` (stdlib) — storage.
- React 18, TypeScript, Tailwind, Vite — frontend.
- Optional LLM SDKs (`anthropic` / `openai` / `google-generativeai`) — only inside `llm/client.py`, never imported elsewhere.

## 3. Libraries — avoid

- **No ORM** (SQLAlchemy, etc.). Raw parameterised SQL only. The schema is three tables.
- **No frontend state library** (Redux, Zustand). Local component state and prop passing are sufficient.
- **No component library** (MUI, Chakra). Tailwind utilities only, so the palette stays in one config.
- **No new heavy dependency** without a clear reason. This is a focused system; each dependency is a thing to explain in the interview.
- **No `requests`/`httpx`/`curl` for arbitrary network calls.** The only outbound calls are the LLM SDKs inside `client.py`.

## 4. Error handling

- **A check that raises must not take down the run.** The runner wraps each check; a crash becomes a single `NEEDS_REVIEW` finding describing the failure. The case still completes with a status.
- **LLM failures are swallowed and fall back to offline.** Missing SDK, bad key, rate limit, unparseable output — all caught in `client.py`, logged at warning, replaced with the template composer. A run never fails because of the model.
- **Invalid submission JSON** returns HTTP 422 with the Pydantic error. Do not try to repair a malformed submission.
- **Unknown country** is a finding (`UNSUPPORTED_COUNTRY`, `NEEDS_REVIEW`), not an exception. We cannot validate what we have no rules for, so we escalate rather than approve.
- **Never swallow an error silently.** Either it becomes a finding the reviewer sees, or it surfaces as an API error. It never disappears.

## 5. What the AI/contributor should do

- Keep each check a **pure function** `run(submission) -> CheckResult`. No I/O beyond reading reference data, no mutation of the submission, no awareness of other checks.
- Decide a finding's severity by **who acts on it**: vendor can fix → `NEEDS_INFO`; we must judge → `NEEDS_REVIEW`; terminal → `REJECT`; note only → `ADVISORY`.
- When adding a country-specific rule, put it in the **YAML pack**, not in Python. If the check needs new logic to read it, keep the *values* in YAML and the *algorithm* in code.
- Write a **golden-case test** for any new scenario, and a **unit test** for any new algorithm (a checksum, a matcher). Outcomes and mechanisms are tested separately on purpose.
- Preserve the **internal vs vendor-facing** split. If you write a `vendor_message`, it must be safe to send to a stranger: no mention of fraud, screening, or internal checks.

## 6. What the AI/contributor should NOT do

- Do not let a model choose a status, a severity, or whether to send an email.
- Do not add a scoring/weighting layer over the severity max.
- Do not make a check depend on another check's output. They are independent by design.
- Do not write account numbers, tax IDs, or personal data into logs. Bank details are fingerprinted before storage; keep it that way.
- Do not add fields to the vendor email that aren't in the payload. The email lists only what the checks found.
- Do not "fix" a fixture to make a test pass. Fixtures encode intended scenarios; if a test fails, the code or the expectation is wrong, not the data.
- Do not introduce early-exit "optimisation" in the runner. The full run is the point.

## 7. Determinism & reproducibility

- Given the same submission and the same reference data, the output status and findings must be identical every run. No randomness, no time-dependence except explicit document-expiry checks (which take an injectable `today`).
- LLM temperature is 0 where the provider supports it, and responses are cached by content hash.

## 8. Security & privacy posture (for this build)

- Local demo only: CORS is open, there is no auth. Do not present this as production-hardened.
- Bank account identifiers are salted-hashed (`bank_fingerprint`) before any comparison or storage.
- The disclosure rule (§1.3–1.4) is a privacy/safety control, not a UX nicety — treat it as security-critical.

## 9. Testing rules

- `make test` must stay green. 44 tests at time of writing.
- Every invariant in §1 has at least one test; do not remove those tests.
- Tests run with `CHECK_DELAY_MS=0` and an isolated DB path. Never point tests at the demo database.
