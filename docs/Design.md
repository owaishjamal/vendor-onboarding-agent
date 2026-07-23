# Design — visual system

The look is deliberately restrained: a review tool, not a marketing page. Colour carries meaning (status and severity), never decoration. If a reviewer glances at the screen, the colours alone should tell them where the trouble is.

---

## 1. Principle

**Colour is information.** Green means resolved, amber means a human is needed, sky-blue means the vendor is needed, red means stop. Nothing is coloured for style. A screen that is mostly grey with one amber row is doing its job — it's pointing at the one thing that matters.

## 2. Palette

Tailwind's default scale, used semantically. No custom hex beyond what Tailwind ships.

### Status (the four case outcomes)

| Status | Colour | Tailwind | Meaning |
|---|---|---|---|
| `APPROVED` | Emerald | `emerald-50/500/700` | Cleared automatically |
| `PENDING_INFO` | Sky | `sky-50/500/700` | Waiting on the vendor |
| `PENDING_REVIEW` | Amber | `amber-50/500/800` | Waiting on us |
| `REJECTED` | Rose | `rose-50/500/700` | Cannot onboard |

### Severity (the five finding levels)

| Severity | Colour | Chip label |
|---|---|---|
| `INFO` | Slate | (hidden from reviewer views) |
| `ADVISORY` | Slate | "Advisory" |
| `NEEDS_INFO` | Sky | "Ask vendor" |
| `NEEDS_REVIEW` | Amber | "Needs review" |
| `REJECT` | Rose | "Reject" |

The status and severity palettes rhyme on purpose: a `NEEDS_INFO` finding is sky, and it produces a `PENDING_INFO` (sky) status. The reviewer learns one colour language, not two.

### Neutrals & surfaces

| Role | Tailwind |
|---|---|
| Page background | `slate-50` |
| Card / surface | `white` |
| Borders | `slate-200` |
| Primary text | `slate-900` |
| Secondary text | `slate-600` |
| Muted / meta | `slate-400` / `slate-500` |
| Dark accent (active tab, suppression banner) | `slate-900` |
| Focus ring | `indigo-100` on `indigo-400` |

Indigo appears only as the interaction accent (active-run pulse, focus rings, selected sample) — it is the one colour with no semantic meaning, reserved for "you are here / this is live."

## 3. Typography

- **UI text:** system sans stack — `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`. No web font to load; renders instantly and looks native.
- **Data & codes:** monospace — `ui-monospace, SFMono-Regular, Menlo, monospace`. Used for finding codes, IDs, regex, IBANs, account fingerprints — anything where character-level precision matters and alignment helps scanning.

### Scale

| Use | Size / weight |
|---|---|
| Page / section heading | `text-sm font-semibold` (14px) — intentionally small; this is a dense tool |
| Card title / vendor name | `text-base font-semibold` (16px) |
| Body | `text-xs` → `text-sm` (12–14px) |
| Meta / labels | `text-[10px]`–`text-[11px]`, often `uppercase tracking-wide` |
| Stat numbers | `text-2xl font-semibold tabular-nums` |

`tabular-nums` on every figure so columns of numbers align.

## 4. Component patterns

- **Cards** — `rounded-xl border border-slate-200 bg-white`. The default container. Padding `p-5` for primary, `p-3` for nested.
- **Status badge** — pill, coloured fill + ring + a leading dot. Three sizes (`sm`/`md`/`lg`).
- **Severity chip** — small uppercase tag with a coloured ring; the leading edge of a finding card is a 0.5px bar in the severity colour.
- **Finding card** — severity bar on top, code + field chips, internal message, and — when present — a distinct sky-tinted block labelled **"Text sent to vendor"** so disclosure is visible at a glance. Review-only findings show "Internal only — not disclosed to the vendor."
- **Check timeline** — vertical list with a connector line; each step has an icon (spinner while active, coloured tick/!/?/✕ once resolved), a summary, a duration, and an expand chevron when it has findings.
- **Tabs** — segmented control on `slate-100`, active tab `white` with shadow; top-level nav uses `slate-900` fill for the active tab.
- **Suppression banner** — `slate-900` dark block explaining why no vendor email was sent. Dark on purpose: it's a statement of policy, not a warning, and it should read as deliberate.

## 5. Motion

- One keyframe: `slidein` (6px rise + fade, 0.28s). Applied as each check result and finding lands, so the live view feels like it's assembling in real time rather than blinking into place.
- Active check: an `animate-ping` indigo halo around the current step.
- No other animation. Motion signals "this just happened," nothing else.

## 6. Layout

- Max content width `1400px`, centred, `px-6`.
- Intake and case detail use a two-column split (`380–400px` control rail + fluid main) on `lg`, stacking on smaller screens.
- Sticky translucent header (`bg-white/90 backdrop-blur`) with nav and a live API/LLM status indicator.
- Scroll containers use a thin custom scrollbar (`scroll-thin`) so long traces and JSON blocks don't feel heavy.

## 7. Tone of the copy

The visual restraint extends to words. Reviewer summaries and vendor emails are plain, specific, and calm. Vendor-facing text is warm and matter-of-fact ("this is usually a typo"), never officious. Internal text is precise and can name a concern directly. The design never uses alarming language where a colour already carries the signal.

## 8. Accessibility notes

- Status is never conveyed by colour alone — every badge pairs the colour with a text label and a shape (dot/icon).
- Text sizes are small by design for density; primary actions and headings stay at or above 14px.
- Contrast: coloured text uses the `-700/-800` shades on `-50` fills, which clear WCAG AA for the sizes used.
