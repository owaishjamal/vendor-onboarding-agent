"""Tests for the generalized, category-driven onboarding.

Covers the four things that changed shape: vendor categories, conditional
requirements, the conditional-approval verdict, and the grounded ops copilot.
"""

from __future__ import annotations

import json
import os
import pathlib
import queue
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VO_DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_general.db")
os.environ["CHECK_DELAY_MS"] = "0"
# Pin the provider. Without this the suite passed or failed depending on
# whether the developer happened to have a key in .env — the copilot tests
# assert the no-model behaviour, and a real key silently changed the answer.
# Tests must not depend on the machine they run on.
os.environ["LLM_PROVIDER"] = "offline"

from backend.app.llm import ops_copilot  # noqa: E402
from backend.app.llm.client import get_llm  # noqa: E402
from backend.app.models import (  # noqa: E402
    SEVERITY_TO_STATUS, Finding, FindingCode, Severity, Status,
    VendorCategory, VendorSubmission,
)
from backend.app.pipeline import confidence  # noqa: E402
from backend.app.pipeline.runner import build_vendor_items, decide, run_pipeline  # noqa: E402
from backend.app.profiles import conditions  # noqa: E402
from backend.app.profiles.store import (  # noqa: E402
    get_profile, list_categories, resolve_requirements,
)
from backend.app.storage import db  # noqa: E402

SUBS = ROOT / "data" / "submissions"


@pytest.fixture(autouse=True)
def fresh_db():
    db.reset_db()
    yield


# ===========================================================================
# Conditions: the `when` grammar
# ===========================================================================

@pytest.mark.parametrize("expr,data,expected", [
    ("country == 'IN'", {"country": "IN"}, True),
    ("country == 'IN'", {"country": "GB"}, False),
    ("country == 'in'", {"country": "IN"}, True),            # case-insensitive
    ("entity_type != 'individual'", {"entity_type": "company"}, True),
    ("category in ['goods', 'other']", {"category": "goods"}, True),
    ("category in ['goods', 'other']", {"category": "services"}, False),
    ("tax_id is present", {"tax_id": "GB123"}, True),
    ("tax_id is present", {"tax_id": "   "}, False),
    ("tax_id is absent", {}, True),
    ("workers_on_site > 5", {"custom_fields": {"workers_on_site": 12}}, True),
    ("workers_on_site > 5", {"custom_fields": {"workers_on_site": 2}}, False),
    ("bank.account_name is present", {"bank": {"account_name": "Acme"}}, True),
    ("country == 'IN' and tax_id is present", {"country": "IN", "tax_id": "X"}, True),
    ("country == 'IN' and tax_id is present", {"country": "IN"}, False),
    ("country == 'GB' or country == 'IN'", {"country": "IN"}, True),
])
def test_condition_grammar(expr, data, expected):
    assert conditions.evaluate(expr, data) is expected


def test_unparseable_condition_is_false_not_an_exception():
    """A profile is client-editable data. A malformed condition must never
    crash a run, and must never silently become 'required' either."""
    assert conditions.evaluate("this is not )( valid", {"country": "IN"}) is False
    assert conditions.evaluate("", {}) is False
    assert conditions.evaluate(None, {}) is False


def test_conditions_are_not_evaluated_as_python():
    """The evaluator must not be `eval` — profiles arrive over an API."""
    data = {"country": "IN"}
    assert conditions.evaluate("__import__('os').system('echo pwned')", data) is False


# ===========================================================================
# Categories and requirement resolution
# ===========================================================================

def test_every_category_has_a_profile():
    cats = {c["id"] for c in list_categories()}
    assert cats == {c.value for c in VendorCategory}


def test_category_adds_requirements_on_top_of_country():
    """A logistics vendor owes the country baseline plus transit cover."""
    prof = get_profile(None, "IN", "logistics")
    keys = {d.key for d in prof.documents}
    assert "transit_insurance" in keys          # from the category
    assert "pan_card" in keys                   # from the country pack


def test_conditional_document_appears_only_when_its_condition_holds():
    prof = get_profile(None, "IN", "construction")
    crew = resolve_requirements(prof, {"custom_fields": {"workers_on_site": 12}})
    solo = resolve_requirements(prof, {"custom_fields": {"workers_on_site": 0}})

    def eff(res, key):
        return next(d["effective"] for d in res["documents"] if d["key"] == key)

    assert eff(crew, "workers_insurance") == "required"
    assert eff(solo, "workers_insurance") == "na"
    # ...and the contractor licence is unconditional either way.
    assert eff(crew, "contractor_licence") == "required"
    assert eff(solo, "contractor_licence") == "required"


