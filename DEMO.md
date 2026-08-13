# Demo runbook

The 5-minute video, and the live interview after it. Rehearse this exact order.

---

## Before you record

```bash
# 1. Fresh queue, paced so the live view is readable
CHECK_DELAY_MS=400 SEED_DEMO_CASES=1 uvicorn backend.app.api.app:app --port 8000
```

Open **two browser tabs**:

| Tab | URL | Used at |
|---|---|---|
| 1 | `/home` → sign in as ops | throughout |
| 2 | `/queue` | 3:40 |

Check before you hit record:

- Header shows `● live` — if it says `mock`, no key is loaded and the AI text falls back to templates. **The verdict is identical either way**, so this is not fatal. Say so if it comes up.
- Close every other tab. Notifications off.
- Zoom to ~110%. The check names must be readable in a compressed recording.

**Do not rehearse with `CHECK_DELAY_MS=0`.** You want to *see* the stages land.

---

## The script — 5:00

### 0:00 — The problem (25s)

> "Before a company pays a supplier, someone has to verify they're real —
> company details, bank account, tax registration, documents. That review is
> manual, chasing missing paperwork is manual, and the audit trail is somebody's
> inbox. This does it in about fifteen seconds and shows its working.
>
> What I want to show you isn't that it approves good vendors. It's the cases
> where the obvious rule gives the wrong answer."

### 0:25 — Happy path (60s)

`/m/onboard` → **Clean goods supplier** from *Or load a prepared case*.

> "A vendor picks what they supply. They only get asked what that category
> actually needs — this is a JSON profile, not a branch in the code."

Hit **Submit for verification**. Let all nine checks land.

> "Nine checks, running live. Seven are deterministic — regex, an IBAN
> checksum, a registry lookup. Two use a model, and the report tells you which
> is which, because 'the checksum failed' and 'the model thinks this looks like
> a resume' deserve different levels of trust.
>
> Nothing short-circuits. Even once a rejection is certain, every check still
> runs — so the vendor gets one message listing everything, not one item per
> round trip."

Verdict: **APPROVED**, confidence 100%.

### 1:25 — Edge case 1: the flawless fraud (55s)

Back → **Bank account already belongs to another vendor**.

> "Watch this one — every field is valid."

Submit. Point at the checks going green, then the verdict.

> "Formats pass. Documents corroborate the form. The company is in the
> registry. And it comes back **needs review** — because that bank account is
> already on our master file under a different company. Nothing *inside* the
> submission is wrong. It's only visible by comparing against what we already
> hold. That's invoice-redirection fraud.
>
> And notice it is **not** rejected. Group treasury, a parent collecting for a
> subsidiary, factoring — all produce this exact pattern legitimately.
> Auto-rejecting breaks real suppliers; auto-approving is how money leaves. So
> a human decides, with the conflicting record attached."

### 2:20 — Edge case 2: the innocent namesake (50s)

Back → **Director shares a name with a sanctioned individual**.

> "This director's name matches an OFAC entry exactly. One hundred percent."

Submit. Verdict: **APPROVED**.

> "Approved — because his date of birth and nationality don't match the
> listing. Names aren't unique, especially transliterated. Screening on names
> alone means turning away legitimate suppliers, and that's a fairness problem
> as much as a commercial one.
>
> The near-match is still written to the audit trail. 'We saw it and cleared
> it, here's why' is a much better record than never having looked."

If time is tight, skip the next line. If not:

> "And the twin case — same name, matching DOB and nationality — is rejected
> outright, no human. Sanctions are one of very few places an automated system
> should refuse rather than escalate."

### 3:10 — Edge case 3: valid today, worthless next month (30s)

Back → **Insurance valid today, expires in three weeks**.

Submit. Verdict: **APPROVED WITH CONDITIONS**.

> "A binary valid/expired test gets this wrong both ways. Blocking a vendor
> whose cover is valid today is how teams learn to route around procurement.
> Waving it through is how a contractor ends up on site with lapsed liability
> cover. So there's a fourth verdict: onboarded now, renewal recorded against
> them and chased before the date."

### 3:40 — Ops dashboard (45s)

Switch to tab 2, `/queue`.

> "Everything that's run, with status and the reason. Touch rate — how often a
> human was needed."

Open the shared-bank-account case.

> "The full report. Every finding with its evidence. Deterministic and AI
> checks rendered separately. The verification matrix — every claim on the
> form, corroborated, contradicted or unevidenced."

Copilot tab → type **why was this flagged?**

> "And a copilot grounded in this case record. Most questions are answered
> straight from the data rather than a model, because that can't hallucinate.
> When it doesn't know, it says so instead of inventing a compliance opinion."

### 4:25 — How it's built (35s)

> "Two things I'd call out.
>
> Categories are data. Six of them, six JSON files — adding one ships no
> Python. The interesting half is what a category *stops* asking for: a
> freelancer has no certificate of incorporation, and demanding one parks them
> in 'more information needed' forever, unresolvable by anyone.
>
> And LLM calls go through a router over Groq, Cerebras and Gemini. It picks a
> provider per request from live rate-limit and health state, and fails over on
> a 429. I tested it with three deliberately invalid keys — every model fails,
> every breaker opens, and all seven cases still reach the right verdict on
> templates. The compliance decision never depended on a model being up."

**Stop at 5:00.**

---

## Cuts, if you run long

In this order:

1. The sanctions twin (2:20 section, last paragraph)
2. The copilot (3:40 section, last paragraph)
3. Edge case 3 — but say the sentence "there's a fourth verdict for obligations
   that are satisfied now and won't be later," because the four-verdict model is
   the point

**Never cut** the shared bank account. It is the strongest thing here.

---

## Live interview

Same order, but expect interruptions. Have these ready:

**"Show me it's not scripted."**
Type a vendor by hand. Any name, any GSTIN. It runs the same nine checks.
Or attach a random PDF to the photo-ID slot and watch preflight refuse it.

**"What if the model is down?"**
`LLM_PROVIDER=offline` and re-run anything. Same verdict, template prose.

**"How do I add a category?"**
Open `data/profiles/categories/professional.json`. Show `"requirement": "na"`
on incorporation. No Python anywhere.

**"How do you know the tests are real?"**
Delete the disclosure gate in `build_vendor_items` and run
`pytest tests/test_scenarios.py`. Two tests go red. Restore.

**"What's not built?"**
Live registry and sanctions APIs — both behind provider adapters with seeded
data. No real auth on the ops routes. Single process, so no horizontal scale.
Say these plainly; they're in the README.
