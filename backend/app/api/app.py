"""FastAPI surface.

Case endpoints stream Server-Sent Events so a reviewer watches each check land
in turn. Unlike an invoice pipeline the run never stops early, so the stream
always delivers all six checks — which is itself informative: you can see that
a rejected case still had its documents verified and its duplicates checked.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator

import uuid

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.app import config, enterprise
from backend.app.llm.client import get_llm
from backend.app.models import Severity, VendorSubmission
from backend.app.pipeline.runner import CHECK_PLAN, run_pipeline
from backend.app.rules import (
    load_common_rules, load_country_rules, supported_countries,
)
from backend.app.storage import cases as casestore
from backend.app.storage import db

enterprise.configure_logging()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("vo.api")

app = FastAPI(title="Vendor Onboarding", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(enterprise.timing_middleware)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    log.info("database ready; countries=%s; llm=%s",
             ",".join(supported_countries()), get_llm().provider)


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@app.get("/metrics")
def metrics():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(enterprise.render_metrics())


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_provider": get_llm().provider,
            "llm_cache": config.LLM_CACHE_ENABLED,
            "check_delay_ms": config.CHECK_DELAY_MS,
            "countries": list(supported_countries())}


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


@app.get("/v1/policy")
def policy() -> dict:
    return {"common": load_common_rules(), "countries": countries()}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def _sse(events: Iterator[dict]) -> Iterator[str]:
    yield f"event: plan\ndata: {json.dumps(CHECK_PLAN)}\n\n"
    for ev in events:
        yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"


def _stream(sub: VendorSubmission, tenant: str = "demo") -> StreamingResponse:
    enterprise.incr("cases_submitted_total", tenant=tenant)
    return StreamingResponse(
        _sse(run_pipeline(sub, tenant=tenant)), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.post("/v1/cases/stream")
def run_submission(payload: dict[str, Any] = Body(...),
                   tenant: str = Depends(enterprise.tenant_of)) -> StreamingResponse:
    try:
        sub = VendorSubmission(**payload)
    except Exception as exc:
        raise HTTPException(422, f"invalid submission: {exc}")
    return _stream(sub, tenant)


@app.post("/v1/cases/form/stream")
async def run_form(
    submission: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    tenant: str = Depends(enterprise.tenant_of),
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
    updir = config.DATA_DIR / "documents" / "uploads" / uid
    saved: dict[str, str] = {}
    for f in files:
        if not f.filename:
            continue
        blob = await f.read()
        # Enterprise hygiene: extension allowlist + size cap before we touch it.
        enterprise.validate_upload(f.filename, len(blob))
        dest = updir / Path(f.filename).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        saved[Path(f.filename).name] = f"uploads/{uid}/{Path(f.filename).name}"

    # Attach saved-file paths to the matching document entries.
    for d in data.get("documents", []):
        fn = Path(d.get("filename", "")).name
        if fn in saved:
            d["path"] = saved[fn]

    try:
        sub = VendorSubmission(**data)
    except Exception as exc:
        raise HTTPException(422, f"invalid submission: {exc}")
    return _stream(sub, tenant)


@app.get("/v1/samples")
def samples() -> list[dict]:
    """Bundled submissions with their intended outcome and a scenario note."""
    manifest = config.SUBMISSION_DIR / "manifest.json"
    return json.loads(manifest.read_text()) if manifest.exists() else []


@app.get("/v1/samples/{name}")
def sample_body(name: str) -> dict:
    path: Path = config.SUBMISSION_DIR / name
    if not path.resolve().is_relative_to(config.SUBMISSION_DIR.resolve()):
        raise HTTPException(400, "invalid sample name")
    if not path.exists():
        raise HTTPException(404, f"no such sample: {name}")
    return json.loads(path.read_text())


@app.post("/v1/cases/sample/{name}/stream")
def run_sample(name: str, tenant: str = Depends(enterprise.tenant_of)) -> StreamingResponse:
    path: Path = config.SUBMISSION_DIR / name
    if not path.resolve().is_relative_to(config.SUBMISSION_DIR.resolve()):
        raise HTTPException(400, "invalid sample name")
    if not path.exists():
        raise HTTPException(404, f"no such sample: {name}")
    return _stream(VendorSubmission(**json.loads(path.read_text())), tenant)


@app.get("/v1/cases")
def list_cases(limit: int = 200, tenant: str = Depends(enterprise.tenant_of)) -> list[dict]:
    # In the demo (tenant='demo') this returns everything; a real customer only
    # ever sees their own org's cases. The isolation seam is here.
    scope = None if tenant == "demo" else tenant
    return casestore.list_cases(limit, tenant=scope)


@app.get("/v1/cases/{case_id}")
def get_case(case_id: str) -> dict:
    c = casestore.get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    return c


@app.get("/v1/stats")
def stats() -> dict:
    return casestore.stats()


@app.get("/v1/overrides")
def overrides() -> dict:
    """Where reviewers disagreed with the automated decision, by check.

    A live calibration signal: a check that is overridden often is either too
    aggressive (crying wolf) or too permissive (missing things).
    """
    return casestore.override_report()


@app.post("/v1/cases/{case_id}/action")
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

@app.get("/v1/reference/vendor-master")
def vendor_master() -> list[dict]:
    p = config.SEED_DIR / "vendor_master.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.get("/v1/reference/denied-parties")
def denied_parties() -> list[dict]:
    p = config.SEED_DIR / "denied_parties.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.post("/v1/reset")
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
