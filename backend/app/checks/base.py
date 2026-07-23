"""Shared helpers for the check modules."""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from rapidfuzz import fuzz

from backend.app.models import Finding, FindingCode, Severity
from backend.app.rules import load_common_rules


def finding(code: FindingCode, severity: Severity, check: str, message: str,
            field: Optional[str] = None, vendor_message: Optional[str] = None,
            **evidence: Any) -> Finding:
    return Finding(
        code=code, severity=severity, check=check, field=field,
        message=message, vendor_message=vendor_message, evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Name normalisation and comparison
# ---------------------------------------------------------------------------

def _suffix_pattern() -> re.Pattern:
    sufs = load_common_rules().get("name_matching", {}).get("strip_suffixes", [])
    escaped = "|".join(re.escape(s) for s in sorted(sufs, key=len, reverse=True))
    return re.compile(rf"\b({escaped})\b\.?", re.IGNORECASE)


_SUFFIX_RE = None


def normalise_name(s: Optional[str]) -> str:
    """Strip everything that differs between two spellings of one company.

    Legal-form suffixes are removed because "Kessler GmbH" and "Kessler" are
    the same entity, and a mismatch flagged on that basis is noise that trains
    reviewers to ignore the flag - which is worse than not having it.
    """
    global _SUFFIX_RE
    if _SUFFIX_RE is None:
        _SUFFIX_RE = _suffix_pattern()

    s = (s or "").lower()
    s = s.replace("&", "and")
    s = re.sub(r"[.,'\"()\-/]", " ", s)
    s = _SUFFIX_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_score(a: Optional[str], b: Optional[str]) -> float:
    """0-100 similarity between two entity names."""
    na, nb = normalise_name(a), normalise_name(b)
    if not na or not nb:
        return 0.0
    return float(max(
        fuzz.token_sort_ratio(na, nb),
        fuzz.token_set_ratio(na, nb),
    ))


def name_verdict(score: float) -> str:
    """MATCH | PARTIAL | MISMATCH against the configured bands."""
    nm = load_common_rules().get("name_matching", {})
    if score >= nm.get("strong", 85):
        return "MATCH"
    if score >= nm.get("weak", 60):
        return "PARTIAL"
    return "MISMATCH"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def email_domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].lower().strip()


def is_free_email(email: Optional[str]) -> bool:
    d = email_domain(email)
    return bool(d and d in set(load_common_rules().get("free_email_domains", [])))


class Timer:
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.ms = int((time.perf_counter() - self._t0) * 1000)
