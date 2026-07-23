# Demo runbook

For the 5-minute video and the live interview. Rehearse this exact order.

---

## Before you start

```bash
make reset
```

Header should show **API online**, `llm: offline`, and the country list.
Keep the **Rules** tab open in a second browser tab — you will switch to it.

`CHECK_DELAY_MS=400` (the default) paces the live view. Set to 0 to show real speed.

---

## The 5-minute video

**0:00 — Frame the problem (20s)**

> "Before you can pay a supplier, someone verifies they're real. Company details,
> banking, tax registration, documents. Today that's manual, the chasing is manual,
> and the audit trail is somebody's inbox. This does it and shows its working."

**0:20 — Happy path, VS-01 Northwind (35s)**

Click it. Let all six checks run.

> "Six checks, all clean, approved automatically. Note it ran *all six* — nothing
> stopped early. That matters in a second."

**0:55 — VS-02 Brightline, the round-trip problem (60s)** ← *the design argument*

> "Missing VAT number. Missing VAT certificate. Missing bank letter. Three separate
> problems found by two different checks."

Scroll to the drafted email.

> "One email, all three items. That's the whole reason nothing stops early."

> "If this pipeline short-circuited like an invoice pipeline does, we'd tell them
> about the VAT number, they'd fix it, resubmit, and we'd tell them about the bank
> letter. Three round trips instead of one. The entire cost of onboarding is round
> trips — so every check runs on every submission, always."

**1:55 — VS-03 Kessler, the fraud signal (70s)** ← *strongest single moment*

> "Now look at this one. German company. IBAN passes its checksum. VAT ID is
> correctly formatted. Registration number is valid. Every single field is fine."

Expand **Cross-field consistency**.

> "The account holder is 'K. Weber Privatkonto'. The company is Kessler
> Industrietechnik GmbH. 37% similar. Nothing wrong with either field — the problem
> only exists when you put them next to each other."

> "That's payment redirection. And notice — no email was drafted."

Point at the black suppression banner.

> "This is a rule I enforce in one place. Vendor emails are generated only for
> PENDING_INFO. Never for review, never for rejection. If someone's under
> investigation for whose bank account that is, emailing them tips them off and
> taints the review."

**3:05 — VS-05 Continental, invisible from the submission (50s)**

> "This submission is spotless. Every field valid, every document matches, name
> matches the account."

Expand **Duplicate & shared-banking check**.

> "The bank account already belongs to Atlas Haulage Group — a different vendor
> already on our master file. You cannot see that from the submission. It only
> exists relative to what we already hold."

> "And it's review, not reject. Group treasury, a parent collecting for a
> subsidiary, factoring — all produce legitimately shared accounts. Auto-rejecting
> breaks real suppliers. Auto-approving is how money leaves. So: never either."

**3:55 — VS-06 Volkov, terminal (35s)**

> "Director matches OFAC at 100%. Rejected outright — one of the few places an
> automated system should say no rather than ask."

> "This submission is *also* missing a bank letter. Ordinary, vendor-fixable. Still
> no email. A sanctions-rejected vendor gets no correspondence at all, for anything."

**NEW capabilities to show if you have time (or save for the interview):**

- **Real document reading (VS-03 / VS-07).** Open VS-03's document check — the "K. Weber" name was *read off the actual bank letter*, not handed to us. Run VS-07 and point out its bank letter is a scanned image that went through OCR at reduced confidence. The documents are real files on disk; the system opens and parses them.
- **Innocent namesake (VS-08).** Director "Sergei Antonov" matches a UK sanctions entry at 100% on name. It still approves — because the supplied date of birth clears it as a different person. Say: "name-only screening would have blocked a legitimate supplier here."
- **Resubmission (VS-02 → VS-09).** Run VS-02 (pending, missing items), then VS-09. It recognises the same company, supersedes the old case, and shows "2 of 2 resolved, nothing new." This is the wait-and-recheck loop, automated.
- **Reviewer action.** On any pending-review case, click Approve with a note. Show that it moves to "Approved by reviewer" and the action is logged with who/when/note — the audit trail is in the case, not an inbox.
- **The eval number.** `make eval`: 100% auto-approve precision, 100% fraud recall, 0 false positives. Lead the interview with this.

