"""Profile-declared validation: typed custom fields + declarative rules +
semantic assertions.

This is what lets a client require ANYTHING and still keep verification
automated. Three tiers, cheapest first:

  Tier 1 — typed validators. Every custom field declares a type; each type has
           a deterministic validator (regex/id, number ranges, date, email,
           iban mod-97, aba checksum, select options, url).
  Tier 2 — declarative cross-field rules: field_match / equals / date_before /
           country_consistent, evaluated between any two profile fields.
  Tier 3 — semantic rules: a plain-English assertion checked by the LLM.
           ESCALATE-ONLY: a model "fail" or "unsure" raises a finding at the
           profile's chosen severity; a model "pass" merely adds nothing. If
           no model is configured, the assertion cannot be evaluated and the
           control escalates (an unevaluated control is never grounds for
           approval).

The default profile has no custom fields or rules, so this check is a no-op
for plain submissions — existing behaviour is untouched.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from backend.app.checks.base import EMAIL_RE, Timer, finding, name_score, name_verdict
from backend.app.checks.formats import aba_is_valid, iban_is_valid
from backend.app.models import (
    CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.profiles.store import get_profile

CHECK = "custom_rules"

_SEV = {"ADVISORY": Severity.ADVISORY, "NEEDS_INFO": Severity.NEEDS_INFO,
        "NEEDS_REVIEW": Severity.NEEDS_REVIEW}


def _get(sub: VendorSubmission, key: str) -> Optional[str]:
    if key == "today":
        return date.today().isoformat()
    if key.startswith("bank."):
        v = getattr(sub.bank, key.split(".", 1)[1], None)
    elif key in sub.custom_fields:
        v = sub.custom_fields.get(key)
    else:
        v = getattr(sub, key, None)
    return str(v).strip() if v not in (None, "") else None


def _parse_date(s: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Tier 1 — typed validators for custom fields
# ---------------------------------------------------------------------------

def _validate_field(spec, value: str) -> Optional[str]:
    """Return an error message, or None if valid."""
    t = spec.type
    if t == "number":
        try:
            n = float(value)
        except ValueError:
            return "must be a number"
        if spec.min is not None and n < spec.min:
            return f"must be at least {spec.min:g}"
        if spec.max is not None and n > spec.max:
            return f"must be at most {spec.max:g}"
    elif t == "date":
        if not _parse_date(value):
            return "must be a date (YYYY-MM-DD)"
    elif t == "email":
        if not EMAIL_RE.match(value):
            return "is not a valid email address"
    elif t == "iban":
        ok, why = iban_is_valid(value)
        if not ok:
            return f"is not a valid IBAN ({why})"
    elif t == "aba":
        ok, why = aba_is_valid(value)
        if not ok:
            return f"is not a valid routing number ({why})"
    elif t == "select":
        if spec.options and value not in spec.options:
            return f"must be one of: {', '.join(spec.options)}"
    elif t == "url":
        if not re.match(r"^https?://[^\s]+\.[^\s]+", value):
            return "must be a valid URL"
    elif t == "phone":
        if not re.match(r"^\+?[\d\s\-()]{7,20}$", value):
            return "is not a valid phone number"
    elif t == "country":
        if not re.fullmatch(r"[A-Za-z]{2}", value):
            return "must be a 2-letter country code"
    if spec.regex and not re.fullmatch(spec.regex, value):
        return f"does not match the required format" + (f" (e.g. {spec.hint})" if spec.hint else "")
    return None


# ---------------------------------------------------------------------------
# Tier 3 — semantic assertions (LLM, escalate-only)
# ---------------------------------------------------------------------------

_SEMANTIC_SYSTEM = (
    "You are a strict compliance validator. You receive a vendor onboarding "
    "submission as JSON and one assertion about it. Reply with EXACTLY one "
    "word on the first line: PASS, FAIL, or UNSURE — then one short sentence "
    "of reasoning. FAIL only if the submission clearly violates the assertion."
)


def _check_semantic(sub: VendorSubmission, assertion: str) -> tuple[str, str]:
    """Returns (verdict PASS/FAIL/UNSURE/NO_MODEL, reason)."""
    from backend.app import config
    from backend.app.llm.client import get_llm

    llm = get_llm()
    if llm.provider == "offline":
        return "NO_MODEL", "No model configured to evaluate this assertion."
    try:
        payload = sub.model_dump_json()
        raw = llm._complete(_SEMANTIC_SYSTEM,
                            f"Assertion: {assertion}\n\nSubmission:\n{payload}", 200)
        first = (raw or "").strip().splitlines()[0].strip().upper()
        reason = " ".join((raw or "").strip().splitlines()[1:])[:300]
        if first.startswith("PASS"):
            return "PASS", reason
        if first.startswith("FAIL"):
            return "FAIL", reason
        return "UNSURE", reason or "Model could not decide."
    except Exception as exc:
        return "UNSURE", f"Model unavailable ({type(exc).__name__})."


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    evaluated: list[dict[str, Any]] = []

    with Timer() as t:
        profile = get_profile(sub.profile_id, sub.country)
        core_keys = {"legal_name", "registration_number", "tax_id", "bank.account_name"}

        # --- Tier 1: typed custom fields (core fields are owned by formats)
        for spec in profile.fields:
            if spec.key in core_keys:
                continue
            value = _get(sub, spec.key)
            if value is None:
                if spec.required:
                    findings.append(finding(
                        FindingCode.CUSTOM_FIELD_INVALID, Severity.NEEDS_INFO, CHECK,
                        message=f"Required field '{spec.label}' was not provided.",
                        field=spec.key,
                        vendor_message=f"{spec.label} is required."
                                       + (f" {spec.hint}" if spec.hint else ""),
                    ))
                continue
            err = _validate_field(spec, value)
            if err:
                findings.append(finding(
                    FindingCode.CUSTOM_FIELD_INVALID, Severity.NEEDS_INFO, CHECK,
                    message=f"'{spec.label}' ({value}) {err}.",
                    field=spec.key,
                    vendor_message=f"The value for {spec.label} ('{value}') {err}. "
                                   f"Please correct it.",
                    value=value,
                ))

        # --- Tier 2: declarative rules
        for rule in profile.rules:
            entry: dict[str, Any] = {"kind": rule.kind}
            if rule.kind == "semantic":
                verdict, reason = _check_semantic(sub, rule.assert_ or "")
                entry.update({"assert": rule.assert_, "verdict": verdict, "reason": reason})
                if verdict in ("FAIL", "UNSURE", "NO_MODEL"):
                    sev = _SEV[rule.on_fail]
                    findings.append(finding(
                        FindingCode.SEMANTIC_RULE_FLAGGED, sev, CHECK,
                        message=(f"Semantic check {'failed' if verdict == 'FAIL' else 'could not be confirmed'}: "
                                 f"\"{rule.assert_}\" — {reason}"),
                        field=None, verdict=verdict, reason=reason,
                    ))
                evaluated.append(entry)
                continue

            a, b = _get(sub, rule.a or ""), _get(sub, rule.b or "")
            entry.update({"a": rule.a, "b": rule.b, "a_value": a, "b_value": b})
            if a is None or b is None:
                entry["result"] = "SKIPPED (missing value)"
                evaluated.append(entry)
                continue

            ok = True
            detail = ""
            if rule.kind == "field_match":
                if rule.mode == "exact":
                    ok = a.strip().lower() == b.strip().lower()
                else:
                    score = name_score(a, b)
                    ok = name_verdict(score) != "MISMATCH"
                    detail = f"similarity {score:.0f}%"
            elif rule.kind == "equals":
                ok = re.sub(r"[\s\-]", "", a).upper() == re.sub(r"[\s\-]", "", b).upper()
            elif rule.kind == "date_before":
                da, db_ = _parse_date(a), _parse_date(b)
                ok = bool(da and db_ and da <= db_)
                detail = f"{a} vs {b}"
            elif rule.kind == "country_consistent":
                ok = a.strip().upper() == b.strip().upper()
            entry["result"] = "PASS" if ok else "FAIL"
            if detail:
                entry["detail"] = detail
            if not ok:
                findings.append(finding(
                    FindingCode.CUSTOM_RULE_FAILED, _SEV[rule.on_fail], CHECK,
                    message=(f"Profile rule failed: {rule.kind}({rule.a}, {rule.b}) — "
                             f"'{a}' vs '{b}'" + (f" ({detail})" if detail else "") + "."),
                    field=rule.a, a=a, b=b, rule=rule.kind,
                ))
            evaluated.append(entry)

    n_rules = len(profile.rules)
    blocking = [f for f in findings if int(f.severity) >= 2]
    if not profile.rules and not any(f.key not in core_keys for f in profile.fields):
        summary = "No custom requirements in this profile."
    elif not findings:
        summary = f"All custom fields valid; {n_rules} profile rule(s) passed."
    else:
        summary = f"{len(blocking)} custom requirement issue(s) found."

    return CheckResult(check=CHECK, label="Profile rules", findings=findings,
                       summary=summary, duration_ms=t.ms,
                       data={"rules_evaluated": evaluated,
                             "profile_id": profile.profile_id})