def test_category_can_mark_a_country_default_not_applicable():
    """An individual has no certificate of incorporation. Demanding one is the
    single most common reason a legitimate freelancer abandons onboarding."""
    prof = get_profile(None, "IN", "professional")
    res = resolve_requirements(prof, {"custom_fields": {"profession": "Architect"}})
    inc = next(d for d in res["documents"] if d["key"] == "incorporation")
    assert inc["effective"] == "na"
    assert inc["why"]                      # and it explains why, for the reviewer


def test_requirements_explain_why_they_are_asked():
    prof = get_profile(None, "IN", "construction")
    res = resolve_requirements(prof, {"custom_fields": {"workers_on_site": 3}})
    wc = next(d for d in res["documents"] if d["key"] == "workers_insurance")
    assert wc["why"]
    assert wc["when_explained"]            # human-readable trigger for the report


def test_unknown_category_falls_back_to_country_defaults():
    prof = get_profile(None, "GB", "not-a-real-category")
    assert {d.key for d in prof.documents} >= {"incorporation", "bank_proof"}


def test_category_flows_through_the_pipeline_into_the_case():
    base = json.loads((SUBS / "VS-01_northwind_clean.json").read_text())
    base["category"] = "goods"
    q: "queue.Queue[dict]" = queue.Queue()
    run_pipeline(VendorSubmission(**base), local_queue=q)
    case = _drain(q)
    completeness = next(c for c in case["checks"] if c["check"] == "completeness")
    assert completeness["data"]["category"] == "goods"
    assert completeness["data"]["requirements"]["documents"]


# ===========================================================================
# The conditional-approval verdict
# ===========================================================================

def test_condition_severity_maps_to_conditional_approval():
    assert SEVERITY_TO_STATUS[Severity.CONDITION] is Status.APPROVED_WITH_CONDITIONS


def test_condition_ranks_between_advisory_and_needs_info():
    """Ordering is load-bearing: a condition must not outrank something a
    vendor has to fix, and must outrank a note on the file."""
    assert Severity.ADVISORY < Severity.CONDITION < Severity.NEEDS_INFO


def test_a_condition_alone_yields_conditional_approval():
    f = Finding(code=FindingCode.DOCUMENT_EXPIRING_SOON, severity=Severity.CONDITION,
                check="documents", message="Expires in 30 days.")
    assert decide([f]) is Status.APPROVED_WITH_CONDITIONS


def test_a_condition_never_masks_something_more_serious():
    cond = Finding(code=FindingCode.DOCUMENT_EXPIRING_SOON, severity=Severity.CONDITION,
                   check="documents", message="Expires soon.")
    review = Finding(code=FindingCode.BANK_NAME_MISMATCH, severity=Severity.NEEDS_REVIEW,
                     check="consistency", message="Holder differs.")
    assert decide([cond, review]) is Status.PENDING_REVIEW


def test_low_confidence_downgrades_a_conditional_approval_to_review():
    """Confidence may only ever move a case TOWARDS a human."""
    cond = Finding(code=FindingCode.DOCUMENT_EXPIRING_SOON, severity=Severity.CONDITION,
                   check="documents", message="Expires soon.")
    status, why = confidence.route(
        Status.APPROVED_WITH_CONDITIONS, 0.40, [cond], 0.85)
    assert status is Status.PENDING_REVIEW
    status, _ = confidence.route(
        Status.APPROVED_WITH_CONDITIONS, 0.95, [cond], 0.85)
    assert status is Status.APPROVED_WITH_CONDITIONS


def test_conditions_are_disclosed_to_the_vendor_but_review_findings_are_not():
    cond = Finding(code=FindingCode.DOCUMENT_EXPIRING_SOON, severity=Severity.CONDITION,
                   check="documents", message="internal wording",
                   vendor_message="Please send a renewed certificate.")
    review = Finding(code=FindingCode.BANK_NAME_MISMATCH, severity=Severity.NEEDS_REVIEW,
                     check="consistency", message="possible redirection",
                     vendor_message="LEAK")

    items = build_vendor_items([cond, review], Status.APPROVED_WITH_CONDITIONS)
    assert items == ["Please send a renewed certificate."]
    assert "LEAK" not in " ".join(items)

    # And a case under review still tells the vendor nothing at all.
    assert build_vendor_items([cond, review], Status.PENDING_REVIEW) == []


def test_every_status_has_a_recommendation():
    for s in Status:
        assert confidence.recommendation(s)


# ===========================================================================
# Deterministic vs AI separation
# ===========================================================================

def test_every_check_declares_its_kind():
    from backend.app.pipeline.runner import CHECK_PLAN
    assert all(c["kind"] in ("deterministic", "ai") for c in CHECK_PLAN)


