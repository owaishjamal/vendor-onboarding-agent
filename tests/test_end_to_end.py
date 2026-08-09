"""End-to-end tests against a real, running server.

Everything else in this suite calls functions. This file boots uvicorn on a
real port and drives the product the way the UI does — HTTP, multipart uploads
of real PDFs, Server-Sent Events, the ops report, the copilot. If the wiring
between the frontend contract and the backend ever drifts, this is what
catches it; unit tests never will, because they never cross the wire.

Covers the full journey for every vendor category:

    pick a category → see what IS required of that category → submit form and
    documents → watch each stage stream → get a verdict with evidence → read
    the ops report → ask the copilot about it

Run just these:   pytest tests/test_end_to_end.py -v
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
API_KEY = "e2e-test-key"


# ---------------------------------------------------------------------------
# Server fixture
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """A real uvicorn process, on a throwaway database."""
    port = _free_port()
    db_path = pathlib.Path(tempfile.mkdtemp()) / "e2e.db"
    env = {
        **os.environ,
        "VO_DB_PATH": str(db_path),
        "CHECK_DELAY_MS": "0",
        "API_KEY": API_KEY,
        "LLM_PROVIDER": "offline",
        "PYTHONPATH": str(ROOT),
        "SEED_DEMO_CASES": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):                       # up to ~30s for a cold start
        if proc.poll() is not None:
            raise RuntimeError(
                "server died on startup:\n"
                + (proc.stdout.read().decode() if proc.stdout else ""))
        try:
            urllib.request.urlopen(f"{base}/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("server did not become healthy")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:          # pragma: no cover
        proc.kill()


# ---------------------------------------------------------------------------
# HTTP helpers — deliberately stdlib, so the test has no deps of its own
# ---------------------------------------------------------------------------

def _get(base: str, path: str, key: bool = False):
    req = urllib.request.Request(base + path)
    if key:
        req.add_header("X-API-Key", API_KEY)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _post_json(base: str, path: str, payload: dict, key: bool = False):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    if key:
        req.add_header("X-API-Key", API_KEY)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----e2e-boundary"
    body = b""
    for name, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                 f"{value}\r\n").encode()
    for name, filename, blob in files:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
        body += blob + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _stream(base: str, path: str, body: bytes, content_type: str) -> list[tuple[str, dict]]:
    """Drive an SSE endpoint and collect (event, data) pairs."""
    req = urllib.request.Request(base + path, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("X-API-Key", API_KEY)
    events: list[tuple[str, dict]] = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        event = "message"
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    events.append((event, json.loads(line[5:].strip())))
                except json.JSONDecodeError:
                    pass
            elif line == "":
                event = "message"
    return events


def _pdf(lines: list[str]) -> bytes:
    """A real PDF the document reader will actually parse."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(60, y, line)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _submit(base: str, submission: dict,
            docs: list[tuple[str, bytes]] | None = None) -> dict:
    """Submit and return the finished case. Asserts the stream is well formed."""
    docs = docs or []
    submission = {**submission,
                  "documents": [{"doc_type": dt, "filename": f"{dt}.pdf"} for dt, _ in docs]}
    body, ctype = _multipart(
        {"submission": json.dumps(submission)},
        [("files", f"{dt}.pdf", blob) for dt, blob in docs])
    events = _stream(base, "/v1/cases/form/stream", body, ctype)

    kinds = [e for e, _ in events]
    assert "plan" in kinds, f"no plan event: {kinds}"
    checks = [d for e, d in events if isinstance(d, dict) and d.get("type") == "check"]
    assert checks, "no stage events streamed"
    done = [d for e, d in events if isinstance(d, dict) and d.get("type") == "done"]
    assert done, f"run never completed: {kinds}"
    return done[0]["case"]


# ---------------------------------------------------------------------------
# 1. The category catalogue drives the form
# ---------------------------------------------------------------------------

def test_all_six_categories_are_offered(server):
    cats = _get(server, "/v1/categories")
    assert {c["id"] for c in cats} == {
        "goods", "services", "construction", "logistics", "professional", "other"}
    for c in cats:
        assert c["label"] and c["blurb"]


@pytest.mark.parametrize("category", [
    "goods", "services", "construction", "logistics", "professional", "other"])
def test_each_category_returns_a_usable_requirement_set(server, category):
    r = _get(server, f"/v1/requirements?country=IN&category={category}")
    docs = r["resolved"]["documents"]
    assert docs, f"{category} asks for nothing at all"
    # Every resolved item must be actionable by the UI.
    for d in docs:
        assert d["effective"] in ("required", "optional", "na")
        assert d["label"]
    assert any(d["effective"] == "required" for d in docs), \
        f"{category} has no required document — the form would be empty"


