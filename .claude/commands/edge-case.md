---
description: Design and wire a new edge case that actually teaches something
argument-hint: <short description of the situation>
---

Add an edge case for: `$ARGUMENTS`

## The bar

**An edge case is only interesting if it is a case where the obvious rule gives
the wrong answer.** "A required field is missing" is not an edge case, it is the
happy path failing. Before writing anything, state:

1. The obvious rule a reasonable engineer would write.
2. Why it gives the wrong answer here.
3. Which of the four verdicts this should reach — and check it is not a fifth
   variation on one the existing cases already cover.

Existing four, so you do not duplicate them:

| Case | Obvious rule it breaks | Verdict |
|---|---|---|
| Bank account belongs to another vendor | every field valid → approve | `PENDING_REVIEW` |
| Sanctions namesake (+ rejected twin) | name matches list → reject | `APPROVED` / `REJECTED` |
| Freelancer, no incorporation | vendors must supply incorporation | `APPROVED` |
| Insurance expiring in 21 days | expired blocks, valid passes | `APPROVED_WITH_CONDITIONS` |

## Build it

Add to `backend/app/scenarios.py` with `kind: "edge"`, the expected verdict,
`expect_why` (the mechanism), and `teaches` (why anyone should care).

**Use real reference data.** The shared-account case works because that account
number genuinely hashes to a fingerprint already in `backend/seed/vendor_master.json`.
A flag set by hand proves nothing. If your case needs a registry entry or a
sanctions record, add it to `backend/seed/` so the collision is real.

**Dates must be relative** (`_in_days`, `_months_ago`). A hardcoded expiry stops
demonstrating anything the year after it is written.

## Test the mechanism, not the status

Add tests to `tests/test_scenarios.py`. The status assertion is the weakest one:
also assert it reached that status *for the right reason*, and that nothing else
blocking fired. Then confirm the test is not vacuous — break the behaviour
deliberately and check the test goes red.

## Verify

```bash
pytest tests/test_scenarios.py -q
python scripts/evaluate.py
```

The new case must be reachable from the vendor form's prefill menu, because a
case that needs hand-typing will never be demonstrated.
