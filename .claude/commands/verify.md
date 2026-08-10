---
description: Run the full verification gate before claiming anything is done
---

Run every gate below. Report the **actual output** of each — a number you read,
not a number you expect. If a command was not run, say so.

```bash
pytest tests/ -q
python scripts/evaluate.py
cd frontend && npm run build
```

Then answer, in one line each:

1. **Tests** — how many passed, how many failed. Name any failure.
2. **Eval** — accuracy, precision, recall, false positives. Any regression from
   11/11 with 0 false positives is a blocker, not a footnote.
3. **Frontend** — did `tsc -b` pass? (`tsc -p` is not a substitute.)
4. **Invariants** — did this change touch severity ordering, the vendor
   disclosure gate, one-way confidence, or category waivers? If yes, name the
   test that proves it still holds.
5. **Secrets** — `git diff --cached` contains no key-shaped string.

If you changed a decision path, also run the prepared scenarios against a live
server and confirm each still reaches its stated verdict:

```bash
pytest tests/test_scenarios.py tests/test_end_to_end.py -q
```

**Do not summarise as complete while any gate is red or unrun.**