def test_the_majority_of_checks_need_no_model():
    """If a rule can decide it, a model must not. Guards against drift back
    towards asking an LLM to validate a checksum."""
    from backend.app.pipeline.runner import CHECK_PLAN
    det = [c for c in CHECK_PLAN if c["kind"] == "deterministic"]
    assert len(det) >= len(CHECK_PLAN) - len(det)


def test_results_carry_their_kind_through_to_the_case():
    q: "queue.Queue[dict]" = queue.Queue()
    run_pipeline(_load("VS-01_northwind_clean.json"), local_queue=q)
    case = _drain(q)
    from backend.app.pipeline.runner import CHECK_KIND
    for c in case["checks"]:
        assert CHECK_KIND[c["check"]] in ("deterministic", "ai")


# ===========================================================================
# Ops copilot grounding
# ===========================================================================

def _case(filename: str) -> dict:
    q: "queue.Queue[dict]" = queue.Queue()
    run_pipeline(_load(filename), local_queue=q)
    return _drain(q)


def test_copilot_answers_the_documented_questions_from_the_record():
    case = _case("VS-03_kessler_bank_mismatch.json")
    for question in [
        "Why was this vendor marked needs review?",
        "What documents are missing?",
        "Which checks failed?",
        "Are there any mismatches?",
        "Summarize the major risks.",
        "What should I ask the vendor to correct?",
        "Which documents are expiring?",
        "Show me the evidence for this finding.",
        "Can this vendor be approved based on the current information?",
    ]:
        assert ops_copilot.answer(case, question), f"no grounded answer for: {question}"


def test_copilot_refuses_what_it_cannot_ground():
    case = _case("VS-01_northwind_clean.json")
    assert ops_copilot.answer(case, "who is the CEO's favourite author?") is None

    reply = get_llm().ops_chat(case, [{"role": "user",
                                       "content": "who is the CEO's favourite author?"}])
    assert reply["source"] == "no-model"
    # The old stub asserted the case "looks okay" no matter what was asked.
    assert "looks okay" not in reply["reply"].lower()


def test_copilot_never_leaks_review_findings_as_vendor_advice():
    """A PENDING_REVIEW case must not produce 'ask the vendor' text — that is
    how you tip off a fraudster mid-investigation."""
    case = _case("VS-03_kessler_bank_mismatch.json")
    reply = ops_copilot.answer(case, "what should I ask the vendor to correct?")
    assert "tip off" in reply.lower() or "internal" in reply.lower()


def test_copilot_reports_a_clean_case_as_clean():
    case = _case("VS-01_northwind_clean.json")
    assert "Nothing is outstanding" in ops_copilot.answer(case, "what is missing?")


def test_model_context_excludes_bulk_and_keeps_evidence():
    case = _case("VS-03_kessler_bank_mismatch.json")
    ctx = ops_copilot.context_for_model(case)
    assert "submission" not in ctx           # raw payload withheld
    assert ctx["findings"] and "evidence" in ctx["findings"][0]
    assert ctx["status"] == case["status"]


# ===========================================================================
# helpers
# ===========================================================================

def _load(filename: str) -> VendorSubmission:
    return VendorSubmission(**json.loads((SUBS / filename).read_text()))


def _drain(q: "queue.Queue[dict]") -> dict:
    case = None
    while not q.empty():
        ev = q.get_nowait()
        if ev.get("type") == "done":
            case = ev["case"]
    assert case is not None, "pipeline produced no done event"
    return case


# ===========================================================================
# Gemini response parsing
#
# gemini-flash-latest is a thinking model: its reasoning tokens count against
# maxOutputTokens, and its reply can arrive as several parts, some of them
# marked as thoughts. Indexing parts[0].text produced answers cut off
# mid-sentence, replies that were actually the model's private plan
# ("Structure: be brief and concrete..."), and bare KeyErrors when it returned
# no text at all. These pin each of those shapes.
# ===========================================================================

def _gemini():
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test-key")
    from backend.app.llm.client import GeminiClient
    return GeminiClient


def test_gemini_joins_every_text_part():
    """A multi-part answer must not be truncated to its first fragment."""
    out = _gemini()._text_from({"candidates": [{"content": {"parts": [
        {"text": "Part one."}, {"text": "Part two."}]}, "finishReason": "STOP"}]})
    assert "Part one." in out and "Part two." in out


