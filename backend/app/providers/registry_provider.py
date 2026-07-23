"""Company-registry providers behind one interface.

The registry check doesn't care WHERE the answer comes from — a seeded file for
the demo, or Companies House / Dun & Bradstreet in production. It calls
`get_registry_provider().lookup(country, number)` and gets back a record or
None. Swapping the data source is an environment variable and a class, not a
change to the check.

Providers:
  * SeedRegistryProvider    — reads backend/seed/company_registry.json (default;
                              also honours an in-memory override used by the eval).
  * CompaniesHouseProvider  — the real UK registry API, gated on an API key.
                              Implemented; needs COMPANIES_HOUSE_API_KEY to run.
  * CompositeProvider       — routes GB to Companies House, everything else to
                              the seed. This is the realistic production shape:
                              one live source per jurisdiction, a fallback for
                              the rest.

Selection is via REGISTRY_PROVIDER (seed | companies_house).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from backend.app import config


@dataclass
class RegistryRecord:
    legal_name: str
    status: str                       # ACTIVE | DISSOLVED | ...
    incorporation_date: Optional[str] = None
    source: str = "seed"


def _norm(v: Optional[str]) -> str:
    return re.sub(r"[\s\-]", "", (v or "")).upper()


class RegistryProvider(Protocol):
    def lookup(self, country: str, number: str) -> Optional[RegistryRecord]: ...


# ---------------------------------------------------------------------------
# Seed provider (default) + eval override
# ---------------------------------------------------------------------------

_OVERRIDE: Optional[list[dict[str, Any]]] = None


def set_registry_override(entries: Optional[list[dict[str, Any]]]) -> None:
    """Used by the evaluator to supply a registry for generated companies."""
    global _OVERRIDE
    _OVERRIDE = entries


class SeedRegistryProvider:
    source = "seed"

    def _entries(self) -> list[dict[str, Any]]:
        if _OVERRIDE is not None:
            return _OVERRIDE
        p = config.SEED_DIR / "company_registry.json"
        return json.loads(p.read_text()) if p.exists() else []

    def lookup(self, country: str, number: str) -> Optional[RegistryRecord]:
        cc, reg = country.upper(), _norm(number)
        for e in self._entries():
            if _norm(e.get("registration_number")) == reg and e.get("country", "").upper() == cc:
                return RegistryRecord(
                    legal_name=e.get("legal_name", ""),
                    status=(e.get("status") or "ACTIVE").upper(),
                    incorporation_date=e.get("incorporation_date"),
                    source="seed",
                )
        return None


# ---------------------------------------------------------------------------
# Companies House (real UK registry) — implemented, needs an API key
# ---------------------------------------------------------------------------

class CompaniesHouseProvider:
    """UK Companies House public API.

    Auth is HTTP Basic with the API key as the username and an empty password.
    Env: COMPANIES_HOUSE_API_KEY. Only handles GB; returns None otherwise so a
    CompositeProvider can fall through to another source.
    """

    source = "companies_house"
    BASE = "https://api.company-information.service.gov.uk"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def lookup(self, country: str, number: str) -> Optional[RegistryRecord]:
        if country.upper() != "GB":
            return None
        import base64
        import urllib.request

        num = _norm(number)
        req = urllib.request.Request(f"{self.BASE}/company/{num}")
        token = base64.b64encode(f"{self.api_key}:".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return None  # not found / network / auth -> treat as unverifiable

        status = (data.get("company_status") or "").upper()
        return RegistryRecord(
            legal_name=data.get("company_name", ""),
            status="ACTIVE" if status == "ACTIVE" else (status or "UNKNOWN"),
            incorporation_date=data.get("date_of_creation"),
            source="companies_house",
        )


class CompositeProvider:
    """Try a live provider first, fall back to the seed for other jurisdictions."""

    def __init__(self, primary: RegistryProvider, fallback: RegistryProvider):
        self.primary, self.fallback = primary, fallback

    def lookup(self, country: str, number: str) -> Optional[RegistryRecord]:
        return self.primary.lookup(country, number) or self.fallback.lookup(country, number)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def get_registry_provider() -> RegistryProvider:
    which = os.getenv("REGISTRY_PROVIDER", "seed").lower()
    seed = SeedRegistryProvider()
    if which == "companies_house":
        key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
        if key:
            return CompositeProvider(CompaniesHouseProvider(key), seed)
    return seed
