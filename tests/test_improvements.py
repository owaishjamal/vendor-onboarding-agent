"""Tests for the second-round improvements.

Real document reading, two-factor screening, resubmission handling, and
reviewer actions. These are separate from the golden-case file so the original
behaviour and the new behaviour can be seen (and broken) independently.
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
os.environ["VO_DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_improve.db")
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.checks.document_reader import read_document  # noqa: E402
from backend.app.models import Status, SubmittedDocument, VendorSubmission  # noqa: E402
from backend.app.pipeline.runner import run_pipeline  # noqa: E402
from backend.app.storage import cases, db  # noqa: E402

SUBS = ROOT / "data" / "submissions"


@pytest.fixture(autouse=True)
def fresh_db():
    db.reset_db()
    yield


def load(f):
    return VendorSubmission(**json.loads((SUBS / f).read_text()))


def run(f):
    events = list(run_pipeline(load(f)))
    return [e for e in events if e["type"] == "done"][0]["case"]


def codes(case, min_sev=2):
    return {x["code"] for x in case["findings"] if x["severity"] >= min_sev}


# ===========================================================================
# Real document reading
# ===========================================================================

def test_documents_are_read_from_real_files():
    """The fixtures point at real rendered files, and they are parsed for real."""
    sub = load("VS-01_northwind_clean.json")
    assert sub.documents and all(d.path for d in sub.documents)
    read = read_document(sub.documents[0])
    assert read.source == "text_layer"
    assert read.confidence >= 0.9
    assert read.fields.get("name")           # actually extracted a name


def test_scanned_document_is_read_by_ocr():
    """Pinnacle's bank letter is an image — it must go down the OCR path."""
    sub = load("VS-07_pinnacle_iban_typo.json")
    scan = [d for d in sub.documents if d.filename.endswith(".png")]
    assert scan, "expected a scanned (.png) document in VS-07"
    read = read_document(scan[0])
    assert read.source == "ocr"
    assert read.confidence < 0.9              # discounted relative to a clean PDF
    assert read.fields.get("name")            # OCR still recovered the holder


def test_bank_document_names_the_account_holder_not_the_company():
    """VS-03's bank letter really says 'K. Weber' — reading it proves the point."""
    sub = load("VS-03_kessler_bank_mismatch.json")
    bank = [d for d in sub.documents if "bank" in d.filename or "bestaet" in d.filename]
    read = read_document(bank[0])
    assert "weber" in (read.fields.get("name", "").lower())


def test_pasted_submission_without_files_still_works():
    """No file on disk → fall back to the provided field block, full confidence."""
    doc = SubmittedDocument(doc_type="tax_form", filename="x.pdf",
                            extracted={"kind": "w9", "legal_name": "Acme Inc",
                                       "ein": "12-3456789"})
    read = read_document(doc)
    assert read.source == "provided"
    assert read.confidence == 1.0
    assert read.fields["name"] == "Acme Inc"


# ===========================================================================
# Two-factor screening
# ===========================================================================

def test_namesake_is_cleared_by_date_of_birth():
    """Exact name match to a sanctioned party, different DOB → cleared, approved."""
    c = run("VS-08_meridian_namesake.json")
    assert c["status"] == Status.APPROVED.value
    assert "DENIED_PARTY_MATCH" not in codes(c)
    assert "DENIED_PARTY_NEAR_MATCH" not in codes(c)
    # It is recorded, just at advisory severity.
    advisory = [f for f in c["findings"] if f["code"] == "DENIED_PARTY_NEAR_MATCH"]
    assert advisory and advisory[0]["severity_name"] == "ADVISORY"


def test_confirmed_hit_still_rejects():
    """Matching DOB confirms the hit — VS-06 stays rejected."""
    c = run("VS-06_volkov_denied_party.json")
    assert c["status"] == Status.REJECTED.value
    hit = [f for f in c["findings"] if f["code"] == "DENIED_PARTY_MATCH"]
    assert hit
    assert "secondary" in hit[0]["evidence"]


# ===========================================================================
# Resubmission handling
# ===========================================================================

