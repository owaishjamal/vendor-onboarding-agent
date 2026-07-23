"""Validate the country rule-pack regexes against real-world examples.

For a system whose literal job is format correctness, the formats themselves
must be tested — otherwise a subtly-wrong regex either adds false friction
(rejects valid vendors) or gives false confidence (accepts malformed IDs), and
nothing catches it. Each case below is a known-valid or known-invalid identifier
for its country; the test asserts the pack's regex agrees.

If a regex here is wrong, the fix is in the YAML pack, not in this test.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.rules import load_country_rules  # noqa: E402


def _regex(country: str, section: str) -> str:
    return load_country_rules(country).get(section, {}).get("regex", "")


# (country, section, value, should_match)
TAX_ID_CASES = [
    # US EIN — 2 digits, hyphen, 7 digits
    ("US", "47-3821990", True),
    ("US", "12-3456789", True),
    ("US", "123456789", False),      # no hyphen
    ("US", "4-73821990", False),     # wrong grouping
    # GB VAT — GB + 9 (or 12) digits
    ("GB", "GB123456789", True),
    ("GB", "GB123456789012", True),
    ("GB", "123456789", False),      # missing GB prefix
    ("GB", "GB12345", False),        # too short
    # DE USt-IdNr — DE + 9 digits
    ("DE", "DE123456789", True),
    ("DE", "DE12345678", False),     # 8 digits
    ("DE", "123456789", False),
    # IN GSTIN — 15 chars, embedded PAN
    ("IN", "27AAPFU0939F1ZV", True),
    ("IN", "27AAPFU0939F1Z", False),  # 14 chars
    ("IN", "AAPFU093927F1ZV", False), # wrong structure
    # SG GST
    ("SG", "200912345K", True),
    ("SG", "M90312345A", True),
    ("SG", "12345", False),
]

REG_CASES = [
    # GB Companies House — 8 digits or 2 letters + 6 digits
    ("GB", "09876543", True),
    ("GB", "SC123456", True),
    ("GB", "1234567", False),        # 7 digits
    # DE Handelsregister — HRA/HRB + digits
    ("DE", "HRB 84721", True),
    ("DE", "HRA123", True),
    ("DE", "HR 12345", False),       # missing A/B
    # IN CIN
    ("IN", "U74999MH2015PTC269898", True),
    ("IN", "X74999MH2015PTC269898", False),  # must start L/U
    # SG UEN
    ("SG", "201512345D", True),
    ("SG", "T05LL1103A", True),
]


@pytest.mark.parametrize("country,value,ok", TAX_ID_CASES,
                         ids=[f"tax-{c}-{v}" for c, v, _ in TAX_ID_CASES])
def test_tax_id_regex(country, value, ok):
    pattern = _regex(country, "tax_id")
    assert pattern, f"no tax_id regex for {country}"
    matched = bool(re.fullmatch(pattern, value))
    assert matched is ok, (
        f"{country} tax_id /{pattern}/ {'should match' if ok else 'should reject'} {value!r}"
    )


@pytest.mark.parametrize("country,value,ok", REG_CASES,
                         ids=[f"reg-{c}-{v}" for c, v, _ in REG_CASES])
def test_registration_regex(country, value, ok):
    pattern = _regex(country, "registration_number")
    assert pattern, f"no registration regex for {country}"
    matched = bool(re.fullmatch(pattern, value))
    assert matched is ok, (
        f"{country} registration /{pattern}/ {'should match' if ok else 'should reject'} {value!r}"
    )
