"""Enterprise hygiene: auth, tenancy, upload safety, metrics, logging.

Everything here is OFF or permissive by default, so the demo and the tests run
unchanged. Each piece turns on with an environment variable — which is exactly
how you'd stage these for a real customer without forking the app.

  * API_TOKEN            set → every /v1 request needs `Authorization: Bearer …`
  * LOG_JSON=1           structured JSON logs (ingestible by Datadog/Splunk)
  * tenancy              always on but defaults to "demo"; the X-Org-Id header
                         scopes cases to a tenant (the seam multi-tenancy needs)
  * upload validation    always on: size cap + extension allowlist
  * /metrics             Prometheus-style counters, always exposed

None of this is "enterprise-grade" on its own — it's the seams a real
deployment builds on (a proper IdP, per-tenant KMS keys, a SIEM). It shows the
architecture is ready for them rather than pretending they're done.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import Header, HTTPException, Request

from backend.app import config


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json
        payload = {
            "ts": self.formatTime(record), "level": record.levelname,
            "logger": record.name, "msg": record.getMessage(),
        }
        for k in ("tenant", "path", "status", "latency_ms"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload)


def configure_logging() -> None:
    import os
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if os.getenv("LOG_JSON") == "1":
        h = logging.StreamHandler()
        h.setFormatter(_JsonFormatter())
        root.handlers = [h]


# ---------------------------------------------------------------------------
# Metrics (Prometheus text format, no dependency)
# ---------------------------------------------------------------------------

_counters: dict[str, float] = defaultdict(float)
_hist: dict[str, list[float]] = defaultdict(list)


def incr(name: str, by: float = 1.0, **labels: str) -> None:
    _counters[_key(name, labels)] += by


def observe(name: str, value: float, **labels: str) -> None:
    _hist[_key(name, labels)].append(value)


def _key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{inner}}}"


def render_metrics() -> str:
    lines = ["# vendor-onboarding metrics"]
    for k, v in sorted(_counters.items()):
        lines.append(f"{k} {v:g}")
    for k, vals in sorted(_hist.items()):
        if vals:
            base = k.split("{")[0]
            lbl = k[len(base):]
            lines.append(f"{base}_count{lbl} {len(vals)}")
            lines.append(f"{base}_sum{lbl} {sum(vals):g}")
            lines.append(f"{base}_avg{lbl} {sum(vals) / len(vals):g}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Auth (optional bearer token)
# ---------------------------------------------------------------------------

def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency. No-op unless API_TOKEN is set."""
    import os
    token = os.getenv("API_TOKEN", "")
    if not token:
        return
    expected = f"Bearer {token}"
    if authorization != expected:
        incr("auth_rejected_total")
        raise HTTPException(401, "missing or invalid bearer token")


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

def tenant_of(x_org_id: Optional[str] = Header(default=None)) -> str:
    """The tenant a request belongs to. Defaults to 'demo' for single-tenant use."""
    return (x_org_id or "demo").strip()[:64]


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

def validate_upload(filename: str, size_bytes: int) -> None:
    from pathlib import Path
    ext = Path(filename or "").suffix.lower()
    if ext not in config.ALLOWED_UPLOAD_EXT:
        incr("upload_rejected_total", reason="extension")
        raise HTTPException(415, f"unsupported file type '{ext}'. "
                                 f"allowed: {sorted(config.ALLOWED_UPLOAD_EXT)}")
    if size_bytes > config.MAX_UPLOAD_MB * 1024 * 1024:
        incr("upload_rejected_total", reason="size")
        raise HTTPException(413, f"file exceeds {config.MAX_UPLOAD_MB} MB limit")


# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------

async def timing_middleware(request: Request, call_next):
    import os
    from fastapi.responses import JSONResponse

    path = request.url.path

    # Optional bearer auth on the API surface (never on /health, / or /metrics).
    token = os.getenv("API_TOKEN", "")
    if token and path.startswith("/v1"):
        if request.headers.get("authorization") != f"Bearer {token}":
            incr("auth_rejected_total")
            return JSONResponse({"detail": "missing or invalid bearer token"}, status_code=401)

    t0 = time.perf_counter()
    response = await call_next(request)
    dt = (time.perf_counter() - t0) * 1000
    if path.startswith("/v1"):
        incr("http_requests_total", path=path, status=str(response.status_code))
        observe("http_request_ms", dt, path=path)
    return response