def test_resubmission_supersedes_prior_and_diffs_findings():
    first = run("VS-02_brightline_incomplete.json")
    assert first["status"] == Status.PENDING_INFO.value
    assert first["revision"] == 1

    second = run("VS-09_brightline_resubmitted.json")
    assert second["status"] == Status.APPROVED.value
    assert second["revision"] == 2
    assert second["supersedes"] == first["case_id"]

    diff = second["change_summary"]
    assert diff is not None
    assert "MISSING_REQUIRED_FIELD" in diff["resolved"]
    assert "MISSING_REQUIRED_DOCUMENT" in diff["resolved"]
    assert diff["remaining"] == [] and diff["new"] == []

    # The prior case is now marked superseded.
    prior = cases.get_case(first["case_id"])
    assert prior["superseded_by"] == second["case_id"]


def test_unrelated_vendors_are_not_linked():
    a = run("VS-01_northwind_clean.json")
    b = run("VS-03_kessler_bank_mismatch.json")
    assert b["supersedes"] is None
    assert b["revision"] == 1


# ===========================================================================
# Reviewer actions
# ===========================================================================

def test_reviewer_can_approve_a_pending_case_and_it_is_logged():
    c = run("VS-03_kessler_bank_mismatch.json")
    assert c["status"] == Status.PENDING_REVIEW.value

    result = cases.record_action(c["case_id"], "approve",
                                 reviewer="tohid", note="Confirmed by phone")
    assert result["new_status"] == "APPROVED_BY_REVIEWER"

    after = cases.get_case(c["case_id"])
    assert after["status"] == "APPROVED_BY_REVIEWER"
    assert after["resolution"] == "APPROVED"
    assert len(after["actions"]) == 1
    assert after["actions"][0]["reviewer"] == "tohid"
    assert after["actions"][0]["note"] == "Confirmed by phone"


def test_reviewer_reject_is_recorded():
    c = run("VS-04_sundara_country_mismatch.json")
    cases.record_action(c["case_id"], "reject", reviewer="tohid", note="Could not verify")
    after = cases.get_case(c["case_id"])
    assert after["status"] == "REJECTED_BY_REVIEWER"
    assert after["resolution"] == "REJECTED"


def test_unknown_action_is_rejected():
    c = run("VS-01_northwind_clean.json")
    with pytest.raises(ValueError):
        cases.record_action(c["case_id"], "banana", reviewer="x", note=None)


def test_action_log_is_append_only_across_multiple_actions():
    c = run("VS-03_kessler_bank_mismatch.json")
    cases.record_action(c["case_id"], "request_info", reviewer="a", note="need DOB")
    cases.record_action(c["case_id"], "approve", reviewer="b", note="cleared")
    after = cases.get_case(c["case_id"])
    assert len(after["actions"]) == 2
    assert [a["action"] for a in after["actions"]] == ["request_info", "approve"]


# ===========================================================================
# Registry verification
# ===========================================================================

def test_fabricated_vendor_fails_registry_and_does_not_approve():
    """The case internal consistency can't catch — only external verification."""
    c = run("VS-11_fabricated_vendor.json")
    assert c["status"] == Status.PENDING_REVIEW.value
    assert "REGISTRY_NOT_FOUND" in codes(c)


def test_real_company_is_registry_verified():
    c = run("VS-01_northwind_clean.json")
    assert c["status"] == Status.APPROVED.value
    verified = [f for f in c["findings"] if f["code"] == "REGISTRY_VERIFIED"]
    assert verified, "a known company should be positively verified"


def test_registry_check_runs_for_every_case():
    for f in ("VS-01_northwind_clean.json", "VS-06_volkov_denied_party.json"):
        c = run(f)
        assert any(ch["check"] == "registry" for ch in c["checks"])


# ===========================================================================
# Subtle name fraud (the volume-eval regression)
# ===========================================================================

def test_subtle_related_account_name_is_caught():
    """'<Company> Holdings' scores high but is a distinct entity — must escalate."""
    c = run("VS-10_harbourstone_related_account.json")
    assert c["status"] == Status.PENDING_REVIEW.value
    f = [x for x in c["findings"] if x["code"] == "BANK_NAME_MISMATCH"]
    assert f and f[0]["severity_name"] == "NEEDS_REVIEW"
    assert f[0]["evidence"].get("added_tokens")


def test_added_token_detection_handles_duplicate_tokens():
    """Multiset diff: an added word that already appears once must still count."""
    from backend.app.checks.consistency import _added_entity_tokens
    assert _added_entity_tokens("Acme Trading Co Ltd", "Acme Trading Co Trading Ltd") == {"trading"}
    # A legitimate suffix-only difference adds nothing.
    assert _added_entity_tokens("Acme Trading Co Ltd", "Acme Trading Co Limited") == set()




