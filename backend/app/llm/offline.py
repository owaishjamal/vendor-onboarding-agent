"""Deterministic composition, used when no model is configured.

This is not a language model and does not imitate one. It assembles both
documents from the structured findings using templates.

It works as well as it does because of a design choice made much earlier: every
NEEDS_INFO finding carries its own `vendor_message`, written at the point the
check fired, where the full context was available. The composer only has to
order them and wrap them. That is also why the real model produces such similar
output — it is given the same well-formed material.

The genuine limitation: fixed phrasing. It cannot adapt tone to a vendor who
has already been emailed twice, or merge two related requests into one sentence.
Set LLM_PROVIDER to use a real model for that.
"""

from __future__ import annotations

from typing import Any

# Findings a reviewer should see first, most serious first. Anything not
# listed sorts after these in its natural order.
REVIEWER_PRIORITY = [
    "DENIED_PARTY_MATCH",
    "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR",
    "BANK_NAME_MISMATCH",
    "DOCUMENT_NAME_MISMATCH",
    "DENIED_PARTY_NEAR_MATCH",
    "TAX_ID_COUNTRY_MISMATCH",
    "IBAN_COUNTRY_MISMATCH",
    "DUPLICATE_VENDOR_REGISTRATION",
    "DUPLICATE_TAX_ID",
    "ADDRESS_COUNTRY_MISMATCH",
    "UNSUPPORTED_COUNTRY",
]


def _rank(code: str) -> int:
    return REVIEWER_PRIORITY.index(code) if code in REVIEWER_PRIORITY else 99


def draft_vendor_email(payload: dict[str, Any]) -> str:
    """Compose the single consolidated request to the vendor."""
    vendor = payload.get("legal_name") or "there"
    contact = (payload.get("contact_name") or "").split(" ")[0]
    greeting = f"Hi {contact}," if contact else f"Hello {vendor},"

    items: list[str] = [
        i for i in (payload.get("vendor_items") or []) if i
    ]
    if not items:
        return ""

    plural = "a few details" if len(items) > 1 else "one detail"
    bullets = "\n".join(f"  - {i}" for i in items)

    return (
        f"{greeting}\n\n"
        f"Thanks for sending through your details to set you up as a supplier. "
        f"Before we can finish, we need {plural} from you:\n\n"
        f"{bullets}\n\n"
        f"Once you send these over we'll pick it straight back up. If anything "
        f"above is unclear, just reply to this email and we'll help.\n\n"
        f"Many thanks,\nSupplier Onboarding"
    )


def reviewer_summary(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    vendor = payload.get("legal_name") or "This vendor"
    findings = sorted(payload.get("findings", []), key=lambda f: _rank(f.get("code", "")))
    blocking = [f for f in findings if f.get("severity_name") in ("NEEDS_REVIEW", "REJECT")]
    info = [f for f in findings if f.get("severity_name") == "NEEDS_INFO"]
    advisory = [f for f in findings if f.get("severity_name") == "ADVISORY"]

    if status == "APPROVED":
        note = (f" {len(advisory)} advisory note(s) were recorded on the file but none "
                f"block approval." if advisory else "")
        return (
            f"{vendor} passed every check and has been approved automatically. "
            f"Identifiers are correctly formatted for their country, the bank account "
            f"holder matches the registered legal name, supporting documents corroborate "
            f"the form, and no denied-party or duplicate-banking matches were found."
            f"{note} No action required."
        )

    if status == "REJECTED":
        lead = blocking[0] if blocking else {}
        suppressed = payload.get("suppressed_vendor_items") or []
        tail = (
            f" {len(suppressed)} routine item(s) were also missing from the submission; "
            f"no request has been sent and none should be."
            if suppressed else ""
        )
        return (
            f"{vendor} has been rejected. {lead.get('message', '')} "
            f"Refer to compliance for confirmation and record-keeping.{tail}"
        )

    if status == "PENDING_REVIEW":
        lead = blocking[0] if blocking else {}
        rest = blocking[1:]
        parts = [f"{vendor} needs a human decision before onboarding can continue.",
                 lead.get("message", "")]
        if rest:
            parts.append(
                f"There {'is' if len(rest) == 1 else 'are'} also {len(rest)} further "
                f"finding(s) requiring review: "
                + "; ".join(r.get("code", "").replace("_", " ").lower() for r in rest) + "."
            )
        if info:
            parts.append(
                f"Separately, {len(info)} item(s) are missing or malformed and can be "
                f"requested from the vendor once the above is resolved."
            )
        else:
            parts.append("Everything else on the submission checked out cleanly.")
        return " ".join(p for p in parts if p)

    # PENDING_INFO
    n = len(info)
    detail = "; ".join(f.get("message", "") for f in info[:3])
    more = f" ({n - 3} further item(s) not listed here.)" if n > 3 else ""
    return (
        f"{vendor} is complete enough to progress but {n} item(s) are missing or "
        f"malformed: {detail}{more} None of these indicate a problem with the vendor — "
        f"they are ordinary submission errors. A request listing everything needed has "
        f"been drafted and is ready to send. No internal review is required; this is "
        f"waiting on the vendor."
    )
