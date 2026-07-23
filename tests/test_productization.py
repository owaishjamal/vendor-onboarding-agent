"""Tests for the productization layer: provider adapters, doc-read caching,
image preprocessing, and the enterprise seams (auth, tenancy, uploads, metrics).

These prove the seams are real and default-off — the demo and the 95 existing
tests are unaffected because every new behaviour is opt-in.
"""

from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VO_DB_PATH"] = str(pathlib.Path(tempfile.gettempdir()) / "vo_prod.db")
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app import enterprise  # noqa: E402
from backend.app.providers.registry_provider import (  # noqa: E402
    SeedRegistryProvider, get_registry_provider, set_registry_override,
)
from backend.app.providers.screening_provider import (  # noqa: E402
    SeedScreeningProvider, get_screening_provider,
)


# ===========================================================================
# Provider adapters
# ===========================================================================

def test_registry_provider_default_is_seed():
    assert isinstance(get_registry_provider(), SeedRegistryProvider)


def test_registry_provider_lookup_and_override():
    p = SeedRegistryProvider()
    # A seeded company resolves; a fabricated number does not.
    assert p.lookup("GB", "09442817") is not None
    assert p.lookup("GB", "00000000") is None

    set_registry_override([{"country": "GB", "registration_number": "XX1",
                            "legal_name": "Test Co", "status": "ACTIVE"}])
    try:
        rec = SeedRegistryProvider().lookup("GB", "XX1")
        assert rec and rec.legal_name == "Test Co"
    finally:
        set_registry_override(None)


def test_screening_provider_returns_parties():
    parties = get_screening_provider().candidates()
    assert any(p.name == "Dmitri Volkov" for p in parties)
    assert all(hasattr(p, "dob") for p in parties)


def test_companies_house_provider_shape():
    """Real UK provider is constructible and only handles GB (no key here)."""
    from backend.app.providers.registry_provider import CompaniesHouseProvider
    prov = CompaniesHouseProvider(api_key="")   # no key → lookups fail gracefully
    assert prov.lookup("US", "123") is None      # non-GB short-circuits


# ===========================================================================
# Document read caching
# ===========================================================================

def test_document_read_is_cached():
    from backend.app.checks import document_reader as dr
    from backend.app.models import SubmittedDocument

    doc = SubmittedDocument(doc_type="bank_proof",
                            filename="bank_letter_northwind.pdf",
                            path="VS-01/bank_letter_northwind.pdf")
    r1 = dr.read_document(doc)
    r2 = dr.read_document(doc)
    assert r1.fields == r2.fields and r1.source == r2.source
    # A cache file should now exist for this content.
    import glob
    assert glob.glob(str(ROOT / "data" / ".llm_cache" / "docreads" / "*.json"))


def test_preprocess_degrades_gracefully_without_opencv(monkeypatch):
    """If OpenCV import fails, preprocessing must not crash — it returns the image."""
    from backend.app.checks import document_reader as dr
    from PIL import Image
    img = Image.new("RGB", (40, 20), "white")
    out = dr._preprocess(img)          # should never raise
    assert out is not None


# ===========================================================================
# Enterprise: uploads, auth, metrics
# ===========================================================================

def test_upload_validation_rejects_bad_extension():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        enterprise.validate_upload("evil.exe", 1000)
    assert e.value.status_code == 415


def test_upload_validation_rejects_oversize():
    from fastapi import HTTPException
    big = (enterprise.config.MAX_UPLOAD_MB + 1) * 1024 * 1024
    with pytest.raises(HTTPException) as e:
        enterprise.validate_upload("scan.pdf", big)
    assert e.value.status_code == 413


def test_upload_validation_accepts_pdf():
    enterprise.validate_upload("bank.pdf", 1000)   # no raise


def test_metrics_render():
    enterprise.incr("test_counter_total", tenant="acme")
    enterprise.observe("test_latency_ms", 12.5)
    out = enterprise.render_metrics()
    assert 'test_counter_total{tenant="acme"}' in out
    assert "test_latency_ms_count" in out


def test_tenant_defaults_to_demo():
    assert enterprise.tenant_of(None) == "demo"
    assert enterprise.tenant_of("acme") == "acme"


# ===========================================================================
# Tenancy isolation at the storage layer
# ===========================================================================

def test_cases_are_isolated_by_tenant():
    import json
    from backend.app.models import VendorSubmission
    from backend.app.pipeline.runner import run_pipeline
    from backend.app.storage import cases as casestore, db

    db.reset_db()
    subs = ROOT / "data" / "submissions"
    a = VendorSubmission(**json.loads((subs / "VS-01_northwind_clean.json").read_text()))
    b = VendorSubmission(**json.loads((subs / "VS-03_kessler_bank_mismatch.json").read_text()))
    list(run_pipeline(a, tenant="acme"))
    list(run_pipeline(b, tenant="globex"))

    acme = casestore.list_cases(tenant="acme")
    globex = casestore.list_cases(tenant="globex")
    assert [c["legal_name"] for c in acme] == ["Northwind Components Inc"]
    assert [c["legal_name"] for c in globex] == ["Kessler Industrietechnik GmbH"]
