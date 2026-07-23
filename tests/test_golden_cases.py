"""Golden-case tests plus unit tests for the two checksum algorithms.

The golden cases pin the *outcome* of each submission. The unit tests pin the
*algorithms* underneath, because an IBAN validator that silently starts
accepting everything would still let every golden case pass — the happy-path
IBANs would validate, and the one broken IBAN would simply stop being caught
in a way that looks like an outcome change rather than a maths bug.

Run:  pytest -q
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["VO_DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_pytest.db")
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.checks.formats import aba_is_valid, iban_is_valid  # noqa: E402
from backend.app.models import Severity, Status, VendorSubmission  # noqa: E402
from backend.app.pipeline.runner import (  # noqa: E402
    build_vendor_items, decide, run_pipeline,
)
from backend.app.storage import db  # noqa: E402

SUBS = ROOT / "data" / "submissions"
MANIFEST = json.loads((SUBS / "manifest.json").read_text())


@pytest.fixture(autouse=True)
def fresh_db():
    db.reset_db()
    yield


def load(filename: str) -> VendorSubmission:
    return VendorSubmission(**json.loads((SUBS / filename).read_text()))


def run(filename: str) -> dict:
    events = list(run_pipeline(load(filename)))
    errors = [e for e in events if e["type"] == "error"]
    assert not errors, f"pipeline errored: {errors}"
    done = [e for e in events if e["type"] == "done"]
    assert done, "pipeline produced no case"
    return done[0]["case"]


def codes(case: dict, min_sev: int = 2) -> set[str]:
    return {f["code"] for f in case["findings"] if f["severity"] >= min_sev}


# ===========================================================================
# Golden cases
# ===========================================================================

@pytest.mark.parametrize("case", MANIFEST, ids=[c["submission_id"] for c in MANIFEST])
def test_expected_status(case):
    c = run(case["file"])
    assert c["status"] == case["expected_status"], (
        f"{case['submission_id']}: expected {case['expected_status']}, "
        f"got {c['status']} — findings {sorted(codes(c))}"
    )


def test_every_case_produces_a_reviewer_summary():
    for case in MANIFEST:
        c = run(case["file"])
        assert c["reviewer_summary"].strip(), f"{case['submission_id']} has no summary"


def test_all_checks_always_run():
    """No early exit — even a rejected case runs every check.

    This is the structural difference from an invoice pipeline and the reason
    a vendor can be told everything at once.
    """
    from backend.app.pipeline.runner import CHECKS
    expected = len(CHECKS)
    for case in MANIFEST:
        c = run(case["file"])
        assert len(c["checks"]) == expected, (
            f"{case['submission_id']} ran {len(c['checks'])} checks, expected {expected}"
        )


# ===========================================================================
# Individual scenarios
# ===========================================================================

def test_clean_submission_approves_with_no_blocking_findings():
    c = run("VS-01_northwind_clean.json")
    assert c["status"] == Status.APPROVED.value
    blocking = [f for f in c["findings"] if f["severity"] >= int(Severity.NEEDS_INFO)]
    assert not blocking, f"unexpected blocking findings: {blocking}"
    assert c["vendor_email"] is None


def test_incomplete_submission_lists_everything_in_one_email():
    """The core anti-round-trip property."""
    c = run("VS-02_brightline_incomplete.json")
    assert c["status"] == Status.PENDING_INFO.value
    assert c["vendor_email"], "a PENDING_INFO case must produce a vendor email"

    needs_info = [f for f in c["findings"] if f["severity_name"] == "NEEDS_INFO"]
    assert len(needs_info) >= 3, "expected the VAT number and two documents"

    # Every vendor-fixable item must actually appear in the email. If any is
    # dropped, the vendor comes back with a partial fix and we round-trip again.
    for f in needs_info:
        assert f["vendor_message"], f"{f['code']} has no vendor-facing text"
        assert f["vendor_message"] in c["vendor_email"], (
            f"{f['code']} was found but never made it into the email"
        )


def test_bank_name_mismatch_escalates_and_is_not_disclosed():
    c = run("VS-03_kessler_bank_mismatch.json")
    assert c["status"] == Status.PENDING_REVIEW.value
    assert "BANK_NAME_MISMATCH" in codes(c)
    # Never email a vendor while investigating who owns their bank account.
    assert c["vendor_email"] is None

    f = next(x for x in c["findings"] if x["code"] == "BANK_NAME_MISMATCH")
    assert f["severity_name"] == "NEEDS_REVIEW"
    assert f["vendor_message"] is None, "consistency findings must not be vendor-facing"


def test_country_contradiction_mixes_severities_and_takes_the_max():
    """A UK VAT number on an Indian vendor produces two findings.

    TAX_ID_FORMAT_INVALID is vendor-fixable (NEEDS_INFO); TAX_ID_COUNTRY_MISMATCH
    needs a human (NEEDS_REVIEW). The status must come from the higher one.
    """
    c = run("VS-04_sundara_country_mismatch.json")
    assert c["status"] == Status.PENDING_REVIEW.value
    found = codes(c)
    assert "TAX_ID_COUNTRY_MISMATCH" in found
    assert "TAX_ID_FORMAT_INVALID" in found

    sevs = {f["code"]: f["severity_name"] for f in c["findings"]}
    assert sevs["TAX_ID_FORMAT_INVALID"] == "NEEDS_INFO"
    assert sevs["TAX_ID_COUNTRY_MISMATCH"] == "NEEDS_REVIEW"
    # The lower-severity item is suppressed, not lost.
    assert c["vendor_email"] is None


def test_shared_bank_account_is_caught_and_never_auto_rejected():
    """Only detectable relative to the vendor master, and never terminal."""
    c = run("VS-05_continental_shared_account.json")
    assert c["status"] == Status.PENDING_REVIEW.value
    assert "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR" in codes(c)
    assert c["status"] != Status.REJECTED.value, (
        "shared accounts have legitimate explanations (group treasury, factoring) "
        "and must not auto-reject"
    )
    f = next(x for x in c["findings"] if x["code"] == "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR")
    assert f["evidence"]["existing_vendor_id"] == "V-2001"


def test_denied_party_rejects_and_suppresses_all_vendor_contact():
    """The disclosure rule. A rejected case emails nobody, ever."""
    c = run("VS-06_volkov_denied_party.json")
    assert c["status"] == Status.REJECTED.value
    assert "DENIED_PARTY_MATCH" in codes(c)

    # This submission is ALSO missing a bank document — a perfectly ordinary,
    # vendor-fixable finding. It must not generate an email regardless.
    assert "MISSING_REQUIRED_DOCUMENT" in codes(c)
    assert c["vendor_email"] is None, (
        "a sanctions-rejected vendor must never receive correspondence, even for "
        "an unrelated missing document"
    )


def test_iban_typo_goes_to_the_vendor_not_to_a_reviewer():
    """Contrast with VS-03: also a banking problem, but a mistake, not a signal."""
    c = run("VS-07_pinnacle_iban_typo.json")
    assert c["status"] == Status.PENDING_INFO.value
    assert "IBAN_CHECKSUM_FAILED" in codes(c)
    assert c["vendor_email"], "a correctable typo should be raised with the vendor"
    assert "IBAN" in c["vendor_email"]


# ===========================================================================
# Disclosure rule, directly
# ===========================================================================

@pytest.mark.parametrize("status,expect_items", [
    (Status.PENDING_INFO, True),
    (Status.PENDING_REVIEW, False),
    (Status.REJECTED, False),
    (Status.APPROVED, False),
])
def test_vendor_items_only_ever_built_for_pending_info(status, expect_items):
    from backend.app.models import Finding, FindingCode
    findings = [Finding(
        code=FindingCode.MISSING_REQUIRED_DOCUMENT, severity=Severity.NEEDS_INFO,
        check="completeness", message="internal", vendor_message="Please send X.",
    )]
    items = build_vendor_items(findings, status)
    assert bool(items) is expect_items


# ===========================================================================
# Decision aggregation
# ===========================================================================

def test_status_is_the_maximum_severity_present():
    from backend.app.models import Finding, FindingCode

    def f(sev):
        return Finding(code=FindingCode.MISSING_REQUIRED_FIELD, severity=sev,
                       check="t", message="m")

    assert decide([]) is Status.APPROVED
    assert decide([f(Severity.ADVISORY)]) is Status.APPROVED
    assert decide([f(Severity.ADVISORY), f(Severity.NEEDS_INFO)]) is Status.PENDING_INFO
    assert decide([f(Severity.NEEDS_INFO), f(Severity.NEEDS_REVIEW)]) is Status.PENDING_REVIEW
    assert decide([f(Severity.NEEDS_INFO), f(Severity.REJECT)]) is Status.REJECTED
    # Order must not matter — it is a max, not a fold with side effects.
    assert decide([f(Severity.REJECT), f(Severity.NEEDS_INFO)]) is Status.REJECTED


# ===========================================================================
# Checksum algorithms
# ===========================================================================

@pytest.mark.parametrize("iban", [
    "GB29NWBK60161331926819",   # canonical ISO 13616 example
    "DE89370400440532013000",   # canonical German example
    "GB18BARC20035387143214",
])
def test_valid_ibans_pass(iban):
    ok, reason = iban_is_valid(iban)
    assert ok, f"{iban} should be valid but failed: {reason}"


@pytest.mark.parametrize("iban,why", [
    ("GB29NWBK60611331926819", "transposed digits break the check digits"),
    ("GB28NWBK60161331926819", "wrong check digits"),
    ("GB29NWBK6016133192681", "too short for GB"),
    ("XX29NWBK60161331926819", "unknown country still fails mod-97"),
    ("", "empty"),
])
def test_invalid_ibans_fail(iban, why):
    ok, _ = iban_is_valid(iban)
    assert not ok, f"{iban} should have failed ({why})"


def test_iban_accepts_spaced_formatting():
    """Banks print IBANs in groups of four; users paste them that way."""
    ok, _ = iban_is_valid("GB29 NWBK 6016 1331 9268 19")
    assert ok


@pytest.mark.parametrize("routing", ["021000021", "121000031", "011000015"])
def test_valid_aba_routing_numbers_pass(routing):
    ok, reason = aba_is_valid(routing)
    assert ok, f"{routing} should be valid: {reason}"


@pytest.mark.parametrize("routing", ["021000022", "12345678", "1234567890", "abcdefghi"])
def test_invalid_aba_routing_numbers_fail(routing):
    ok, _ = aba_is_valid(routing)
    assert not ok


# ===========================================================================
# Name matching bands
# ===========================================================================

@pytest.mark.parametrize("a,b,expect", [
    ("Kessler Industrietechnik GmbH", "Kessler Industrietechnik", "MATCH"),
    ("Brightline Analytics Ltd", "Brightline Analytics Limited", "MATCH"),
    ("Northwind Components Inc", "Northwind Components Inc.", "MATCH"),
    ("Kessler Industrietechnik GmbH", "K. Weber Privatkonto", "MISMATCH"),
    ("Acme Trading Ltd", "Zenith Holdings Plc", "MISMATCH"),
])
def test_name_matching_bands(a, b, expect):
    """Legal-form suffixes must never on their own create a mismatch."""
    from backend.app.checks.base import name_score, name_verdict
    assert name_verdict(name_score(a, b)) == expect, (
        f"{a!r} vs {b!r} scored {name_score(a, b):.0f}"
    )


# ===========================================================================
# Robustness
# ===========================================================================

def test_unsupported_country_never_approves():
    """We cannot validate what we have no rules for, so we must not approve it."""
    sub = load("VS-01_northwind_clean.json")
    sub.country = "ZZ"
    c = [e for e in run_pipeline(sub) if e["type"] == "done"][0]["case"]
    assert c["status"] != Status.APPROVED.value
    assert "UNSUPPORTED_COUNTRY" in codes(c)


def test_empty_submission_does_not_crash():
    sub = VendorSubmission()
    events = list(run_pipeline(sub))
    assert not [e for e in events if e["type"] == "error"]
    c = [e for e in events if e["type"] == "done"][0]["case"]
    assert c["status"] != Status.APPROVED.value
