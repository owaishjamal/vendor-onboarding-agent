"""Tests for client-configurable onboarding: profiles, evidence-first
verification, the custom validation tiers, and the vendor-safe portal view.
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
os.environ["VO_DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_cfg.db")
os.environ["VO_PROFILE_DIR"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_cfg_profiles")
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.models import Severity, VendorSubmission  # noqa: E402
from backend.app.pipeline.runner import assess, run_pipeline  # noqa: E402
from backend.app.profiles.models import (  # noqa: E402
    DocSpec, FieldSpec, RequirementProfile, RuleSpec,
)
from backend.app.profiles.store import (  # noqa: E402
    delete_profile, get_profile, save_profile,
)
from backend.app.storage import cases, db  # noqa: E402

SUBS = ROOT / "data" / "submissions"


@pytest.fixture(autouse=True)
def fresh_db():
    db.reset_db()
    yield


def load(f):
    return VendorSubmission(**json.loads((SUBS / f).read_text()))


# ===========================================================================
# Profiles
# ===========================================================================

def test_default_profile_mirrors_country_packs():
    p = get_profile(None, "GB")
    assert {d.key for d in p.documents} == {"incorporation", "tax_form", "bank_proof"}
    assert any(f.key == "tax_id" and f.evidence for f in p.fields)


def test_profile_save_merge_and_delete():
    delete_profile("t-acme")          # hermetic: ignore anything left behind
    prof = RequirementProfile(
        profile_id="t-acme", name="Acme", extends="country_defaults",
        fields=[FieldSpec(key="fleet_size", label="Fleet size", type="number",
                          required=True, min=1)],
        documents=[DocSpec(key="insurance", label="Insurance certificate",
                           expects="A vehicle insurance certificate naming the business")],
    )
    save_profile(prof)
    try:
        merged = get_profile("t-acme", "GB")
        keys = {d.key for d in merged.documents}
        # custom doc + inherited country docs
        assert "insurance" in keys and "bank_proof" in keys
        assert any(f.key == "fleet_size" for f in merged.fields)
        # versioning bumps on re-save
        v2 = save_profile(prof)
        assert v2.version == 2
    finally:
        delete_profile("t-acme")


def test_unknown_profile_falls_back_to_default():
    p = get_profile("does-not-exist", "US")
    assert p.profile_id == "default"


# ===========================================================================
# Evidence-first field verification
# ===========================================================================

def test_matrix_corroborates_clean_submission():
    _, _, results, _ = assess(load("VS-01_northwind_clean.json"))
    fv = next(r for r in results if r.check == "field_verification")
    outcomes = {r["field"]: r["outcome"] for r in fv.data["matrix"]}
    assert outcomes["tax_id"] == "CORROBORATED"          # matches the W-9
    assert outcomes["bank.account_name"] == "CORROBORATED"


def test_matrix_on_kessler_is_internally_consistent():
    """VS-03's fraud is legal-name vs account-holder (the consistency check's
    finding). The vendor HONESTLY declared 'K. Weber' as the holder, and the
    bank letter confirms it — so claim-vs-evidence is CORROBORATED here, and
    'HRB 84721' must corroborate despite containing a space."""
    _, _, results, _ = assess(load("VS-03_kessler_bank_mismatch.json"))
    fv = next(r for r in results if r.check == "field_verification")
    outcomes = {r["field"]: r["outcome"] for r in fv.data["matrix"]}
    assert outcomes["bank.account_name"] == "CORROBORATED"
    assert outcomes["registration_number"] == "CORROBORATED"
    assert outcomes["legal_name"] == "CORROBORATED"


def test_matrix_contradiction_on_forged_number():
    """A claim that admissible evidence disputes must be CONTRADICTED: form
    says one tax id, the (valid) certificate shows another."""
    base = json.loads((SUBS / "VS-01_northwind_clean.json").read_text())
    base["tax_id"] = "99-9999999"          # differs from the W-9's 47-3821990
    st, _, results, _ = assess(VendorSubmission(**base))
    fv = next(r for r in results if r.check == "field_verification")
    outcomes = {r["field"]: r["outcome"] for r in fv.data["matrix"]}
    assert outcomes["tax_id"] == "CONTRADICTED"
    assert st.value == "PENDING_REVIEW"    # contradicted id-like claim escalates


def test_failed_document_contributes_no_evidence():
    """A wrong/irrelevant document is inadmissible — its fields go UNEVIDENCED."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from backend.app import config

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(60, 780, "Jane Doe — Curriculum Vitae")
    c.drawString(60, 760, "Work Experience: 5 years. Skills: Python. Education: BSc.")
    c.showPage(); c.save()
    up = config.DATA_DIR / "documents" / "uploads" / "cfg_resume"
    up.mkdir(parents=True, exist_ok=True)
    (up / "cv.pdf").write_bytes(buf.getvalue())

    sub = VendorSubmission(
        legal_name="Acme Widgets Ltd", country="GB",
        registration_number="09442817", tax_id="GB123456789",
        bank={"account_name": "Acme Widgets Ltd"},
        documents=[{"doc_type": "bank_proof", "filename": "cv.pdf",
                    "path": "uploads/cfg_resume/cv.pdf"}],
    )
    _, _, results, _ = assess(sub)
    fv = next(r for r in results if r.check == "field_verification")
    outcomes = {r["field"]: r["outcome"] for r in fv.data["matrix"]}
    assert outcomes["bank.account_name"] == "UNEVIDENCED"