**The vendor form (the main intake surface).** The **Vendor form** tab is a real onboarding form — company details, directors (with DOB/nationality), banking (fields adapt to the country), and **document uploads**. Fill it in (or "Start from an example" to prefill the text fields), attach a PDF or two, and submit. The uploaded documents are **read for real** — text layer or OCR — and cross-checked against the form, then every check streams live on the right. This is the "any data submitted can be verified / any new test case" path: type anything, upload anything, watch it get verified. (The **Sample vendors** tab still one-click-runs the 11 curated cases with their pre-rendered documents for the scripted demo.)

**Round-3 capabilities — these are the ones that answer a senior interviewer:**

- **Fabricated vendor (VS-11).** Everything is internally perfect — valid VAT, valid IBAN, matching documents, clean screening. It still doesn't auto-approve, because the registration number exists in no registry. Say: "internal consistency isn't legitimacy; this is the one check that looks outside the submission." This is the strongest single moment in the round-3 set.
- **Subtle redirection (VS-10).** Contrast with VS-03. The account is "Harbourstone Interiors **Holdings** Ltd" — one added word, 90%+ similar, and a threshold alone waves it through. Caught, and routed to review not rejection (group treasury is legitimate). Say: "real fraud uses plausible names, not obviously wrong ones."
- **Volume, not vibes.** `make eval-volume` — 250 generated cases including plausible fraud, 100% precision / 100% recall / 0% FP. Then the honest bit: "during development this found a subtle-fraud miss at 96%; I root-caused it to set-vs-multiset token diffing and fixed it." Admitting a caught-and-fixed miss is more convincing than a clean 100%.
- **Calibration.** `make calibrate` — "why 88? Below it, borderline namesakes get rejected; at 88 nobody is rejected on name alone and every real hit is still caught. The threshold sits on a curve, not a hunch."
- **Override report.** Approve a held case with a note, then show the Queue's amber overrides card — "the system now tells me which check reviewers overrule most, so I know what to recalibrate. The audit log became a feedback loop."

**4:30 — Rules tab (30s)**

Switch tabs. Show `gb.yaml` regex and the name-matching bands.

> "Rules are YAML, not code, because the people who own them aren't engineers. A
> compliance lead can read that VAT pattern and tell me it's wrong. Adding a country
> is adding a file."

> "And the decision is one line: status is the maximum severity across all findings.
> No weighted score to argue about."

---

## Live interview — same order, plus these

**"How do you decide what's a fraud signal versus a typo?"**
Who can fix it. A transposed IBAN digit is something the vendor can correct — it
goes in the email. A bank account in someone else's name isn't something you ask
the vendor about, because if it's fraud you've tipped them off and if it isn't
you've accused a real supplier. That's why severity is about routing, not about
how bad something feels. VS-07 and VS-03 are both banking problems and they go to
opposite places.

**"Why not score the risk and threshold it?"**
Because I'd then be defending the weights. A max over severities has no free
parameters — each check decides its own finding's severity where it has the
context, and the status falls out. VS-04 is the case that shows it: same field
produces a NEEDS_INFO and a NEEDS_REVIEW, and the higher one wins.

**"What stops a NEEDS_REVIEW finding leaking into a vendor email?"**
Two gates, both in `build_vendor_items`. Status must be PENDING_INFO, and the
finding's severity must be NEEDS_INFO. I filter on severity rather than on whether
a vendor message exists, so a leak can't happen even if someone later attaches
vendor text to a review finding by mistake. There's a parametrised test on it.

**"How does this scale to a new country?"**
One YAML file. Tax ID and registration regex, payment scheme, required documents.
No code change. The harder part isn't the format rules — it's whether you have a
registry to verify against, which I haven't built.

**"What's the weakest part?"**
Document text is supplied by the fixtures rather than extracted, so I'm testing
cross-referencing, not OCR. And screening is a four-entry stub matching on name
alone — real screening matches on date of birth and nationality, which is exactly
why I made near-matches escalate rather than reject.

**"What next?"**
Registry verification — confirming a Companies House number actually exists and is
active. Everything here validates *format* and *internal consistency*; nothing
confirms the entity is real. That's the biggest gap.

---

## If something goes wrong live

- **"API unreachable"** — backend died. `make api` in the other terminal.
- **A check hangs** — you're on a live provider. Set `LLM_PROVIDER=offline` in
  `.env`, restart the API, carry on. Nothing else changes.
- **A status looks wrong** — check you haven't edited a fixture. `make seed` regenerates.
- **Queue is cluttered from rehearsal** — `make reset`.
