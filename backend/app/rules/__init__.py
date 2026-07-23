"""Rule-pack loader.

Rules live in YAML rather than in Python because the people who own them are
not engineers. A procurement or compliance lead can read gb.yaml, see that a
VAT number must match `^GB(\\d{9}|\\d{12})$`, and tell you it is wrong. They
cannot do that with a regex buried in a validator function.

Adding a country is adding a file. No code change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).parent


@lru_cache(maxsize=32)
def load_country_rules(country_code: str) -> dict[str, Any]:
    code = (country_code or "").strip().lower()
    if not code:
        raise FileNotFoundError("country code is required")
    path = _DIR / f"{code}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no rule pack for country: {country_code!r}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# Deep-merge override, used by the calibration script to vary a threshold and
# re-measure without editing the YAML on disk.
_COMMON_OVERRIDE: dict[str, Any] = {}


def set_common_override(patch: dict[str, Any] | None) -> None:
    global _COMMON_OVERRIDE
    _COMMON_OVERRIDE = patch or {}
    load_common_rules.cache_clear()


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load_common_rules() -> dict[str, Any]:
    path = _DIR / "common.yaml"
    base = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
    return _deep_merge(base, _COMMON_OVERRIDE) if _COMMON_OVERRIDE else base


@lru_cache(maxsize=1)
def supported_countries() -> tuple[str, ...]:
    return tuple(sorted(
        p.stem.upper() for p in _DIR.glob("*.yaml") if p.stem != "common"
    ))


def is_supported(country_code: str) -> bool:
    return (country_code or "").strip().upper() in supported_countries()


def country_name(country_code: str) -> str:
    try:
        return load_country_rules(country_code).get("country_name", country_code)
    except FileNotFoundError:
        return country_code


def required_documents(country_code: str) -> list[dict[str, Any]]:
    try:
        return load_country_rules(country_code).get("required_documents", []) or []
    except FileNotFoundError:
        return []
