#!/usr/bin/env python3
"""MCP server exposing this system's own internals to an agent.

WHY THIS EXISTS
    An agent reasoning about a verdict has two options: read the code and
    predict what it would do, or run it and look. The first is how confident
    wrong answers get written — "this should return PENDING_REVIEW" is a guess
    dressed as a fact. These tools make the second option one call away.

    Everything here is READ-ONLY or runs against a throwaway database. An agent
    exploring the decision model cannot modify a real case.

WHY HAND-ROLLED JSON-RPC AND NOT THE MCP SDK
    Same reasoning as the rest of the repo: the protocol surface needed here is
    three methods over stdio, and a dependency that must be installed before
    the tooling works is a dependency that stops the tooling working. This runs
    on a bare Python 3.10.

REGISTER IT
    claude mcp add vendor-onboarding -- python3 scripts/mcp_server.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import traceback
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Never touch the developer's real database, and never call a live model just
# because a key happens to be present in the environment.
os.environ.setdefault("LLM_PROVIDER", "offline")
os.environ.setdefault("CHECK_DELAY_MS", "0")
os.environ["VO_DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_mcp.db")

PROTOCOL_VERSION = "2024-11-05"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def run_submission(submission: dict) -> dict:
    """Run the real nine-check pipeline and return the verdict with reasons."""
    from backend.app.models import VendorSubmission
    from backend.app.pipeline.runner import run_pipeline
    from backend.app.storage import cases as casestore, db

    db.init_db()
    case_id = run_pipeline(VendorSubmission(**submission), compose_offline=True)
    case = casestore.get_case(case_id)
    return {
        "status": case["status"],
        "confidence": (case.get("confidence") or {}).get("score"),
        "decision_reason": (case.get("confidence") or {}).get("decision_reason"),
        "findings": [
            {"code": f["code"], "severity": f["severity_name"],
             "check": f["check"], "field": f.get("field"),
             "message": f["message"]}
            for f in case["findings"]
        ],
        "checks": [{"check": c["check"], "kind": c.get("kind"),
                    "summary": c["summary"]} for c in case.get("checks", [])],
    }


def run_scenario(scenario_id: str) -> dict:
    """Run a prepared scenario and report whether it met its stated verdict."""
    from backend.app.scenarios import SCENARIOS_BY_ID, to_submission_payload
    s = SCENARIOS_BY_ID.get(scenario_id)
    if not s:
        return {"error": f"unknown scenario '{scenario_id}'",
                "known": sorted(SCENARIOS_BY_ID)}
    result = run_submission(to_submission_payload(s))
    return {"scenario": scenario_id, "expected": s["expect"],
            "actual": result["status"],
            "matches": result["status"] == s["expect"],
            "teaches": s["teaches"], **result}


def list_scenarios() -> list[dict]:
    from backend.app.scenarios import list_scenarios as _ls
    return _ls()


def resolve_requirements(country: str = "IN", category: str = "goods",
                         submission: dict | None = None) -> dict:
    """What would the form ask THIS vendor for, and why?

    The three-layer resolution (country → category → client) plus conditionals
    is the least predictable part of the system by reading alone.
    """
    from backend.app.profiles.store import get_profile, resolve_requirements as _rr
    prof = get_profile(None, country, category)
    payload = {"country": country, "category": category, **(submission or {})}
    r = _rr(prof, payload)
    return {
        "profile": prof.profile_id,
        "fields": [{"key": f["key"], "effective": f["effective"],
                    "declared": f["declared"], "why": f.get("why"),
                    "because": f.get("when_explained")} for f in r["fields"]],
        "documents": [{"key": d["key"], "effective": d["effective"],
                       "declared": d["declared"], "why": d.get("why"),
                       "because": d.get("when_explained")} for d in r["documents"]],
    }


def explain_routing(task_type: str = "reasoning") -> dict:
    """The fallback chain the LLM router would use for a task, and why.

    Routing depends on live health and rate-limit state, so reading scoring.py
    tells you the algorithm but not the answer.
    """
    from backend.app.llm.router import LLMRouter, TaskType, scoring
    from backend.app.llm.router.schemas import LLMRequest, Message
    r = LLMRouter()
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     task_type=TaskType(task_type))
    chain = scoring.diversify(scoring.rank(r.registry, req,
                                           latency_lookup=r.health.avg_latency_ms))
    return {
        "task_type": task_type,
        "requires": sorted(c.value for c in
                           scoring.required_capabilities(r.registry, req)),
        "providers_loaded": r.providers.names(),
        "providers_skipped": r.registry.skipped,
        "chain": [{"model": c.spec.key, "score": round(c.score, 1),
                   "why": c.why} for c in chain],
    }


def check_invariants() -> dict:
    """Assert the four invariants that must never break. Cheap to run often."""
    from backend.app.models import SEVERITY_TO_STATUS, Severity, Status
    out: dict[str, Any] = {}

    out["severity_ordering"] = (
        int(Severity.ADVISORY) < int(Severity.CONDITION) < int(Severity.NEEDS_INFO)
        < int(Severity.NEEDS_REVIEW) < int(Severity.REJECT))

    out["condition_maps_to_conditional_approval"] = (
        SEVERITY_TO_STATUS[Severity.CONDITION] is Status.APPROVED_WITH_CONDITIONS)

    from backend.app.pipeline.runner import build_vendor_items
    out["rejected_vendors_are_told_nothing"] = (
        build_vendor_items([], Status.REJECTED) == [])

    from backend.app.profiles.store import get_profile, resolve_requirements
    goods = resolve_requirements(get_profile(None, "IN", "goods"),
                                 {"country": "IN", "category": "goods"})
    waived = {f["key"] for f in goods["fields"] if f["effective"] == "na"}
    out["waivers_do_not_leak_to_other_categories"] = "tax_id" not in waived

    out["all_hold"] = all(v for k, v in out.items() if k != "all_hold")
    return out


TOOLS: dict[str, tuple[Any, str, dict]] = {
    "run_submission": (
        run_submission,
        "Run the real nine-check pipeline on a submission and return the "
        "verdict, confidence and every finding. Use this instead of predicting "
        "what a change would do.",
        {"type": "object",
         "properties": {"submission": {
             "type": "object",
             "description": "VendorSubmission fields: legal_name, country, "
                            "category, tax_id, bank{}, documents[], ..."}},
         "required": ["submission"]}),

    "run_scenario": (
        run_scenario,
        "Run one prepared scenario by id and report whether it still reaches "
        "its stated verdict.",
        {"type": "object",
         "properties": {"scenario_id": {"type": "string"}},
         "required": ["scenario_id"]}),

    "list_scenarios": (
        list_scenarios,
        "List the prepared scenarios with the verdict each is expected to "
        "reach and what it demonstrates.",
        {"type": "object", "properties": {}}),

    "resolve_requirements": (
        resolve_requirements,
        "Show exactly what the form would ask a given vendor for, and why — "
        "including which requirements are waived and which conditionals fired.",
        {"type": "object",
         "properties": {
             "country": {"type": "string", "default": "IN"},
             "category": {"type": "string",
                          "enum": ["goods", "services", "construction",
                                   "logistics", "professional", "other"]},
             "submission": {"type": "object",
                            "description": "Partial answers, to resolve conditionals"}}}),

    "explain_routing": (
        explain_routing,
        "Show the LLM router's fallback chain for a task type, with the score "
        "and reason for each candidate model.",
        {"type": "object",
         "properties": {"task_type": {"type": "string", "default": "reasoning"}}}),

    "check_invariants": (
        check_invariants,
        "Assert the four decision-model invariants. Run after any change to a "
        "decision path.",
        {"type": "object", "properties": {}}),
}


# ---------------------------------------------------------------------------
# JSON-RPC over stdio
# ---------------------------------------------------------------------------

def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")

    if method == "initialize":
        return _ok(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "vendor-onboarding", "version": "1.0.0"}})

    if method == "notifications/initialized":
        return None                       # notification: no reply

    if method == "tools/list":
        return _ok(rid, {"tools": [
            {"name": n, "description": d, "inputSchema": s}
            for n, (_, d, s) in TOOLS.items()]})

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        entry = TOOLS.get(name)
        if entry is None:
            return _err(rid, -32602, f"unknown tool '{name}'")
        try:
            result = entry[0](**(params.get("arguments") or {}))
            text = json.dumps(result, indent=2, default=str)
        except Exception as exc:
            # Return the failure as tool output rather than a protocol error:
            # an agent can read a traceback and correct, but a transport error
            # just ends the call.
            text = json.dumps({"error": f"{type(exc).__name__}: {exc}",
                               "traceback": traceback.format_exc()[-1500:]},
                              indent=2)
            return _ok(rid, {"content": [{"type": "text", "text": text}],
                             "isError": True})
        return _ok(rid, {"content": [{"type": "text", "text": text}]})

    return _err(rid, -32601, f"unknown method '{method}'")


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
