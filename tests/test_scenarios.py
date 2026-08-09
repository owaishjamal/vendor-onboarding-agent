"""The demonstrable scenarios must keep reaching the verdicts they claim.

A prefill that quietly stops producing its advertised outcome is worse than no
prefill: someone demos it, the verdict is wrong, and nobody notices because
the label still says the right thing. Every scenario states its expected
status in `scenarios.py`, and these tests run the real pipeline and hold it to
that claim.

They also assert WHY, not just the status. `shared-bank-account` reaching
PENDING_REVIEW is only interesting if it got there on the shared-account
finding — if it started failing for a missing document instead, the status
would still pass while the scenario stopped demonstrating anything.
"""

from __future__ import annotations

import pytest

from backend.app.models import Severity, VendorSubmission
from backend.app.pipeline.runner import run_pipeline
from backend.app.scenarios import SCENARIOS, SCENARIOS_BY_ID, to_submission_payload
from backend.app.storage import cases as casestore


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """conftest points VO_DB_PATH at a temp file; nothing creates the tables."""
    from backend.app.storage.db import init_db
    init_db()


def _run(scenario_id: str) -> dict:
    s = SCENARIOS_BY_ID[scenario_id]
    cid = run_pipeline(VendorSubmission(**to_submission_payload(s)),
                       compose_offline=True)
    return casestore.get_case(cid)


def _codes(case: dict) -> set[str]:
    return {f["code"] for f in case["findings"]}


