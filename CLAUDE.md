# Working in this repo

Vendor onboarding and verification. A submission goes in, a decided status
comes out, and every reason is visible. Read `README.md` for the design and
`docs/LLM-Router.md` for the LLM layer.

This file is the short version of what has already gone wrong here. Most of it
was learned by shipping a bug, not by reasoning from first principles.

---

## The one rule

**Verification means running it, not reading it.**

Every claim in a summary must come from output you actually saw in this
session. "Tests should pass" is not "330 passed". If you did not run it, say
you did not run it — that is a fine answer and a wrong claim is not.

Before saying you are done: `/verify`.

---

## What this system decides, and why that constrains you

```
status = SEVERITY_TO_STATUS[max(f.severity for f in findings)]
```

That is the entire decision rule. No weighting, no tuned threshold. Each check
decides how serious its own finding is; the status falls out of the worst thing
present. If you find yourself adding a score, a weight or a magic number to a
decision path, stop — you are about to make the verdict unarguable.

Four invariants. Breaking any of these is a correctness bug, not a style
preference, and each has a test that fails loudly.

**1. Severity ordering is load-bearing.**
`CONDITION` sits between `ADVISORY` and `NEEDS_INFO` deliberately. Because
status is `max(severity)`, ordering alone guarantees a condition can never
upgrade a case — only hold it or be overtaken. Never compare severities with
integer literals; that broke once when `CONDITION` was inserted. Use
`BLOCKING_SEVERITY` and the enum.

**2. What the vendor is told is gated separately from what the reviewer sees.**
Vendor-facing text exists only for `PENDING_INFO` and `APPROVED_WITH_CONDITIONS`
— the two states where the vendor can act — and each finding needs an explicit
`vendor_message`. A rejected vendor learns nothing. Telling someone they matched
a sanctions list is tipping off, a criminal offence in several jurisdictions,
and it teaches an adversary which name to change.

**3. Confidence is one-way.**
It can send a case to a human. It can never rescue one. A low score blocks an
auto-approval; it never turns a flagged case into an approval.

**4. Not recognising something is not the same as it being fine.**
Preflight once returned a green tick for a cover letter dropped into the photo-ID
slot, because the type check only ran when a slot declared accepted types — and
category documents declare none. Absence of a check is not a pass.

---

## Architecture rules

**Categories are data, not code.** Six vendor categories live in
`data/profiles/categories/*.json`. Adding one ships no Python. If you are
writing `if category == "professional"`, the answer is a profile field.

**Requirements resolve in three layers**: country pack → category profile →
client profile. Each overrides only what it names. A category that says nothing
about a field inherits the country pack unchanged.

**Waivers are per-category and explicit.** A category may waive a country field
by declaring it `na` — and only `na`, and only from the category layer. Reading
the *merged* profile and honouring `optional` silently dropped the GSTIN
requirement for every Indian vendor. That bug got written, caught by
`test_a_company_in_the_same_country_is_still_asked_for_its_tax_id`, and fixed.
Do not re-broaden it.

**The `when` grammar is deliberately not `eval`.** Profiles are configuration,
and configuration that executes arbitrary code is not configuration. Anything
unparseable evaluates to `false`, so a malformed profile asks for *less*.

**Nothing short-circuits.** Every check runs on every submission, even after a
rejection is certain, so the vendor gets one message listing everything and the
reviewer gets the full picture. This is the opposite of an invoice pipeline and
is the right trade at onboarding volume.

**7 of 9 checks are deterministic and must never call a model.** An IBAN
checksum does not need an LLM: it would be slower, non-reproducible and less
correct. The two AI checks are never the sole basis for an approval, and the
ops report renders the two kinds separately because a reviewer must know what
to trust.

---

## LLM access

Never import a provider SDK or call an LLM API directly. Everything goes
through the router:

```python
from backend.app.llm.router import LLMRouter
r = await router.generate(messages, task_type="reasoning")
```

The caller does not choose a provider — that is decided per request from live
rate-limit and health state. Adding a model is an edit to
`backend/app/llm/router/models.yaml`; adding an OpenAI-compatible provider is
the same file plus a `base_url`. See `/add-model`.

`LLM_PROVIDER=offline` must always produce a complete, correct case. Every
verdict in the README is reproducible with no key and no network, and that is
what makes the test suite hermetic. If a change makes the app require a key,
the change is wrong.

---

## Secrets

**A real Gemini key was once committed in `.env.example`.** It sat in a tracked
file and reached the history.

- Never write a key into any tracked file, including examples and tests.
- Never print, log or include a key in an error message, a repr or a metric
  label. Adapters read keys from the environment at call time for exactly this
  reason.
- `.env` is gitignored. `.env.example` is committed and holds empty values.
- A `PreToolUse` hook blocks writes containing key-shaped strings. If it fires,
  it is right and you are wrong — do not work around it.

---

## Tests

```bash
pytest tests/ -q                  # 330
python scripts/evaluate.py        # 11/11, precision and recall
cd frontend && npm run build      # tsc -b, catches what tsc -p misses
```

`tests/conftest.py` pins `LLM_PROVIDER=offline` and a temp database before any
test module imports config. Do not read config at import time in a test.

**Write tests that fail when the behaviour breaks.** A test asserting a status
is weak; assert the *mechanism*. `shared-bank-account` reaching `PENDING_REVIEW`
only counts if it got there on the shared-account finding — if it started
failing for a missing document, the status would still pass while the scenario
stopped demonstrating anything.

**Check your test is not vacuous.** One tipping-off test read `vendor_items` off
the stored case, which never persists it, so it asserted "no leak" against
`None` and passed with the disclosure gate deleted outright. If a test protects
something important, delete the protection and confirm the test goes red.

Prepared scenarios in `backend/app/scenarios.py` state their expected verdict,
and `tests/test_scenarios.py` holds the real pipeline to it. Change a decision
path and these tell you what you changed.

---

## Frontend

`npm run build` runs `tsc -b` with project references. `tsc -p tsconfig.json`
passes on code that `tsc -b` rejects — six type errors reached a Docker build
that way. Verify with the real build command.

Tailwind config keys added mid-session are not picked up by a running dev
server; a `@apply` on a fresh token can break the build while the dev server
looks fine.

---

## Conventions

- Comments explain **why**, especially why an obvious alternative is wrong.
  A comment restating the code is noise.
- Commit messages: what changed, why, and what it cost. No AI attribution.
  Author is the repo owner.
- Prefer deleting to deprecating. `scripts/fix_*.py` and `scripts/refactor_*.py`
  are dead one-off migrations and should not be extended.
- Ask before a large refactor. Do not reorganise files you were not asked to
  touch.

---

## Where things are

```
backend/app/
  models.py          Severity, Status, FindingCode — the vocabulary
  scenarios.py       Prepared cases + their expected verdicts
  pipeline/          runner.py (9 checks, aggregate, decide), confidence.py
  checks/            One module per check
  dva/               Document classification, verification, preflight
  profiles/          Three-layer resolution + the safe `when` grammar
  llm/router/        Provider routing — see docs/LLM-Router.md
  rules/*.yaml       Country packs
data/profiles/categories/*.json   The generalisation surface
tests/                            330 tests
```
