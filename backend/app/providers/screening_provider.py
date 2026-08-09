"""Denied-party screening providers behind one interface.

Same pattern as the registry: the screening check calls
`get_screening_provider().candidates()` to get the list it fuzzy-matches
against, without knowing whether that list is a seeded file or a live feed
from a licensed vendor (ComplyAdvantage, Refinitiv World-Check, Dow Jones).

Providers:
  * SeedScreeningProvider     — reads backend/seed/denied_parties.json (default).
  * ComplyAdvantageProvider   — sketch of the real API shape, gated on a key.

Selection is via SCREENING_PROVIDER (seed | complyadvantage).

Note: the two-factor resolution (DOB / nationality) lives in the check, not the
provider — a provider supplies candidates and their identifiers; the decision
of confirm-vs-clear is ours to own and audit.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol

from backend.app import config
from backend.app.models import DeniedParty


class ScreeningProvider(Protocol):
    def candidates(self) -> list[DeniedParty]: ...


class SeedScreeningProvider:
    source = "seed"

    def candidates(self) -> list[DeniedParty]:
        p = config.SEED_DIR / "denied_parties.json"
        return [DeniedParty(**d) for d in json.loads(p.read_text())] if p.exists() else []


class ComplyAdvantageProvider:
    """Sketch of a live screening feed. Implemented shape, needs a key to run.

    A real integration screens a name via the vendor's search API and maps the
    hits (with DOB/nationality) into DeniedParty records. Here it degrades to
    the seed list if no key is set, so nothing breaks without credentials.
    """

    source = "complyadvantage"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._seed = SeedScreeningProvider()

    def candidates(self) -> list[DeniedParty]:
        # A production build would call the vendor's API per screened name and
        # cache aggressively. Without a key we return the seed list so the check
        # still runs; the swap point is here and nowhere else.
        if not self.api_key:
            return self._seed.candidates()
        # ... real API call would populate and return here ...
        return self._seed.candidates()


class HttpScreeningAdapter:
    """A generic HTTP JSON API adapter for denied-party lists."""
    source = "http_screening"

    def __init__(self, endpoint: str, api_key: str):
        self.endpoint = endpoint
        self.api_key = api_key

    def candidates(self) -> list[DeniedParty]:
        import urllib.request
        
        req = urllib.request.Request(self.endpoint)
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                return [DeniedParty(**d) for d in data]
        except Exception as exc:
            import logging
            logging.getLogger("vo.screening").warning("HTTP screening fetch failed: %s", exc)
            return []


def get_screening_provider() -> ScreeningProvider:
    which = os.getenv("SCREENING_PROVIDER", "seed").lower()
    if which == "complyadvantage":
        return ComplyAdvantageProvider(os.getenv("COMPLYADVANTAGE_API_KEY", ""))
    elif which == "http":
        endpoint = os.getenv("SCREENING_ENDPOINT", "")
        key = os.getenv("SCREENING_API_KEY", "")
        if endpoint:
            return HttpScreeningAdapter(endpoint, key)
    return SeedScreeningProvider()
