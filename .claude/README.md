# Agent tooling

Everything here is committed on purpose. A guard that lives on one laptop
guards nothing, and a convention nobody can read is not a convention.

```
CLAUDE.md                  the rules — read this first
.claude/
  settings.json            hook + permission wiring
  commands/                /verify /add-model /add-category /edge-case
  agents/                  verifier, compliance-reviewer
  hooks/                   guard-secrets, post-edit-tests
scripts/
  dev-setup.sh             fresh clone to verified working tree
  mcp_server.py            lets an agent run this system instead of guessing
```

---

## `CLAUDE.md`

The rules of this codebase, written down. Almost all of it was learned by
shipping a bug — the four decision invariants, the waiver that leaked, the
preflight that green-ticked an unrecognised document, the key that reached a
tracked file. It is short because a file nobody finishes is a file nobody
follows.

## Commands

| Command | For |
|---|---|
| `/verify` | The gate before claiming done. Runs tests, eval, frontend build, invariants, secret scan — and demands the real numbers, not expectations. |
| `/add-model` | Add or retune a router model. Enforces that this stays a YAML edit. |
| `/add-category` | Add a vendor category as data. Includes the half people forget: what this category must *not* be asked for. |
| `/edge-case` | Design an edge case worth having. Sets the bar — the obvious rule must give the wrong answer — and lists the four that exist so you do not duplicate them. |

## Sub-agents

**`verifier`** — checks a change it did not write. Its distinctive job is
attacking the tests: delete the behaviour, confirm the test goes red, restore.
This repo has shipped a vacuous test that passed with the feature removed
entirely, so "the tests pass" is not on its own evidence.

**`compliance-reviewer`** — reviews anything touching the decision path. Not a
linter: it asks whether the severity is right, whether anything leaks to the
vendor, whether a false positive can be cleared, and whether a requirement is
being asked of someone who cannot produce it.

## Hooks

Both exist because of a real incident here.

**`guard-secrets`** (`PreToolUse` on Write|Edit) blocks writes containing
provider-key-shaped strings. A live Gemini key was committed in `.env.example`
— a tracked file — and sat in the history. Writes to `.env` itself pass, since
that is the correct home for a key.

**`post-edit-tests`** (`PostToolUse` on Write|Edit) runs the narrowest suite
covering the file just edited. Deliberately not the full 330 — those take 95
seconds, and a check slow enough to skip protects nothing. Verified: breaking a
line in `confidence.py` surfaces a red test in 9 seconds.

## MCP server

Six read-only tools over the system's own internals, so an agent can **run** the
pipeline rather than predict it:

| Tool | Answers |
|---|---|
| `run_submission` | What verdict does this actually produce, with which findings? |
| `run_scenario` | Does a prepared case still reach its stated verdict? |
| `list_scenarios` | What can be demonstrated? |
| `resolve_requirements` | What would the form ask this vendor, and why? |
| `explain_routing` | Which model would serve this task, and why that one? |
| `check_invariants` | Do the four decision invariants still hold? |

Pinned to `LLM_PROVIDER=offline` and a temp database, so exploring cannot spend
quota or touch real cases. Hand-rolled JSON-RPC over stdio — no dependency, so
the tooling works on a bare Python 3.10.

```bash
claude mcp add vendor-onboarding -- python3 scripts/mcp_server.py
```

`resolve_requirements` and `explain_routing` earn their place: both depend on
layered config resolved at runtime, so reading the source tells you the
algorithm but not the answer.