# ---------------------------------------------------------------------------
# The contract: every scenario reaches the verdict it advertises
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_reaches_its_stated_verdict(scenario):
    case = _run(scenario["id"])
    assert case["status"] == scenario["expect"], (
        f"{scenario['id']} was advertised as {scenario['expect']} but came back "
        f"{case['status']}. Either the pipeline changed or the claim is stale — "
        f"findings: {sorted(_codes(case))}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_scenario_metadata_is_complete(scenario):
    """A scenario with no explanation is a fixture, not a demonstration."""
    for key in ("label", "blurb", "expect", "expect_why", "teaches", "category"):
        assert scenario.get(key), f"{scenario['id']} is missing '{key}'"
    assert scenario["kind"] in ("happy", "edge")


def test_the_brief_asked_for_two_to_four_edge_cases():
    edge = [s for s in SCENARIOS if s["kind"] == "edge"]
    assert 2 <= len(edge) <= 5, f"expected 2–4 headline edge cases, found {len(edge)}"


def test_edge_cases_cover_distinct_verdicts():
    """Four variations on one outcome would not demonstrate flexibility."""
    verdicts = {s["expect"] for s in SCENARIOS if s["kind"] == "edge"}
    assert len(verdicts) >= 3, (
        f"edge cases only produce {verdicts} — they should exercise different "
        f"decision paths, not the same one four times")


# ---------------------------------------------------------------------------
# Edge case 1 — clean submission, colliding bank account
# ---------------------------------------------------------------------------

def test_shared_account_is_caught_on_the_account_not_on_a_form_error():
    case = _run("shared-bank-account")
    assert "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR" in _codes(case)


def test_shared_account_submission_is_otherwise_clean():
    """The whole point: nothing INSIDE the submission is wrong."""
    case = _run("shared-bank-account")
    blocking = [f for f in case["findings"]
                if f["severity"] >= int(Severity.NEEDS_INFO)
                and f["code"] != "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR"]
    assert not blocking, (
        f"the scenario is supposed to be clean apart from the shared account, "
        f"but also raised {[f['code'] for f in blocking]}")


def test_shared_account_names_the_conflicting_vendor_for_the_reviewer():
    case = _run("shared-bank-account")
    f = next(f for f in case["findings"]
             if f["code"] == "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR")
    assert f["evidence"].get("existing_vendor_id"), \
        "a reviewer cannot resolve this without the conflicting record"


def test_shared_account_is_not_auto_rejected():
    """Group treasury and factoring produce this pattern legitimately."""
    assert _run("shared-bank-account")["status"] != "REJECTED"


# ---------------------------------------------------------------------------
# Edge case 2 — namesake cleared, real match rejected
# ---------------------------------------------------------------------------

def test_namesake_is_approved_despite_an_exact_name_match():
    case = _run("sanctions-namesake")
    assert case["status"] == "APPROVED"
    assert "DENIED_PARTY_NEAR_MATCH" in _codes(case), \
        "the near match should still be recorded — 'we looked and cleared it'"


def test_namesake_records_the_reason_it_was_cleared():
    case = _run("sanctions-namesake")
    f = next(f for f in case["findings"] if f["code"] == "DENIED_PARTY_NEAR_MATCH")
    assert "birth" in f["message"].lower() or "dob" in f["message"].lower()


def test_confirmed_match_is_rejected_outright():
    case = _run("sanctions-confirmed")
    assert case["status"] == "REJECTED"
    assert "DENIED_PARTY_MATCH" in _codes(case)


def _vendor_items(case: dict) -> list[str]:
    """Run the real disclosure gate over a stored case.

    `vendor_items` rides on the streamed payload and is NOT persisted, so
    reading it off the case row returns None and any assertion against it
    passes vacuously — including with the gate deleted outright. Rebuild the
    findings and call build_vendor_items, which is the code that actually
    decides what a vendor is allowed to see.
    """
    from backend.app.models import Finding, FindingCode, Status
    from backend.app.pipeline.runner import build_vendor_items
    findings = [
        Finding(code=FindingCode(f["code"]), severity=Severity(f["severity"]),
                check=f["check"], field=f.get("field"), message=f["message"],
                vendor_message=f.get("vendor_message"),
                evidence=f.get("evidence") or {})
        for f in case["findings"]
    ]
    return build_vendor_items(findings, Status(case["status"]))


def test_confirmed_match_does_not_tell_the_vendor_why():
    """Disclosing a sanctions hit to its subject is tipping off."""
    case = _run("sanctions-confirmed")
    items = _vendor_items(case)
    blob = " ".join(items).lower()
    for term in ("sanction", "ofac", "denied", "watchlist", "volkov"):
        assert term not in blob, f"vendor-facing text leaked '{term}': {items}"


def test_a_rejected_vendor_is_told_nothing_actionable_at_all():
    """The status gate, not just per-finding redaction: REJECTED discloses nothing."""
    assert _vendor_items(_run("sanctions-confirmed")) == []


def test_a_vendor_who_can_fix_things_is_told_all_of_them_at_once():
    """The counterpart — the gate must not be so tight that PENDING_INFO is silent."""
    items = _vendor_items(_run("incomplete-services"))
    assert len(items) >= 2, (
        f"a vendor missing two documents should hear about both in one message, "
        f"got {items}")


def test_the_two_sanctions_cases_differ_only_in_the_identifiers():
    """Otherwise the pair proves nothing about secondary identifiers."""
    a = SCENARIOS_BY_ID["sanctions-namesake"]["form"]["director_details"][0]
    b = SCENARIOS_BY_ID["sanctions-confirmed"]["form"]["director_details"][0]
    assert a["name"] == b["name"]
    assert (a["dob"], a["nationality"]) != (b["dob"], b["nationality"])


# ---------------------------------------------------------------------------
# Edge case 3 — requirements that must not be asked for
# ---------------------------------------------------------------------------

def test_sole_trader_is_approved_without_an_incorporation_certificate():
    case = _run("sole-trader-no-incorporation")
    assert case["status"] == "APPROVED"


def test_sole_trader_is_never_asked_for_documents_that_cannot_exist():
    case = _run("sole-trader-no-incorporation")
    missing = [f for f in case["findings"] if f["code"] == "MISSING_REQUIRED_DOCUMENT"]
    assert not missing, f"asked an individual for {[f['field'] for f in missing]}"


def test_sole_trader_is_not_blocked_on_a_tax_id_they_do_not_have():
    """The country pack demands a GSTIN; the category profile waives it."""
    case = _run("sole-trader-no-incorporation")
    fields = [f["field"] for f in case["findings"]
              if f["code"] == "MISSING_REQUIRED_FIELD"]
    assert "tax_id" not in fields and "registration_number" not in fields


def test_the_waiver_is_data_not_a_branch_in_the_code():
    """If this waiver were hardcoded, other categories would inherit it."""
    import json
    import pathlib
    prof = json.loads((pathlib.Path(__file__).resolve().parents[1]
                       / "data/profiles/categories/professional.json").read_text())
    waived = {f["key"]: f["requirement"] for f in prof["fields"]}
    assert waived.get("registration_number") == "na"
    assert waived.get("tax_id") == "na"


def test_a_company_in_the_same_country_is_still_asked_for_its_tax_id():
    """The waiver must be per-category, not a global relaxation."""
    sub = VendorSubmission(legal_name="Some Goods Company Private Limited",
                           country="IN", category="goods",
                           address_line1="1 Road", contact_email="a@b.in")
    cid = run_pipeline(sub, compose_offline=True)
    case = casestore.get_case(cid)
    fields = [f["field"] for f in case["findings"]
              if f["code"] == "MISSING_REQUIRED_FIELD"]
    assert "tax_id" in fields


# ---------------------------------------------------------------------------
# Edge case 4 — valid today, not next month
# ---------------------------------------------------------------------------

def test_expiring_document_approves_with_a_condition_rather_than_blocking():
    case = _run("licence-expiring-soon")
    assert case["status"] == "APPROVED_WITH_CONDITIONS"
    assert "DOCUMENT_EXPIRING_SOON" in _codes(case)


def test_expiring_document_is_not_treated_as_expired():
    case = _run("licence-expiring-soon")
    assert "DOCUMENT_EXPIRED" not in _codes(case)


def test_the_condition_is_recorded_so_it_can_be_chased():
    case = _run("licence-expiring-soon")
    conditions = case.get("conditions") or [
        f for f in case["findings"] if f["severity"] == int(Severity.CONDITION)]
    assert conditions, "a condition nobody records is just an approval"


def test_the_vendor_is_told_about_the_condition():
    """Conditions are one of only two statuses where we disclose to the vendor.

    The disclosure gate lives in build_vendor_items, so that is what this
    asserts — `vendor_items` rides on the streamed payload rather than being
    persisted on the case row.
    """
    items = _vendor_items(_run("licence-expiring-soon"))
    assert items, "a conditional approval the vendor never hears about cannot be met"
    assert any("expir" in i.lower() for i in items), \
        f"the vendor should be told what to renew, got {items}"


# ---------------------------------------------------------------------------
# Prefill plumbing
# ---------------------------------------------------------------------------

def test_every_scenario_names_a_real_category():
    from backend.app.profiles.store import list_categories
    known = {c["id"] for c in list_categories()}
    for s in SCENARIOS:
        assert s["category"] in known, f"{s['id']} -> unknown category {s['category']}"


def test_payload_builder_produces_a_valid_submission():
    for s in SCENARIOS:
        VendorSubmission(**to_submission_payload(s))       # must not raise


def test_prefilled_documents_carry_their_field_blocks():
    """Without these the documents are unreadable and every scenario degrades."""
    for s in SCENARIOS:
        for d in to_submission_payload(s)["documents"]:
            assert d["extracted"], f"{s['id']}/{d['doc_type']} has no field block"


def test_scenario_documents_target_slots_the_category_actually_asks_for():
    """A document filed under a slot no profile declares is silently ignored."""
    from backend.app.profiles.store import get_profile, resolve_requirements
    for s in SCENARIOS:
        payload = to_submission_payload(s)
        prof = get_profile(None, payload["country"], s["category"])
        slots = {d["key"] for d in resolve_requirements(prof, payload)["documents"]}
        slots |= {d.key for d in prof.documents}
        for d in payload["documents"]:
            assert d["doc_type"] in slots, (
                f"{s['id']} attaches '{d['doc_type']}', which the "
                f"{s['category']} profile never asks for")
