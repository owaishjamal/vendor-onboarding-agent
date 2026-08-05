"""AI confidence score and the routing it drives.

The rule these protect: confidence can move a case TOWARDS a human, never away
from one. A low score can block an auto-approval; it can never turn a flagged
case into an approval.
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
os.environ["VO_DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_conf.db")
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.models import Finding, FindingCode, Severity, Status, VendorSubmission  # noqa: E402
from backend.app.pipeline import confidence  # noqa: E402
from backend.app.pipeline.runner import assess  # noqa: E402
from backend.app.storage import db  # noqa: E402

SUBS = ROOT / "data" / "submissions"


@pytest.fixture(autouse=True)
def fresh_db():
    db.reset_db()
    yield


def load(f):
    return VendorSubmission(**json.loads((SUBS / f).read_text()))


def _f(code: str, sev=Severity.NEEDS_REVIEW):
    return Finding(code=FindingCode(code), severity=sev, check="t", message="m")


# ===========================================================================
# Scoring
# ===========================================================================

def test_clean_submission_scores_high_enough_to_auto_approve():
    st, _, _, conf = assess(load("VS-01_northwind_clean.json"))
    assert st is Status.APPROVED
    assert conf["score"] >= 0.85
    assert conf["recommendation"] if "recommendation" in conf else True


def test_score_is_explainable():
    _, _, _, conf = assess(load("VS-01_northwind_clean.json"))
    assert set(conf["components"]) == {
        "document_read", "document_classification", "form_corroboration", "certainty"}
    assert conf["reasons"] and conf["decision_reason"]


def test_unreadable_documents_lower_confidence():
    """Same vendor, but the documents can't be read -> less certainty."""
    good = assess(load("VS-01_northwind_clean.json"))[3]["score"]
    sub = load("VS-01_northwind_clean.json")
    for d in sub.documents:
        d.path = None
        d.extracted = {}
        d.readable = False
    weak = assess(sub)[3]["score"]
    assert weak < good


# ===========================================================================
# Routing
# ===========================================================================

def test_high_confidence_and_clean_auto_approves():
    st, why = confidence.route(Status.APPROVED, 0.95, [], 0.85)
    assert st is Status.APPROVED and "Auto-approved" in why


def test_low_confidence_blocks_auto_approval():
    """Nothing is wrong, but the evidence is too weak to decide alone."""
    st, why = confidence.route(Status.APPROVED, 0.60, [], 0.85)
    assert st is Status.PENDING_REVIEW
    assert "below" in why


def test_denied_party_auto_rejects_regardless_of_score():
    st, why = confidence.route(Status.REJECTED, 0.10,
                               [_f("DENIED_PARTY_MATCH", Severity.REJECT)], 0.85)
    assert st is Status.REJECTED and "Auto-rejected" in why


def test_high_confidence_cannot_clear_a_review_finding():
    """The invariant: confidence never turns a flagged case into an approval."""
    st, _ = confidence.route(Status.PENDING_REVIEW, 0.99,
                             [_f("BANK_NAME_MISMATCH")], 0.85)
    assert st is Status.PENDING_REVIEW


def test_pending_info_is_unaffected_by_confidence():
    st, _ = confidence.route(Status.PENDING_INFO, 0.99, [], 0.85)
    assert st is Status.PENDING_INFO


@pytest.mark.parametrize("status,expected", [
    (Status.APPROVED, "Approve"),
    (Status.REJECTED, "Reject"),
    (Status.PENDING_INFO, "Request more information"),
    (Status.PENDING_REVIEW, "Manual review"),
])
def test_recommendation_wording(status, expected):
    assert confidence.recommendation(status) == expected


# ===========================================================================
# End to end
# ===========================================================================

def test_every_case_carries_a_score_and_a_reason():
    for f in ("VS-01_northwind_clean.json", "VS-03_kessler_bank_mismatch.json",
              "VS-06_volkov_denied_party.json"):
        _, _, _, conf = assess(load(f))
        assert 0.0 <= conf["score"] <= 1.0
        assert conf["decision_reason"]
        assert conf["severity_status"]
