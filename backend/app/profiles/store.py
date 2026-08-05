"""Profile storage + the default profile.

Profiles live as JSON files under data/profiles/ (simple, diffable, and the
same repo-as-source-of-truth pattern as the rule packs). The special id
"default" is synthesized from the country packs at call time, so existing
behaviour is byte-for-byte unchanged when no profile is chosen.

The default profile also carries the built-in EVIDENCE MAP — which document
corroborates which core field — making the evidence-first verifier work for
plain submissions too:

    legal_name          <- incorporation doc's name (and tax doc's name)
    registration_number <- incorporation doc's number
    tax_id              <- tax doc's number
    bank.account_name   <- bank doc's name
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import os

from backend.app import config
from backend.app.profiles.models import (
    DocSpec, FieldSpec, RequirementProfile,
)
from backend.app.rules import load_country_rules, required_documents

# Overridable so tests (and synced-folder deployments) can point profiles at
# local disk — same pattern as VO_DB_PATH.
PROFILE_DIR = Path(os.getenv("VO_PROFILE_DIR", str(config.DATA_DIR / "profiles")))


# Doc-slot -> which evidence keys a slot's documents provide, for the default
# country packs. Custom profiles declare this per-field instead.
DEFAULT_EVIDENCE = {
    "legal_name": ["incorporation.name", "tax_form.name"],
    "registration_number": ["incorporation.number"],
    "tax_id": ["tax_form.number"],
    "bank.account_name": ["bank_proof.name"],
    "pan": ["pan_card.number"],
}


def default_profile(country: str = "") -> RequirementProfile:
    """The implicit profile every submission had before profiles existed."""
    docs: list[DocSpec] = []
    if country:
        try:
            for spec in required_documents(country):
                docs.append(DocSpec(
                    key=spec["doc_type"], label=spec["label"],
                    accepted=spec.get("accepted", []), required=True,
                ))
        except Exception:
            pass

    fields = [
        FieldSpec(key="legal_name", label="Registered legal name", type="text",
                  required=True, validation_source=DEFAULT_EVIDENCE["legal_name"]),
        FieldSpec(key="registration_number", label="Registration number", type="id",
                  validation_source=DEFAULT_EVIDENCE["registration_number"]),
        FieldSpec(key="tax_id", label="Tax registration number", type="id",
                  validation_source=DEFAULT_EVIDENCE["tax_id"]),
        FieldSpec(key="bank.account_name", label="Name on the bank account", type="text",
                  validation_source=DEFAULT_EVIDENCE["bank.account_name"]),
    ]
    # India: PAN is a distinct identifier with its own card, so it gets its own
    # field and its own document to verify against.
    if (country or "").upper() == "IN":
        pan_rules = (load_country_rules("IN") or {}).get("pan", {})
        fields.append(FieldSpec(
            key="pan", label=pan_rules.get("label", "PAN"), type="id", required=True,
            regex=pan_rules.get("regex"),
            hint=f"e.g. {pan_rules['example']}" if pan_rules.get("example") else None,
            validation_source=DEFAULT_EVIDENCE["pan"]))
    return RequirementProfile(
        profile_id="default", name="Default (country packs)",
        extends="country_defaults", fields=fields, documents=docs)


def _path(profile_id: str) -> Path:
    return PROFILE_DIR / f"{profile_id}.json"


def get_profile(profile_id: Optional[str], country: str = "") -> RequirementProfile:
    if not profile_id or profile_id == "default":
        return default_profile(country)
    p = _path(profile_id)
    if not p.exists():
        # Unknown profile falls back to default rather than failing the run.
        return default_profile(country)
    prof = RequirementProfile(**json.loads(p.read_text()))
    if prof.extends == "country_defaults":
        # Merge: country docs + custom docs; core fields + custom fields.
        base = default_profile(country)
        seen_docs = {d.key for d in prof.documents}
        prof.documents = prof.documents + [d for d in base.documents if d.key not in seen_docs]
        seen_fields = {f.key for f in prof.fields}
        prof.fields = prof.fields + [f for f in base.fields if f.key not in seen_fields]
    return prof


def list_profiles() -> list[dict]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    out = [{"profile_id": "default", "name": "Default (country packs)", "version": 1,
            "builtin": True}]
    for p in sorted(PROFILE_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text())
            out.append({"profile_id": d["profile_id"], "name": d.get("name", d["profile_id"]),
                        "version": d.get("version", 1), "builtin": False})
        except Exception:
            continue
    return out


def save_profile(prof: RequirementProfile) -> RequirementProfile:
    if prof.profile_id == "default":
        raise ValueError("the default profile is built-in and cannot be overwritten")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    existing = _path(prof.profile_id)
    if existing.exists():
        try:
            prof.version = json.loads(existing.read_text()).get("version", 0) + 1
        except Exception:
            prof.version = prof.version + 1
    existing.write_text(prof.model_dump_json(by_alias=True, indent=2))
    return prof


def delete_profile(profile_id: str) -> bool:
    if profile_id == "default":
        raise ValueError("cannot delete the built-in default profile")
    p = _path(profile_id)
    if p.exists():
        p.unlink()
        return True
    return False