def test_requirements_differ_by_category(server):
    """The whole point of categories: a logistics vendor and a freelancer are
    not asked for the same things."""
    log = {d["key"] for d in _get(server, "/v1/requirements?country=IN&category=logistics")
           ["resolved"]["documents"] if d["effective"] == "required"}
    pro = {d["key"] for d in _get(server, "/v1/requirements?country=IN&category=professional")
           ["resolved"]["documents"] if d["effective"] == "required"}
    assert log != pro
    assert "transit_insurance" in log
    assert "identity_proof" in pro
    # An individual is never asked to incorporate.
    assert "incorporation" not in pro


def test_conditional_requirements_react_to_the_answers(server):
    """The ask tightens as the vendor answers — this is the live preview the
    form calls on every keystroke."""
    def docs_for(workers: int) -> dict[str, str]:
        r = _post_json(server, "/v1/requirements/preview", {
            "country": "IN", "category": "construction",
            "custom_fields": {"workers_on_site": workers, "contract_value": 10000},
        })
        return {d["key"]: d["effective"] for d in r["resolved"]["documents"]}

    assert docs_for(0)["workers_insurance"] == "na"
    assert docs_for(3)["workers_insurance"] == "required"
    assert docs_for(3)["safety_certification"] == "na"      # threshold is 5
    assert docs_for(20)["safety_certification"] == "required"


# ---------------------------------------------------------------------------
# 2. Submission → verdict, for real, over HTTP
# ---------------------------------------------------------------------------

def _clean_docs(name: str) -> list[tuple[str, bytes]]:
    return [
        ("incorporation", _pdf([
            "CERTIFICATE OF INCORPORATION",
            "Registrar of Companies",
            f"Company name: {name}",
            "Company number: U74999KA2019PTC128000",
        ])),
        ("pan_card", _pdf([
            "INCOME TAX DEPARTMENT",
            "Permanent Account Number",
            f"Name: {name}", "PAN: AAACZ1234C",
        ])),
        ("tax_form", _pdf([
            "GOODS AND SERVICES TAX",
            "GST registration certificate",
            f"Legal name: {name}", "GSTIN: 29AAACZ1234C1ZV",
        ])),
        ("bank_proof", _pdf([
            "BANK CONFIRMATION LETTER",
            "We hereby confirm the following account",
            f"Account holder: {name}", "Account number: 000123456789",
            "IFSC: HDFC0001234",
        ])),
    ]


def test_happy_path_streams_every_stage_and_reaches_a_verdict(server):
    name = "Aarav Components Private Limited"
    case = _submit(server, {
        "legal_name": name, "country": "IN", "category": "goods",
        "business_description": "We supply machined metal components.",
        "contact_name": "Aarav Shah", "contact_email": "aarav@aaravcomponents.in",
        "address_line1": "12 MG Road, Bengaluru",
        "registration_number": "U74999KA2019PTC128000",
        "tax_id": "29AAACZ1234C1ZV", "pan": "AAACZ1234C",
        "bank": {"account_name": name, "account_number": "000123456789",
                 "ifsc": "HDFC0001234"},
        "custom_fields": {"nature_of_goods": "Machined metal components"},
    }, _clean_docs(name))

    assert case["status"] in (
        "APPROVED", "APPROVED_WITH_CONDITIONS", "PENDING_REVIEW", "PENDING_INFO")
    assert case["confidence"]["recommendation"]
    assert case["confidence"]["decision_reason"]
    # Every stage ran, and each one declares how it decided.
    kinds = {c["kind"] for c in case["checks"]}
    assert kinds <= {"deterministic", "ai"} and kinds
    assert len(case["checks"]) == 9


def test_missing_documents_route_to_the_vendor_with_a_written_ask(server):
    """The PS requirement: for anything not approved, tell the vendor what is
    needed — and only the vendor-safe part of it."""
    case = _submit(server, {
        "legal_name": "Halcyon Interiors LLP", "country": "IN", "category": "construction",
        "contact_email": "ops@halcyoninteriors.in",
        "address_line1": "44 Residency Road, Bengaluru",
        "business_description": "Interior fit-out contractor.",
        "bank": {"account_name": "Halcyon Interiors LLP",
                 "account_number": "55512345", "ifsc": "ICIC0000123"},
        "custom_fields": {"trade_specialisation": "Interior fit-out",
                          "workers_on_site": 8},
    })  # no documents at all

    assert case["status"] == "PENDING_INFO"
    assert case["vendor_items"], "vendor was not told what to send"
    assert case["vendor_email"], "no email drafted for a vendor-fixable case"

    # The conditional document triggered by 8 workers must be among the asks.
    codes = {f["code"] for f in case["findings"]}
    assert "MISSING_REQUIRED_DOCUMENT" in codes
    asked = " ".join(case["vendor_items"]).lower()
    assert "insurance" in asked or "licence" in asked or "license" in asked