def test_gemini_never_returns_the_models_private_thinking():
    """The reviewer must see the answer, not the plan for the answer."""
    out = _gemini()._text_from({"candidates": [{"content": {"parts": [
        {"text": "Structure: be brief, cite finding codes.", "thought": True},
        {"text": "The vendor is pending review on BANK_NAME_MISMATCH."},
    ]}, "finishReason": "STOP"}]})
    assert out == "The vendor is pending review on BANK_NAME_MISMATCH."
    assert "Structure:" not in out


@pytest.mark.parametrize("payload,expect", [
    ({"candidates": [{"content": {}, "finishReason": "MAX_TOKENS"}]}, "output limit"),
    ({"candidates": [{"content": {}, "finishReason": "SAFETY"}]}, "safety"),
    ({"promptFeedback": {"blockReason": "OTHER"}}, "no candidates"),
])
def test_gemini_explains_an_empty_response_instead_of_crashing(payload, expect):
    """A stack trace is a useless answer to "why was this vendor flagged?"."""
    with pytest.raises(RuntimeError, match=expect):
        _gemini()._text_from(payload)


_INVALID_ARG = ('Gemini HTTP 400: {"error": {"code": 400, "message": '
                '"Request contains an invalid argument.", "status": "INVALID_ARGUMENT"}}')


def _sim_client(reject_thinking: bool):
    """A Gemini client whose transport is recorded instead of sent."""
    G = _gemini()
    G._supports_thinking_config = True          # reset the per-process memo

    class Sim(G):
        def __init__(self):
            self.api_key, self.model, self.calls = "k", "test-model", []

        def _post(self, gen, system, user):
            self.calls.append(gen)
            if reject_thinking and "thinkingConfig" in gen:
                raise RuntimeError(_INVALID_ARG)
            return "answer"

    return Sim()


def test_thinking_is_disabled_where_the_model_supports_it():
    """Reasoning tokens are billed against maxOutputTokens, so a thinking
    model spends the budget planning and the answer arrives truncated."""
    c = _sim_client(reject_thinking=False)
    assert c._complete("sys", "q", 4096) == "answer"
    assert c.calls[0]["thinkingConfig"] == {"thinkingBudget": 0}


def test_a_model_that_rejects_thinking_config_still_answers():
    """`gemini-flash-latest` returns a bare 400 INVALID_ARGUMENT for
    thinkingConfig — with no clue which argument is at fault. Sending it must
    not take the copilot down."""
    c = _sim_client(reject_thinking=True)
    assert c._complete("sys", "q", 4096) == "answer"
    assert "thinkingConfig" in c.calls[0]        # tried
    assert "thinkingConfig" not in c.calls[1]    # then fell back


def test_the_unsupported_model_is_remembered_not_re_probed():
    """Otherwise every single call pays for two round trips."""
    c = _sim_client(reject_thinking=True)
    c._complete("sys", "q", 4096)
    c.calls.clear()
    c._complete("sys", "q", 4096)
    assert len(c.calls) == 1 and "thinkingConfig" not in c.calls[0]


def test_an_unrelated_400_is_not_swallowed_by_the_fallback():
    """A rejected API key must surface, not be retried into a different error."""
    G = _gemini()
    G._supports_thinking_config = True

    class Hard(G):
        def __init__(self):
            self.api_key, self.model = "k", "test-model"

        def _post(self, gen, system, user):
            raise RuntimeError("Gemini HTTP 400: API key not valid")

    with pytest.raises(RuntimeError, match="API key not valid"):
        Hard()._complete("sys", "q", 1024)


def test_the_output_ceiling_is_never_below_the_floor():
    """800 tokens truncated a grounded answer citing findings."""
    c = _sim_client(reject_thinking=False)
    c._complete("sys", "q", 100)
    assert c.calls[0]["maxOutputTokens"] >= 1024


@pytest.mark.parametrize("question", [
    "hi", "hello", "what was the issue", "what issue did u cought",
    "what went wrong?", "which is the problem here", "why was this flagged",
])
def test_the_most_natural_questions_are_answered_from_the_record(question):
    """These went to the model, which is slower, costs money, and can truncate.
    They are lookups; the record answers them exactly."""
    case = _case("VS-03_kessler_bank_mismatch.json")
    assert ops_copilot.answer(case, question), f"fell through to the model: {question}"


def test_a_greeting_opens_with_the_case_not_a_model_call():
    case = _case("VS-03_kessler_bank_mismatch.json")
    reply = ops_copilot.answer(case, "hi")
    assert "Kessler" in reply and "Status" in reply


def test_which_checks_failed_still_routes_to_the_checks_intent():
    """The new 'flagged' pattern must not swallow more specific questions."""
    case = _case("VS-03_kessler_bank_mismatch.json")
    assert "checks raised" in ops_copilot.answer(case, "which checks failed?")
