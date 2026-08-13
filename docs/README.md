# Docs

Start with the root [`README.md`](../README.md) — it is the full design doc and
covers most of what you need. These go deeper.

## Read in this order

| Doc | What it answers |
|---|---|
| [HLD.md](HLD.md) | **Start here.** System diagrams, request lifecycle, the decision model, the nine checks, generalisation, LLM layer, deployment, trade-offs. |
| [Architecture.md](Architecture.md) | Component-by-component detail below the HLD. |
| [LLM-Router.md](LLM-Router.md) | Routing, rate limiting, circuit breaking, fallback, tools, agents. How to add a provider or a model. |
| [Rules.md](Rules.md) | Country packs and what each check enforces. |
| [Design.md](Design.md) | The visual system — tokens, type, spacing. |

## Context and history

| Doc | What it is |
|---|---|
| [PRD.md](PRD.md) | What was being built and for whom. |
| [Phases.md](Phases.md) | How it was built, in order. |
| [Case-Study-Compliance.md](Case-Study-Compliance.md) | The brief's requirements mapped to where each is satisfied, with honest scores. |
| [Memory.md](Memory.md) | Decisions and their reasons, kept as they were made. |

## Elsewhere in the repo

| File | What it is |
|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | The rules of this codebase — mostly bugs that already shipped once. |
| [`../SUBMISSION.md`](../SUBMISSION.md) | What gets sent, and the checklist before sending. |
| [`../DEMO.md`](../DEMO.md) | The 5-minute video script, timed. |
| [`../DEPLOY.md`](../DEPLOY.md) | Getting it live. |
| [`../.claude/README.md`](../.claude/README.md) | Agent tooling — commands, sub-agents, hooks, MCP server. |

---

**If you only read one thing:** [HLD.md](HLD.md) §3, the decision model. Every
other choice in the system follows from `status = max(severity)` and the fact
that confidence can only ever route a case *towards* a human.
