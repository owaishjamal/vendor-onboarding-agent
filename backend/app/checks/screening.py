"""Denied-party screening.

Two bands, and the gap between them is the whole point.

    Confident match  -> REJECT. Paying a sanctioned party is a criminal
                        offence in most jurisdictions. This is one of the very
                        few places where an automated system should say no
                        outright rather than asking a human, because there is
                        no commercial judgement to make.

    Near match       -> NEEDS_REVIEW. Names are not unique. Rejecting a
                        legitimate supplier because a director shares a
                        surname with someone on a list is a real and common
                        failure, and it is expensive in a different way.

Screening runs against name and aliases for the entity and every named
director, because sanctions attach to people as well as companies and the
company name alone will miss a designated individual behind a clean shell.
"""

from __future__ import annotations

import json
from typing import Any

from rapidfuzz import fuzz

from backend.app import config
from backend.app.checks.base import Timer, finding, normalise_name
from backend.app.models import (
    CheckResult, DeniedParty, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.providers.screening_provider import get_screening_provider
from backend.app.rules import load_common_rules

CHECK = "screening"


def _load_list() -> list[DeniedParty]:
    return get_screening_provider().candidates()


def _best_match(name: str, parties: list[DeniedParty]) -> tuple[float, DeniedParty | None, str]:
    """Highest similarity between `name` and any listed name or alias."""
    target = normalise_name(name)
    if not target:
        return 0.0, None, ""
    best, who, matched_on = 0.0, None, ""
    for p in parties:
        for candidate in [p.name, *p.aliases]:
            s = max(
                fuzz.token_sort_ratio(target, normalise_name(candidate)),
                fuzz.token_set_ratio(target, normalise_name(candidate)),
            )
            if s > best:
                best, who, matched_on = float(s), p, candidate
    return best, who, matched_on


def _secondary(person_dob, person_nat, party) -> tuple[str, list[str]]:
    """Compare secondary identifiers (DOB, nationality) against a list entry.

    Returns (verdict, reasons):
      CONFIRM  - a secondary identifier matches the listed party.
      CLEAR    - a secondary identifier CONTRADICTS the listed party (different
                 person with the same name).
      UNKNOWN  - not enough secondary data to decide either way.
    """
    reasons: list[str] = []
    confirm = False
    clear = False

    if person_dob and party.dob:
        if person_dob == party.dob:
            confirm = True
            reasons.append(f"date of birth matches ({person_dob})")
        else:
            clear = True
            reasons.append(f"date of birth differs ({person_dob} vs listed {party.dob})")

    if person_nat and party.nationality:
        if person_nat.upper() == party.nationality.upper():
            confirm = True
            reasons.append(f"nationality matches ({person_nat})")
        else:
            # Nationality alone contradicting is weaker than DOB; note it but
            # don't let it clear a DOB confirmation.
            reasons.append(f"nationality differs ({person_nat} vs listed {party.nationality})")
            if not confirm:
                clear = True

    if confirm:
        return "CONFIRM", reasons
    if clear:
        return "CLEAR", reasons
    return "UNKNOWN", reasons


def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    parties = _load_list()
    cfg = load_common_rules().get("denied_party_screening", {})
    hit = cfg.get("match_threshold", 88)
    near = cfg.get("near_match_threshold", 76)

    # Index structured people by name so a name hit can pull the DOB/nationality
    # the vendor supplied for that individual.
    people = {p.name.strip().lower(): p for p in sub.director_details}

    screened: list[dict[str, Any]] = []

    with Timer() as t:
        subjects: list[tuple[str, str]] = []
        if sub.legal_name:
            subjects.append(("entity", sub.legal_name))
        if sub.trading_name and sub.trading_name != sub.legal_name:
            subjects.append(("trading_name", sub.trading_name))
        for d in sub.directors:
            subjects.append(("director", d))
        # The bank account holder is screened too: a clean company paying into
        # an account held by a designated individual is precisely the structure
        # screening exists to catch.
        if sub.bank.account_name:
            subjects.append(("bank_account_holder", sub.bank.account_name))

        for role, name in subjects:
            score, party, matched_on = _best_match(name, parties)
            person = people.get(name.strip().lower())
            sec_verdict, sec_reasons = ("UNKNOWN", [])
            if party and score >= near:
                sec_verdict, sec_reasons = _secondary(
                    getattr(person, "dob", None), getattr(person, "nationality", None), party)

            entry = {"role": role, "name": name, "score": round(score, 1),
                     "list": party.list_name if party else None,
                     "matched_on": matched_on if party else None,
                     "secondary": sec_verdict, "secondary_reasons": sec_reasons}
            screened.append(entry)

            if not party or score < near:
                continue

            # A supplied secondary identifier that CONTRADICTS the listed party
            # clears the hit outright — this is the innocent-namesake case, and
            # is the entire reason two-factor screening exists.
            if sec_verdict == "CLEAR":
                findings.append(finding(
                    FindingCode.DENIED_PARTY_NEAR_MATCH, Severity.ADVISORY, CHECK,
                    message=(
                        f"{role.replace('_', ' ').title()} '{name}' shares a name with "
                        f"'{party.name}' on {party.list_name} ({score:.0f}%), but "
                        f"{' and '.join(sec_reasons)} — a different individual. Recorded and "
                        f"cleared; no action needed."
                    ),
                    field=role, subject=name, matched_name=party.name,
                    list_name=party.list_name, score=round(score, 1),
                    secondary="CLEAR", secondary_reasons=sec_reasons,
                ))
                continue

            # A name at or above the confirm threshold, OR a near match that a
            # secondary identifier confirms, is a hit.
            confirmed = score >= hit or sec_verdict == "CONFIRM"
            if confirmed:
                extra = (f" Confirmed on secondary identifiers: {'; '.join(sec_reasons)}."
                         if sec_verdict == "CONFIRM" else "")
                findings.append(finding(
                    FindingCode.DENIED_PARTY_MATCH, Severity.REJECT, CHECK,
                    message=(
                        f"{role.replace('_', ' ').title()} '{name}' matches '{party.name}' on "
                        f"the {party.list_name} denied-party list at {score:.0f}% confidence."
                        f"{extra} This vendor cannot be onboarded. Escalate to compliance — do "
                        f"not contact the vendor to discuss the match."
                    ),
                    field=role, subject=name, matched_name=party.name, matched_on=matched_on,
                    list_name=party.list_name, score=round(score, 1),
                    secondary=sec_verdict, secondary_reasons=sec_reasons,
                ))
            else:
                # Near match, no secondary data to resolve it. Human decides.
                findings.append(finding(
                    FindingCode.DENIED_PARTY_NEAR_MATCH, Severity.NEEDS_REVIEW, CHECK,
                    message=(
                        f"{role.replace('_', ' ').title()} '{name}' is a possible match for "
                        f"'{party.name}' on {party.list_name} ({score:.0f}%), below the "
                        f"confirm threshold of {hit}%, and no date of birth or nationality was "
                        f"supplied to resolve it. A compliance reviewer must confirm or clear "
                        f"it against those identifiers before this proceeds."
                    ),
                    field=role, subject=name, matched_name=party.name, matched_on=matched_on,
                    list_name=party.list_name, score=round(score, 1),
                    secondary="UNKNOWN",
                ))

    top = max((s["score"] for s in screened), default=0.0)
    summary = (f"{len(screened)} name(s) screened against {len(parties)} listed parties; "
               f"no matches (highest similarity {top:.0f}%)." if not findings
               else f"{len(findings)} denied-party finding(s) across {len(screened)} names screened.")

    return CheckResult(check=CHECK, label="Denied-party screening", findings=findings,
                       summary=summary, duration_ms=t.ms,
                       data={"screened": screened, "list_size": len(parties),
                             "match_threshold": hit, "near_match_threshold": near})