# ===========================================================================
# Custom validation tiers
# ===========================================================================

def _with_profile(prof: RequirementProfile):
    save_profile(prof)
    return prof.profile_id


def test_typed_custom_field_validation():
    pid = _with_profile(RequirementProfile(
        profile_id="t-typed", name="T", extends="country_defaults",
        fields=[FieldSpec(key="outlet_count", label="Outlets", type="number",
                          required=True, min=1)],
    ))
    try:
        base = json.loads((SUBS / "VS-01_northwind_clean.json").read_text())
        base["profile_id"] = pid

        # Missing required custom field -> NEEDS_INFO
        st, findings, _, _ = assess(VendorSubmission(**base))
        assert any(f.code.value == "CUSTOM_FIELD_INVALID" for f in findings)
        assert st.value == "PENDING_INFO"

        # Invalid value -> NEEDS_INFO
        base["custom_fields"] = {"outlet_count": "zero"}
        _, findings, _, _ = assess(VendorSubmission(**base))
        assert any("must be a number" in f.message for f in findings)

        # Valid value -> clean again
        base["custom_fields"] = {"outlet_count": "12"}
        st, findings, _, _ = assess(VendorSubmission(**base))
        assert not any(f.code.value == "CUSTOM_FIELD_INVALID" for f in findings)
        assert st.value == "APPROVED"
    finally:
        delete_profile("t-typed")


def test_declarative_rule_fails_and_escalates():
    pid = _with_profile(RequirementProfile(
        profile_id="t-rule", name="T", extends="country_defaults",
        rules=[RuleSpec(kind="field_match", a="legal_name", b="trading_name",
                        mode="exact", on_fail="NEEDS_REVIEW")],
    ))
    try:
        base = json.loads((SUBS / "VS-01_northwind_clean.json").read_text())
        base["profile_id"] = pid
        base["trading_name"] = "Completely Different Co"
        st, findings, _, _ = assess(VendorSubmission(**base))
        assert any(f.code.value == "CUSTOM_RULE_FAILED" for f in findings)
        assert st.value == "PENDING_REVIEW"
    finally:
        delete_profile("t-rule")


def test_semantic_rule_escalates_offline():
    """No model configured -> the assertion can't be evaluated -> escalate.
    An unevaluated control is never grounds for approval."""
    pid = _with_profile(RequirementProfile(
        profile_id="t-sem", name="T", extends="country_defaults",
        rules=[RuleSpec(kind="semantic", assert_="Outlet count must be plausible",
                        on_fail="NEEDS_REVIEW")],
    ))
    try:
        base = json.loads((SUBS / "VS-01_northwind_clean.json").read_text())
        base["profile_id"] = pid
        st, findings, _, _ = assess(VendorSubmission(**base))
        assert any(f.code.value == "SEMANTIC_RULE_FLAGGED" for f in findings)
        assert st.value == "PENDING_REVIEW"
    finally:
        delete_profile("t-sem")


# ===========================================================================
# Vendor portal view (disclosure by construction)
# ===========================================================================

def _run(f):
    events = list(run_pipeline(load(f)))
    return [e for e in events if e["type"] == "done"][0]["case"]


def test_vendor_view_shows_only_safe_content():
    c = _run("VS-02_brightline_incomplete.json")     # PENDING_INFO
    v = cases.vendor_view(c["vendor_token"])
    assert v["action_needed"] is True
    assert v["items"]                                  # the requested items
    blob = json.dumps(v)
    # No internal vocabulary can appear in the vendor view.
    for word in ("NEEDS_REVIEW", "DENIED_PARTY", "finding", "severity", "screening"):
        assert word not in blob, f"vendor view leaked internal term: {word}"


def test_vendor_view_hides_reasons_for_review_and_rejection():
    c = _run("VS-03_kessler_bank_mismatch.json")      # PENDING_REVIEW
    v = cases.vendor_view(c["vendor_token"])
    assert v["items"] == []                            # nothing disclosed
    assert "review" in v["status_message"].lower()

    c = _run("VS-06_volkov_denied_party.json")        # REJECTED
    v = cases.vendor_view(c["vendor_token"])
    assert v["items"] == []
    assert "unable" in v["status_message"].lower()
    assert "sanction" not in json.dumps(v).lower()


def test_vendor_token_is_unique_per_case_and_invalid_token_404s():
    a = _run("VS-01_northwind_clean.json")
    b = _run("VS-03_kessler_bank_mismatch.json")
    assert a["vendor_token"] != b["vendor_token"]
    assert cases.vendor_view("not-a-real-token") is None
