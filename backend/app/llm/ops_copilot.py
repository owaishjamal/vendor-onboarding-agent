"""Grounded question answering over one vendor case.

WHY THIS IS NOT JUST A PROMPT

The questions an ops reviewer actually asks — "what's missing?", "which checks
failed?", "why was this flagged?", "show me the evidence" — are lookups against
a structured record we already hold. A model adds nothing to a lookup except
the risk of getting it wrong, and this record decides whether a company gets
paid.

So the copilot answers from the case data directly, and every answer is
assembled from stored findings, check results and extracted fields. There is
no step where a model is asked to recall a fact.

The model still earns its place: when a question doesn't match a known intent
and a provider is configured, the question plus the same grounded context is
handed to it. When no provider is configured, the copilot says what it can and
what it cannot, rather than improvising — which is what the previous offline
stub did when it replied "the case looks okay" to every question, regardless of
the case.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Severity names in ascending order of seriousness.
_BLOCKING = {"NEEDS_INFO", "NEEDS_REVIEW", "REJECT"}
_VENDOR_FIXABLE = {"NEEDS_INFO"}

_STATUS_PLAIN = {
    "APPROVED": "approved",
    "APPROVED_WITH_CONDITIONS": "approved with conditions",
    "PENDING_INFO": "waiting on the vendor",
    "PENDING_REVIEW": "waiting on an internal reviewer",
    "REJECTED": "rejected",
    "ERROR": "interrupted before a decision",
}


def _findings(case: dict) -> list[dict]:
    return case.get("findings") or []


def _checks(case: dict) -> list[dict]:
    return case.get("checks") or []


def _blocking(case: dict) -> list[dict]:
    return [f for f in _findings(case) if f.get("severity_name") in _BLOCKING]


def _fmt_finding(f: dict, *, with_evidence: bool = False) -> str:
    bits = [f"• [{f.get('severity_name')}] {f.get('code')}"]
    if f.get("field"):
        bits.append(f" ({f['field']})")
    line = "".join(bits) + f"\n  {f.get('message', '').strip()}"
    if with_evidence and f.get("evidence"):
        ev = ", ".join(f"{k}={v}" for k, v in list(f["evidence"].items())[:6])
        line += f"\n  Evidence: {ev}"
    return line


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------

def _why_verdict(case: dict) -> str:
    status = case.get("status", "")
    conf = case.get("confidence") or {}
    out = [f"**{case.get('legal_name', 'This vendor')}** is {_STATUS_PLAIN.get(status, status)}."]
    if conf.get("decision_reason"):
        out.append(conf["decision_reason"])
    blocking = _blocking(case)
    if not blocking:
        out.append("No blocking findings were raised.")
    else:
        drivers = [f for f in blocking if f.get("severity_name") == _worst(blocking)]
        out.append(f"\nThe verdict was driven by {len(drivers)} finding(s) at "
                   f"{_worst(blocking)}:")
        out += [_fmt_finding(f) for f in drivers]
    if conf.get("score") is not None:
        out.append(f"\nOverall confidence {conf['score']:.0%}.")
    return "\n".join(out)


def _worst(findings: list[dict]) -> str:
    order = ["INFO", "ADVISORY", "CONDITION", "NEEDS_INFO", "NEEDS_REVIEW", "REJECT"]
    present = [f.get("severity_name", "INFO") for f in findings]
    return max(present, key=lambda s: order.index(s) if s in order else 0)


def _whats_missing(case: dict) -> str:
    missing = [f for f in _findings(case)
               if f.get("code") in ("MISSING_REQUIRED_FIELD", "MISSING_REQUIRED_DOCUMENT")]
    if not missing:
        return "Nothing is outstanding — every required field and document was supplied."
    out = [f"{len(missing)} item(s) outstanding:"]
    out += [_fmt_finding(f) for f in missing]
    vendor_items = [f["vendor_message"] for f in missing if f.get("vendor_message")]
    if vendor_items:
        out.append("\nWhat the vendor has been asked for:")
        out += [f"  - {m}" for m in vendor_items]
    return "\n".join(out)


def _which_failed(case: dict) -> str:
    rows = []
    for c in _checks(case):
        fs = [f for f in _findings(case) if f.get("check") == c.get("check")]
        blocking = [f for f in fs if f.get("severity_name") in _BLOCKING]
        if blocking:
            rows.append(f"• {c.get('label')} — {len(blocking)} blocking finding(s): "
                        + ", ".join(sorted({f['code'] for f in blocking})))
    if not rows:
        return "No check raised a blocking finding. Every check ran and passed."
    return f"{len(rows)} of {len(_checks(case))} checks raised something:\n" + "\n".join(rows)


def _mismatches(case: dict) -> str:
    codes = ("MISMATCH", "CONTRADICTED", "SHARED", "DUPLICATE")
    ms = [f for f in _findings(case) if any(c in (f.get("code") or "") for c in codes)]
    if not ms:
        return "No mismatches were found between the form, the documents or our records."
    return (f"{len(ms)} mismatch(es):\n"
            + "\n".join(_fmt_finding(f, with_evidence=True) for f in ms))


def _risks(case: dict) -> str:
    serious = [f for f in _findings(case)
               if f.get("severity_name") in ("NEEDS_REVIEW", "REJECT")]
    if not serious:
        return ("No risk findings. Anything outstanding is administrative — "
                "missing or malformed input the vendor can fix.")
    return (f"{len(serious)} risk finding(s), most serious first:\n"
            + "\n".join(_fmt_finding(f, with_evidence=True) for f in serious))


def _expiring(case: dict) -> str:
    exp = [f for f in _findings(case)
           if f.get("code") in ("DOCUMENT_EXPIRED", "DOCUMENT_EXPIRING_SOON")]
    if not exp:
        return "No document on this case is expired or expiring soon."
    return "\n".join(_fmt_finding(f, with_evidence=True) for f in exp)


def _ask_vendor(case: dict) -> str:
    """What to ask the vendor — gated on whether we should be asking at all.

    A case under internal review, or one already rejected, must not produce a
    "here's what to send us" script. The same rule the email generator applies
    has to apply here, or the copilot becomes the way round it: a reviewer
    copies the list into an email and tips off the party being investigated.
    """
    status = case.get("status", "")
    items = [f["vendor_message"] for f in _findings(case)
             if f.get("severity_name") in _VENDOR_FIXABLE and f.get("vendor_message")]

    if status == "REJECTED":
        return ("Nothing. This vendor was rejected on a decisive finding, and "
                "telling them which control caught them is not something we do. "
                "Refer it to compliance.")

    if status == "PENDING_REVIEW":
        out = ["**Do not contact the vendor yet.** This case is with an internal "
               "reviewer, and reaching out while a possible misdirection is being "
               "assessed can tip off a fraudster and taint the review. Resolve the "
               "internal question first."]
        if items:
            out.append("\nOnce it clears, these are the items that would be "
                       "requested (not yet sent):")
            out += [f"  - {m}" for m in items]
        return "\n".join(out)

    if not items:
        return "There is nothing outstanding to ask the vendor for."
    return "Ask the vendor for:\n" + "\n".join(f"  - {m}" for m in items)


def _can_approve(case: dict) -> str:
    status = case.get("status", "")
    blocking = _blocking(case)
    if status in ("APPROVED", "APPROVED_WITH_CONDITIONS"):
        return f"It already is — status {status}."
    if any(f.get("severity_name") == "REJECT" for f in blocking):
        return ("No. There is a terminal finding: "
                + ", ".join(f["code"] for f in blocking
                            if f.get("severity_name") == "REJECT")
                + ". That is decisive on its own and is not a judgement call.")
    review = [f for f in blocking if f.get("severity_name") == "NEEDS_REVIEW"]
    info = [f for f in blocking if f.get("severity_name") == "NEEDS_INFO"]
    out = ["Not on the current information."]
    if review:
        out.append(f"{len(review)} finding(s) need your judgement first:")
        out += [_fmt_finding(f) for f in review]
    if info:
        out.append(f"{len(info)} item(s) must come back from the vendor first.")
    out.append("\nYou can override and approve — the action is recorded against "
               "your name with the findings that stood at the time.")
    return "\n".join(out)


def _evidence(case: dict) -> str:
    withev = [f for f in _findings(case) if f.get("evidence")]
    if not withev:
        return "No finding on this case carries structured evidence."
    return "\n".join(_fmt_finding(f, with_evidence=True) for f in withev[:10])


def _documents(case: dict) -> str:
    docs = None
    for c in _checks(case):
        if c.get("check") == "documents":
            docs = (c.get("data") or {}).get("verdicts")
            break
    if not docs:
        return "No per-document verdicts were recorded for this case."
    out = []
    for d in docs:
        out.append(f"• {d.get('doc_type')} ({d.get('filename')}) — {d.get('status')}"
                   + (f", read as {d.get('detected_type')}" if d.get("detected_type") else "")
                   + (f", name on document '{d.get('name_on_document')}'"
                      if d.get("name_on_document") else ""))
    return "\n".join(out)


def _summary(case: dict) -> str:
    sub = case.get("submission") or {}
    conf = case.get("confidence") or {}
    lines = [
        f"**{case.get('legal_name')}** ({case.get('country')})",
        f"Category: {sub.get('category') or 'not specified'}",
        f"Status: {case.get('status')} — {conf.get('recommendation', 'n/a')}",
        f"Findings: {len(_findings(case))} total, {len(_blocking(case))} blocking",
    ]
    if case.get("reviewer_summary"):
        lines.append("\n" + case["reviewer_summary"])
    return "\n".join(lines)


def _greeting(case: dict) -> str:
    """A greeting should open with the case, not a round trip to a model."""
    return (_summary(case)
            + "\n\nAsk me why this verdict was reached, what is missing, "
              "which checks failed, or to show the evidence.")


# Ordered: first pattern that matches wins, so put specific before general.
_INTENTS: list[tuple[str, Any]] = [
    # Greetings first, and anchored, so "hi" matches but "which" does not.
    (r"^\s*(hi|hey|hello|yo|good (morning|afternoon|evening))\b", _greeting),
    # "What was the issue / problem / what went wrong" is the single most
    # natural way to ask about a verdict, and it was falling through to the
    # model — which is both slower and less reliable than the record.
    (r"\b(issue|problem|wrong|concern|flag(ged)?|caught|catch)\b", _why_verdict),
    (r"\b(what|which).*(missing|outstanding|not (been )?(supplied|provided|attached))", _whats_missing),
    (r"\bmissing\b", _whats_missing),
    (r"\b(which|what).*(check|test).*(fail|flag)", _which_failed),
    (r"\bfailed\b|\bfailing\b", _which_failed),
    (r"\b(mismatch|discrepan|inconsist|match between|differ)", _mismatches),
    (r"\b(risk|red flag|fraud|concern|serious)", _risks),
    (r"\b(expir|renew|out of date|lapse)", _expiring),
    (r"\b(ask|tell|request|chase|email).*(vendor|supplier|them)", _ask_vendor),
    (r"\b(can|should|may).*(approve|onboard|proceed|pay)", _can_approve),
    (r"\b(evidence|proof|source|show me|why do you (say|think)|back .*up)", _evidence),
    (r"\b(document|attachment|file|upload)", _documents),
    (r"\b(why|reason|rationale|explain).*(review|reject|approv|verdict|status|flag)", _why_verdict),
    (r"\bwhy\b", _why_verdict),
    (r"\b(summar|overview|brief|tell me about|what happened)", _summary),
]


def answer(case: dict, question: str) -> Optional[str]:
    """Answer from the case record, or None if no intent matches.

    Returning None rather than guessing is the point: an unmatched question is
    handed to a model (when configured) or honestly declined.
    """
    q = (question or "").strip().lower()
    if not q:
        return None
    for pattern, fn in _INTENTS:
        if re.search(pattern, q):
            return fn(case)
    return None


def context_for_model(case: dict) -> dict:
    """The trimmed, grounded slice of the case handed to a model.

    Deliberately not the whole record: raw base64 documents and internal ids
    add tokens and give the model more room to confabulate. Findings, check
    summaries and the decision are what the questions are actually about.
    """
    sub = case.get("submission") or {}
    return {
        "vendor": {
            "legal_name": case.get("legal_name"),
            "country": case.get("country"),
            "category": sub.get("category"),
            "business_description": sub.get("business_description"),
            "entity_type": sub.get("entity_type"),
        },
        "status": case.get("status"),
        "decision_reason": (case.get("confidence") or {}).get("decision_reason"),
        "confidence": (case.get("confidence") or {}).get("score"),
        "reviewer_summary": case.get("reviewer_summary"),
        "findings": [
            {k: f.get(k) for k in
             ("code", "severity_name", "check", "field", "message", "evidence")}
            for f in _findings(case)
        ],
        "checks": [
            {"check": c.get("check"), "label": c.get("label"),
             "summary": c.get("summary")}
            for c in _checks(case)
        ],
    }


def grounded_menu(case: dict) -> str:
    """The questions answerable straight from the record, no model needed."""
    status = str(case.get("status", "flagged")).lower().replace("_", " ")
    return (
        "I can still answer these directly from the case record:\n"
        f"  • Why was this vendor {status}?\n"
        "  • What documents are missing?\n"
        "  • Which checks failed?\n"
        "  • Are there any mismatches?\n"
        "  • Summarise the major risks.\n"
        "  • What should I ask the vendor to correct?\n"
        "  • Which documents are expiring?\n"
        "  • Show me the evidence.\n"
        "  • Can this vendor be approved?"
    )


NO_MODEL_FALLBACK = (
    "I can only answer from this case's record, and that question doesn't map "
    "to anything I can look up directly. No language model is configured "
    "(LLM_PROVIDER=offline), so I won't guess.\n\n"
    "Try one of these, which I can answer exactly:\n"
    "  • Why was this vendor {status}?\n"
    "  • What documents are missing?\n"
    "  • Which checks failed?\n"
    "  • Are there any mismatches?\n"
    "  • Summarise the major risks.\n"
    "  • What should I ask the vendor to correct?\n"
    "  • Which documents are expiring?\n"
    "  • Show me the evidence.\n"
    "  • Can this vendor be approved?"
)
