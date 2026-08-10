---
description: Add a vendor category as data, without shipping Python
argument-hint: <category-name>
---

Add the `$ARGUMENTS` vendor category. **This must ship no Python.** If you write
`if category == "$ARGUMENTS"` anywhere, the design has failed — the whole
generalisation claim is that a category is a JSON file.

## 1. Write `data/profiles/categories/$ARGUMENTS.json`

Copy the closest existing profile. Then answer three questions honestly:

**What does this category need that others do not?** Add those as `fields` and
`documents`. Every item needs a `why` — it is shown to the vendor, and "we ask
because you operate vehicles" is the difference between a form and an
interrogation.

**What only applies sometimes?** Make it `conditional` with a `when` expression.
Grammar: `== != >= <= > <`, `in`, `not in`, `is present`, `is absent`, joined by
`and` / `or`. Anything unparseable evaluates to `false`, so a malformed
condition asks for *less*.

**What must this category NOT be asked for?** This is the half people forget,
and it is the more interesting half. An individual professional has no
certificate of incorporation and demanding one parks them in `PENDING_INFO`
forever — unfixable by the vendor, unresolvable by any reviewer. Waive it with
`"requirement": "na"`.

A waiver must be `na` and must live in the **category** layer. `optional` does
not waive, and reading the merged profile broke this once by dropping the GSTIN
requirement for every Indian vendor.

## 2. Check what the form will actually ask

```bash
python - <<'PY'
import sys; sys.path.insert(0, '.')
from backend.app.profiles.store import get_profile, resolve_requirements
p = get_profile(None, "IN", "$ARGUMENTS")
r = resolve_requirements(p, {"country": "IN", "category": "$ARGUMENTS"})
for k in ("fields", "documents"):
    print(k.upper())
    for i in r[k]:
        print(f"  {i['key']:<26} {i['effective']:<10} {i.get('when_explained') or ''}")
PY
```

Read that list as a vendor would. Anything they cannot produce is a bug.

## 3. Add a prepared scenario

A category nobody can demonstrate is a category nobody will believe. Add one to
`backend/app/scenarios.py` with the verdict it should reach and a `teaches`
string saying what it shows. `tests/test_scenarios.py` will hold the pipeline
to it.

## 4. Verify

```bash
pytest tests/test_scenarios.py tests/test_generalized.py -q
```

Confirm specifically that no *other* category's requirements changed — a waiver
that leaks is the failure mode here.