def test_an_irrelevant_document_is_caught_and_never_silently_accepted(server):
    """A CV in the bank-proof slot is the canonical wrong-document case."""
    resume = _pdf([
        "Priya Nair — Curriculum Vitae",
        "Work Experience: 6 years in product design",
        "Skills: Figma, user research. Education: B.Des",
        "References available on request",
    ])
    case = _submit(server, {
        "legal_name": "Nair Design Studio", "country": "IN", "category": "professional",
        "contact_email": "priya@nairdesign.in", "address_line1": "8 Church Street",
        "pan": "AAAPN1234C",
        "bank": {"account_name": "Nair Design Studio",
                 "account_number": "99887766", "ifsc": "SBIN0001111"},
        "custom_fields": {"profession": "Product designer",
                          "engagement_basis": "Per project"},
    }, [("bank_proof", resume)])

    assert case["status"] != "APPROVED", "a CV was accepted as a bank proof"
    codes = {f["code"] for f in case["findings"]}
    assert "DOCUMENT_TYPE_MISMATCH" in codes
    mismatch = next(f for f in case["findings"] if f["code"] == "DOCUMENT_TYPE_MISMATCH")
    assert "resume" in mismatch["message"].lower() or "cv" in mismatch["message"].lower()
    assert mismatch["evidence"], "the finding carries no evidence"


def test_preflight_rejects_the_wrong_file_before_submission(server):
    """Catching it at attach time is what stops a wasted round trip."""
    resume = _pdf(["Rahul Verma — Resume", "Work Experience: 4 years",
                   "Skills: Python", "Education: B.Tech"])
    body, ctype = _multipart(
        {"doc_type": "bank_proof", "country": "IN", "legal_name": "Verma Traders"},
        [("file", "resume.pdf", resume)])
    req = urllib.request.Request(server + "/v1/documents/preflight",
                                 data=body, method="POST")
    req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    assert out["detected_type"] == "resume / CV"
    assert "bank proof" in out["message"].lower()


def test_a_sanctioned_party_is_rejected_and_told_nothing(server):
    """Disclosure gate: a rejected vendor must not receive a helpful email
    explaining which control caught them."""
    name = "Volkov Maritime Trading Pte Ltd"
    case = _submit(server, {
        "legal_name": name, "country": "SG", "category": "logistics",
        "contact_email": "ops@volkovmaritime.sg", "address_line1": "1 Marina Blvd",
        "registration_number": "201812345K", "tax_id": "M90312345A",
        "directors": ["Dmitri Volkov"],
        "bank": {"account_name": name, "account_number": "1234567890",
                 "swift_bic": "DBSSSGSG"},
        "custom_fields": {"fleet_size": 4, "service_region": "International"},
    })
    assert case["status"] == "REJECTED"
    assert not case["vendor_items"], "a rejected vendor was told what to fix"
    assert not case.get("vendor_email"), "an email was drafted for a rejected vendor"


@pytest.mark.parametrize("category,extra", [
    ("goods", {"nature_of_goods": "Steel fasteners"}),
    ("services", {"service_description": "IT consulting",
                  "engagement_model": "Retainer", "data_access": "No"}),
    ("construction", {"trade_specialisation": "Civil", "workers_on_site": 0}),
    ("logistics", {"fleet_size": 2, "service_region": "North", "warehousing": "No"}),
    ("professional", {"profession": "Accountant", "engagement_basis": "Hourly"}),
    ("other", {"nature_of_engagement": "Specialist calibration equipment"}),
])
def test_every_category_completes_a_run(server, category, extra):
    """The generalisation claim, tested rather than asserted: one pipeline,
    six categories, no category-specific code path."""
    name = f"Testco {category.title()} Private Limited"
    case = _submit(server, {
        "legal_name": name, "country": "IN", "category": category,
        "contact_email": f"ops@testco-{category}.in",
        "address_line1": "1 Test Road", "pan": "AAACT1234C",
        "business_description": f"A {category} vendor.",
        "bank": {"account_name": name, "account_number": "111222333",
                 "ifsc": "HDFC0001234"},
        "custom_fields": extra,
    }, _clean_docs(name))

    assert len(case["checks"]) == 9, f"{category} did not run every check"
    assert case["status"], f"{category} produced no status"
    assert case["confidence"]["recommendation"]
    # The category is recorded on the case, so the report can show it.
    assert case["submission"]["category"] == category


