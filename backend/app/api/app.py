"""FastAPI surface.

Case endpoints stream Server-Sent Events so a reviewer watches each check land
in turn. Unlike an invoice pipeline the run never stops early, so the stream
always delivers all six checks — which is itself informative: you can see that
a rejected case still had its documents verified and its duplicates checked.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterator

import uuid

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.app import config
from backend.app.llm.client import get_llm
from backend.app.models import Severity, VendorSubmission
from backend.app.pipeline.runner import CHECK_PLAN, run_pipeline
from backend.app.rules import (
    load_common_rules, load_country_rules, supported_countries,
)
from backend.app.storage import cases as casestore
from backend.app.storage import db
from backend.app.storage.documents import get_storage
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("vo.api")

app = FastAPI(title="Vendor Onboarding", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    log.info("database ready; countries=%s; llm=%s",
             ",".join(supported_countries()), get_llm().provider)
    if os.getenv("SEED_DEMO_CASES", "").lower() in ("1", "true", "yes"):
        _seed_demo_cases()


def _seed_demo_cases() -> None:
    """Run the labelled submissions once, if the queue is empty.

    Hosting platforms with an ephemeral filesystem (Render free tier, Fly
    machines) lose the SQLite file on every restart. Without this a visitor
    opening the public link after a redeploy lands on an empty dashboard,
    which reads as "nothing works" rather than "nothing has run yet".

    Only ever seeds an EMPTY database, so it can never pollute real history.
    """
    try:
        if casestore.list_cases(limit=1):
            return
        manifest_path = config.SUBMISSION_DIR / "manifest.json"
        if not manifest_path.exists():
            return
        seeded = 0
        for entry in json.loads(manifest_path.read_text()):
            path = config.SUBMISSION_DIR / entry["file"]
            if not path.exists():
                continue
            try:
                sub = VendorSubmission(**json.loads(path.read_text()))
                # Compose the prose offline. Eleven cases at boot meant
                # twenty-two model calls before the port opened — enough to
                # exhaust a free-tier quota and stall startup past the
                # platform's port scan.
                run_pipeline(sub, compose_offline=True)
                seeded += 1
            except Exception as exc:          # one bad fixture must not block boot
                log.warning("demo seed skipped %s: %s", entry.get("file"), exc)
        log.info("seeded %d demo cases into an empty queue", seeded)
    except Exception as exc:                   # never let seeding break startup
        log.warning("demo seeding failed, continuing with an empty queue: %s", exc)


def _validate_upload(filename: str, size_bytes: int) -> None:
    """Basic upload hygiene: allowed type, sane size."""
    ext = Path(filename or "").suffix.lower()
    if ext not in config.ALLOWED_UPLOAD_EXT:
        raise HTTPException(415, f"unsupported file type '{ext}'. "
                                 f"allowed: {sorted(config.ALLOWED_UPLOAD_EXT)}")
    if size_bytes > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {config.MAX_UPLOAD_MB} MB limit")


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    llm = get_llm()
    return {"status": "ok", "llm_provider": llm.provider,
            "llm_routes_via": llm.routes_via,
            "app_title": config.APP_TITLE, "app_subtitle": config.APP_SUBTITLE,
            "llm_cache": config.LLM_CACHE_ENABLED,
            "check_delay_ms": config.CHECK_DELAY_MS,
            "countries": list(supported_countries())}


@app.get("/metrics")
def metrics():
    """Prometheus exposition. 404s when prometheus_client is not installed,
    which is the honest answer — an empty 200 would look like zero traffic."""
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        raise HTTPException(404, "prometheus_client is not installed")
    from fastapi import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/checks")
def checks() -> list[dict]:
    return CHECK_PLAN


@app.get("/v1/severities")
def severities() -> list[dict]:
    return [{"name": s.name, "value": int(s)} for s in Severity]


@app.get("/v1/countries")
def countries() -> list[dict]:
    out = []
    for code in supported_countries():
        r = load_country_rules(code)
        out.append({
            "code": code,
            "name": r.get("country_name", code),
            "tax_id": r.get("tax_id", {}),
            "registration_number": r.get("registration_number", {}),
            "bank_scheme": (r.get("bank", {}) or {}).get("scheme"),
            "required_documents": r.get("required_documents", []),
        })
    return out


@app.get("/v1/categories")
def categories() -> list[dict]:
    """Vendor categories for the intake dropdown, with what each one adds."""
    from backend.app.profiles.store import list_categories
    return list_categories()


@app.get("/v1/scenarios")
def scenarios() -> list[dict]:
    """The demonstrable scenarios offered as one-click prefills on the form.

    Catalogue only — labels, the verdict each one is expected to reach and the
    reason it is worth showing. The values themselves come from
    /v1/scenarios/{id}, so the menu stays small.
    """
    from backend.app import scenarios as scen
    return scen.list_scenarios()


@app.get("/v1/scenarios/{scenario_id}")
def scenario_detail(scenario_id: str) -> dict:
    """Form values for one scenario, ready to drop into the wizard.

    Returns data, not a decision. The prefilled form is submitted through the
    ordinary endpoint and runs the ordinary pipeline, so a scenario cannot
    reach a verdict any other submission could not also reach.
    """
    from backend.app import scenarios as scen
    s = scen.get_scenario(scenario_id)
    if not s:
        raise HTTPException(404, f"unknown scenario '{scenario_id}'")
    return {
        "id": s["id"], "label": s["label"], "kind": s["kind"],
        "category": s["category"], "expect": s["expect"],
        "expect_why": s["expect_why"], "teaches": s["teaches"],
        "form": s["form"], "bank": s["bank"],
        "custom_fields": s["custom_fields"],
        "documents": s["documents"],
        "payload": scen.to_submission_payload(s),
    }


@app.get("/v1/requirements")
def requirements(country: str = "", category: str = "",
                 profile_id: str = "") -> dict:
    """What THIS vendor has to supply, before they submit anything.

    Powers the dynamic form: the vendor picks a category and immediately sees
    the fields and documents that apply to them, each with the reason it is
    being asked for. Conditional items are resolved against whatever has been
    entered so far, so the list tightens as the form is filled in.
    """
    from backend.app.profiles.store import get_profile, resolve_requirements
    prof = get_profile(profile_id or None, country, category or None)
    resolved = resolve_requirements(prof, {"country": country, "category": category})
    return {
        "profile_id": prof.profile_id,
        "profile_name": prof.name,
        "country": country,
        "category": category,
        "fields": [f.model_dump(mode="json") for f in prof.fields],
        "documents": [d.model_dump(mode="json") for d in prof.documents],
        "resolved": resolved,
    }


@app.post("/v1/requirements/preview")
def requirements_preview(payload: dict[str, Any] = Body(...)) -> dict:
    """Re-resolve requirements against a partially-filled submission.

    Called as the vendor types, so a conditional document appears the moment
    the answer that triggers it is given ("you told us 12 people will be on
    site, so we now need workers' compensation cover").
    """
    from backend.app.profiles.store import get_profile, resolve_requirements
    prof = get_profile(payload.get("profile_id") or None,
                       payload.get("country", "") or "",
                       payload.get("category") or None)
    return {"resolved": resolve_requirements(prof, payload)}


@app.get("/v1/policy")
def policy() -> dict:
    return {"common": load_common_rules(), "countries": countries()}


from fastapi.security.api_key import APIKeyHeader
from fastapi import Security

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def require_api_key(api_key: str = Security(api_key_header)):
    expected_key = os.environ.get("API_KEY", "dev_secret")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    import hmac
    if not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key


@app.get("/v1/llm/health", dependencies=[Depends(require_api_key)])
async def llm_health() -> dict:
    """Per-model routing state: health, breaker, rate-limit windows, spend.

    Guarded, because it names every configured model and its live limits —
    useful to an operator, and a free map of the deployment to anyone else.
    It reveals no credentials: keys are read from the environment inside the
    adapters and never enter a request, a response or a log line.

    Defined here rather than beside /health because `require_api_key` is
    declared just above; referencing it earlier is a NameError at import, and
    uvicorn reports that as "server died on startup" with the real cause forty
    frames down.
    """
    llm = get_llm()
    router = getattr(llm, "router", None)
    if router is None:
        return {"mode": "offline",
                "detail": "No LLM router is active; the app is on templates.",
                "reason": "no provider API key is configured"}
    return {"mode": "router", **await router.health_report()}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

# A run that produces no event for this long is treated as dead. Without a
# ceiling the browser sits on an open connection forever when a worker dies
# mid-job, which looks identical to "your product is broken".
STREAM_IDLE_TIMEOUT_S = float(os.getenv("STREAM_IDLE_TIMEOUT_S", "120"))


def _stream(sub: VendorSubmission) -> StreamingResponse:
    """Run a submission and stream one Server-Sent Event per completed stage.

    The pipeline runs on a background thread and pushes events into a queue
    that this response drains. Two consequences worth stating:

      * The run does not depend on the browser staying connected. If the tab
        closes, the case still completes and is persisted — the viewer is a
        spectator, not the driver.
      * There is no broker and no worker process. At onboarding volumes the
        whole run is sub-second, so a job queue would add an service to
        provision and monitor in exchange for nothing.
    """
    import queue
    import threading

    from backend.app.pipeline.runner import plan_for, run_pipeline

    cid = uuid.uuid4().hex[:12]
    local_q: "queue.Queue[dict]" = queue.Queue()
    threading.Thread(
        target=run_pipeline, args=(sub, cid, local_q), daemon=True
    ).start()

    def event_generator():
        yield f"event: plan\ndata: {json.dumps(plan_for(sub))}\n\n"
        yield f"event: mode\ndata: {json.dumps({'case_id': cid})}\n\n"

        deadline = time.monotonic() + STREAM_IDLE_TIMEOUT_S
        while True:
            if time.monotonic() > deadline:
                casestore.fail_case(cid, "Run timed out before a decision was reached.")
                yield ("event: error\ndata: "
                       + json.dumps({"message": "Run timed out."}) + "\n\n")
                return
            try:
                ev = local_q.get(timeout=1.0)
            except queue.Empty:
                # Keep-alive comment: proxies drop idle SSE connections.
                yield ": keep-alive\n\n"
                continue

            deadline = time.monotonic() + STREAM_IDLE_TIMEOUT_S
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            if ev["type"] in ("done", "error"):
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.post("/v1/cases/stream", dependencies=[Depends(require_api_key)])
def run_submission(payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    try:
        sub = VendorSubmission(**payload)
    except Exception as exc:
        raise HTTPException(422, f"invalid submission: {exc}")
    return _stream(sub)


@app.post("/v1/cases/form/stream")
async def run_form(
    submission: str = Form(...),
    files: list[UploadFile] = File(default=[]),
) -> StreamingResponse:
    """Run a submission built in the vendor form, WITH real uploaded documents.

    The form sends its fields as a JSON string plus the attached files. Each
    file is saved and matched by filename to its document entry, so the very
    same document reader that runs on the bundled samples runs on whatever the
    user (or an interviewer) uploads live — the uploads are read for real, not
    trusted. Any submission is therefore verifiable: this is the "new test
    case" path.
    """
    try:
        data = json.loads(submission)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"invalid submission JSON: {exc}")

    uid = uuid.uuid4().hex[:12]
    saved: dict[str, str] = {}
    storage = get_storage()
    for f in files:
        if not f.filename:
            continue
        blob = await f.read()
        # Enterprise hygiene: extension allowlist + size cap before we touch it.
        _validate_upload(f.filename, len(blob))
        logical_path = f"uploads/{uid}/{Path(f.filename).name}"
        storage.save(logical_path, blob)
        saved[Path(f.filename).name] = logical_path

    # Attach saved-file paths to the matching document entries.
    for d in data.get("documents", []):
        fn = Path(d.get("filename", "")).name
        if fn in saved:
            d["path"] = saved[fn]

    try:
        sub = VendorSubmission(**data)
    except Exception as exc:
        raise HTTPException(422, f"invalid submission: {exc}")
    return _stream(sub)


# ---------------------------------------------------------------------------
# Vendor portal (token-scoped; structurally vendor-safe)
# ---------------------------------------------------------------------------
#
# NOTE: these endpoints are exempt from the approver API_TOKEN auth — the
# vendor's per-case token IS the credential. The middleware allows /v1/vendor.

@app.get("/v1/vendor/{token}")
def vendor_case(token: str) -> dict:
    v = casestore.vendor_view(token)
    if not v:
        raise HTTPException(404, "unknown or expired link")
    return v


@app.post("/v1/vendor/{token}/resubmit")
async def vendor_resubmit(
    token: str,
    submission: str = Form(...),
    files: list[UploadFile] = File(default=[]),
) -> StreamingResponse:
    """A vendor fixes what was requested and resubmits through their link.

    Runs the full pipeline as a resubmission under the SAME tenant as the
    original case — the entity-key linkage then supersedes the old case and
    produces the resolved/remaining diff automatically.
    """
    ref = casestore.token_case(token)
    if not ref:
        raise HTTPException(404, "unknown or expired link")

    try:
        data = json.loads(submission)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"invalid submission JSON: {exc}")

    uid = uuid.uuid4().hex[:12]
    saved: dict[str, str] = {}
    storage = get_storage()
    for f in files:
        if not f.filename:
            continue
        blob = await f.read()
        _validate_upload(f.filename, len(blob))
        logical_path = f"uploads/{uid}/{Path(f.filename).name}"
        storage.save(logical_path, blob)
        saved[Path(f.filename).name] = logical_path
    for d in data.get("documents", []):
        fn = Path(d.get("filename", "")).name
        if fn in saved:
            d["path"] = saved[fn]

    try:
        sub = VendorSubmission(**data)
    except Exception as exc:
        raise HTTPException(422, f"invalid submission: {exc}")
    return _stream(sub)


# ---------------------------------------------------------------------------
# Onboarding templates (what a client asks its vendors for)
# ---------------------------------------------------------------------------

@app.get("/v1/profiles")
def profiles_list() -> list[dict]:
    from backend.app.profiles.store import list_profiles
    return list_profiles()


@app.get("/v1/profiles/{profile_id}")
def profiles_get(profile_id: str, country: str = "") -> dict:
    from backend.app.profiles.store import get_profile
    return get_profile(profile_id, country).model_dump(by_alias=True)


@app.put("/v1/profiles/{profile_id}")
def profiles_save(profile_id: str, payload: dict[str, Any] = Body(...)) -> dict:
    from backend.app.profiles.models import RequirementProfile
    from backend.app.profiles.store import save_profile
    payload["profile_id"] = profile_id
    try:
        return save_profile(RequirementProfile(**payload)).model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"invalid template: {exc}")


@app.delete("/v1/profiles/{profile_id}")
def profiles_delete(profile_id: str) -> dict:
    from backend.app.profiles.store import delete_profile
    try:
        return {"deleted": delete_profile(profile_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/v1/documents/preflight")
async def documents_preflight(
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    country: str = Form(default=""),
    legal_name: str = Form(default=""),
) -> dict:
    """Verify one uploaded document instantly, before the form is submitted.

    Lets the UI flag "that looks like a resume, not a bank letter" the moment a
    file is attached — the Document Verification Agent run on a single file.
    """
    from backend.app.dva.preflight import preflight

    blob = await file.read()
    _validate_upload(file.filename or "file", len(blob))

    accepted: set[str] = set()
    try:
        specs = load_country_rules(country).get("required_documents", []) if country else []
    except FileNotFoundError:
        specs = []
    for spec in specs:
        if spec.get("doc_type") == doc_type:
            accepted = set(spec.get("accepted", []))
            break

    return preflight(blob, file.filename or "upload", doc_type, accepted,
                     legal_name or None)


@app.get("/v1/samples", dependencies=[Depends(require_api_key)])
def samples() -> list[dict]:
    """Bundled submissions with their intended outcome and a scenario note."""
    manifest = config.SUBMISSION_DIR / "manifest.json"
    return json.loads(manifest.read_text()) if manifest.exists() else []


@app.get("/v1/samples/{name}", dependencies=[Depends(require_api_key)])
def sample_body(name: str) -> dict:
    path: Path = config.SUBMISSION_DIR / name
    if not path.resolve().is_relative_to(config.SUBMISSION_DIR.resolve()):
        raise HTTPException(400, "invalid sample name")
    if not path.exists():
        raise HTTPException(404, f"no such sample: {name}")
    return json.loads(path.read_text())


@app.post("/v1/cases/sample/{name}/stream", dependencies=[Depends(require_api_key)])
def run_sample(name: str) -> StreamingResponse:
    path: Path = config.SUBMISSION_DIR / name
    if not path.resolve().is_relative_to(config.SUBMISSION_DIR.resolve()):
        raise HTTPException(400, "invalid sample name")
    if not path.exists():
        raise HTTPException(404, f"no such sample: {name}")
    return _stream(VendorSubmission(**json.loads(path.read_text())))


@app.get("/v1/cases", dependencies=[Depends(require_api_key)])
def list_cases(limit: int = 200) -> list[dict]:
    return casestore.list_cases(limit)


@app.get("/v1/cases/{case_id}", dependencies=[Depends(require_api_key)])
def get_case(case_id: str) -> dict:
    c = casestore.get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    return c


@app.post("/v1/cases/{case_id}/chat", dependencies=[Depends(require_api_key)])
def case_chat(case_id: str, payload: dict[str, Any] = Body(...)) -> dict:
    """Talk to the AI agent regarding a specific case."""
    c = casestore.get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
        
    messages = payload.get("messages", [])
    if not isinstance(messages, list) or not messages:
        raise HTTPException(422, "messages must be a non-empty list")
    # Bound the history: an unbounded transcript is both a cost and a prompt-
    # injection surface, and nothing useful lives more than a few turns back.
    return get_llm().ops_chat(c, messages[-12:])


@app.get("/v1/stats", dependencies=[Depends(require_api_key)])
def stats() -> dict:
    return casestore.stats()


@app.post("/v1/cases/{case_id}/action", dependencies=[Depends(require_api_key)])
def case_action(case_id: str, payload: dict[str, Any] = Body(...)) -> dict:
    """Record a reviewer decision on a case.

    This is what turns the queue from a read-only view into a system of record:
    approve / reject / request_info / resolve / reopen, each appended to the
    case's action log and reflected in its status. The automated finding trail
    is untouched — the human decision sits alongside it, not over it.
    """
    action = (payload.get("action") or "").strip()
    try:
        result = casestore.record_action(
            case_id, action,
            reviewer=payload.get("reviewer") or "reviewer",
            note=payload.get("note"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except KeyError:
        raise HTTPException(404, "case not found")
    return result


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@app.get("/v1/reference/vendor-master", dependencies=[Depends(require_api_key)])
def vendor_master() -> list[dict]:
    p = config.SEED_DIR / "vendor_master.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.get("/v1/reference/denied-parties", dependencies=[Depends(require_api_key)])
def denied_parties() -> list[dict]:
    p = config.SEED_DIR / "denied_parties.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.post("/v1/reset", dependencies=[Depends(require_api_key)])
def reset() -> dict:
    return {"status": "reset", **db.reset_db()}


# ---------------------------------------------------------------------------
# Serve the built frontend from the same origin (single-URL deployment)
# ---------------------------------------------------------------------------
#
# When frontend/dist exists (i.e. `npm run build` has run, as it does in the
# Docker image), the API also serves the React app. That gives ONE URL for the
# whole product — the API under /v1 and /health, everything else falling back
# to the SPA's index.html. In local dev this block is skipped and Vite serves
# the frontend on :5174 with a proxy, so nothing changes there.

_DIST = config.ROOT / "frontend" / "dist"
if _DIST.exists():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # API namespaces are handled above; anything else is a client route.
        if full_path.startswith(("v1/", "health", "assets/")):
            raise HTTPException(404, "not found")
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")

    log.info("serving built frontend from %s", _DIST)