# ---------------------------------------------------------------------------
# 3. The ops surface
# ---------------------------------------------------------------------------

def test_dashboard_lists_runs_with_status_and_history(server):
    cases = _get(server, "/v1/cases", key=True)
    assert len(cases) >= 5, "earlier runs are not showing up on the dashboard"
    row = cases[0]
    for key in ("case_id", "legal_name", "status", "created_at"):
        assert key in row, f"dashboard row is missing {key}"

    stats = _get(server, "/v1/stats", key=True)
    assert stats["total_cases"] >= 5
    assert "by_status" in stats


def test_ops_report_separates_rule_findings_from_model_findings(server):
    case_id = _get(server, "/v1/cases", key=True)[0]["case_id"]
    detail = _get(server, f"/v1/cases/{case_id}", key=True)
    kinds = {c["check"]: c["kind"] for c in detail["checks"]}
    assert kinds["formats"] == "deterministic"
    assert kinds["documents"] == "ai"
    # Deterministic checks must remain the majority — the guard against
    # quietly handing rule work back to a model.
    det = sum(1 for k in kinds.values() if k == "deterministic")
    assert det > len(kinds) - det


def test_report_exposes_the_resolved_requirement_checklist(server):
    """Ops must see what was considered and dismissed, not only what failed."""
    cases = _get(server, "/v1/cases", key=True)
    target = next(c for c in cases if "Nair Design" in c["legal_name"])
    detail = _get(server, f"/v1/cases/{target['case_id']}", key=True)
    completeness = next(c for c in detail["checks"] if c["check"] == "completeness")
    reqs = completeness["data"]["requirements"]["documents"]
    inc = next(d for d in reqs if d["key"] == "incorporation")
    assert inc["effective"] == "na"
    assert inc["why"], "a dismissed requirement gives no reason"


def test_api_key_guards_the_write_and_reporting_endpoints(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/v1/stats")            # no key
    assert e.value.code in (401, 403)


# ---------------------------------------------------------------------------
# 4. The copilot
# ---------------------------------------------------------------------------

def test_copilot_answers_from_the_case_and_cites_findings(server):
    cases = _get(server, "/v1/cases", key=True)
    target = next(c for c in cases if "Halcyon" in c["legal_name"])
    cid = target["case_id"]

    r = _post_json(server, f"/v1/cases/{cid}/chat",
                   {"messages": [{"role": "user", "content": "What documents are missing?"}]},
                   key=True)
    assert r["source"] == "case-record"
    assert r["grounded_in"] == cid
    assert "MISSING_REQUIRED_DOCUMENT" in r["reply"]

    r2 = _post_json(server, f"/v1/cases/{cid}/chat",
                    {"messages": [{"role": "user", "content": "Which checks failed?"}]},
                    key=True)
    assert "Completeness" in r2["reply"] or "check" in r2["reply"].lower()


def test_copilot_refuses_to_invent_and_says_so(server):
    cid = _get(server, "/v1/cases", key=True)[0]["case_id"]
    r = _post_json(server, f"/v1/cases/{cid}/chat",
                   {"messages": [{"role": "user",
                                  "content": "What is the founder's home address?"}]},
                   key=True)
    assert r["source"] == "no-model"
    assert "won't guess" in r["reply"] or "not map" in r["reply"]
    # The old stub asserted every case "looks okay" — never again.
    assert "looks okay" not in r["reply"].lower()


def test_copilot_will_not_hand_review_findings_to_the_vendor(server):
    """Asking "what should I tell the vendor" on a case under internal review
    must not produce vendor-facing text."""
    cases = _get(server, "/v1/cases", key=True)
    review = [c for c in cases if c["status"] == "PENDING_REVIEW"]
    if not review:
        pytest.skip("no case under review in this run")
    r = _post_json(server, f"/v1/cases/{review[0]['case_id']}/chat",
                   {"messages": [{"role": "user",
                                  "content": "What should I ask the vendor to correct?"}]},
                   key=True)
    assert "internal" in r["reply"].lower() or "tip off" in r["reply"].lower()


def test_chat_rejects_an_empty_conversation(server):
    cid = _get(server, "/v1/cases", key=True)[0]["case_id"]
    with pytest.raises(urllib.error.HTTPError) as e:
        _post_json(server, f"/v1/cases/{cid}/chat", {"messages": []}, key=True)
    assert e.value.code == 422
