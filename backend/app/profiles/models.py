"""Onboarding Templates — a client's vendor requirements as DATA.

Different Zamp clients need different things from their vendors. A template
declares which fields to collect, which documents to require, and which
cross-checks to run — as data, not code. The onboarding form renders from it
and the verification engine reads from it.

The "default" template is built from the country rule packs at runtime, so a
submission that doesn't pick one still gets sensible country requirements.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

FieldType = Literal[
    "text", "number", "date", "email", "phone", "country",
    "id", "iban", "aba", "select", "url", "currency", "textarea",
]

# How badly we need a thing.
#
#   required     — always. Absence is a NEEDS_INFO finding.
#   conditional  — required only when `when` evaluates true against the
#                  submission. A GST certificate matters for an Indian company
#                  and is meaningless for a UK sole trader; asking everyone for
#                  everything is how onboarding forms become 40 fields long.
#   optional     — accepted and verified if supplied, never chased.
#   na           — explicitly not applicable; shown as such so an ops reviewer
#                  can see the requirement was considered and dismissed, not
#                  forgotten.
Requirement = Literal["required", "conditional", "optional", "na"]



class FieldSpec(BaseModel):
    key: str
    label: str
    type: FieldType = "text"
    required: bool = False
    # Richer than `required`, which stays as the legacy boolean so existing
    # profiles keep loading. When `requirement` is left unset it is derived
    # from `required` by the validator below.
    requirement: Optional[Requirement] = None
    # Expression deciding whether a `conditional` item applies to this
    # submission, e.g. "country == 'IN'" or "entity_type != 'individual'".
    when: Optional[str] = None
    # Shown to the vendor next to the field. The brief asks the agent to
    # explain WHY something is needed — this is where that text lives, as
    # data, so it can differ per client without a code change.
    why: Optional[str] = None
    regex: Optional[str] = None          # for type=id / text
    min: Optional[float] = None          # for type=number / currency
    max: Optional[float] = None
    options: list[str] = Field(default_factory=list)   # for type=select
    hint: Optional[str] = None
    # Which document must corroborate this field, as "<doc_key>.<field>"
    # e.g. "gst_certificate.number". Empty = no document backs it, so it is
    # validated by type/rules only.
    validation_source: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_evidence(cls, data: Any) -> Any:
        """`evidence` was the old name — keep old profiles loading."""
        if isinstance(data, dict) and "evidence" in data and "validation_source" not in data:
            data = {**data, "validation_source": data.get("evidence") or []}
        return data

    @property
    def evidence(self) -> list[str]:      # back-compat for existing callers
        return self.validation_source

    @model_validator(mode="after")
    def _derive_requirement(self) -> "FieldSpec":
        return _sync_requirement(self)


def _sync_requirement(spec):
    """Keep the legacy `required` bool and the richer `requirement` in step.

    Old profiles on disk only carry `required`. New ones may carry only
    `requirement`. Both must round-trip, and every consumer should be able to
    read either without caring which was authored.
    """
    if spec.requirement is None:
        spec.requirement = "required" if spec.required else "optional"
    else:
        spec.required = spec.requirement == "required"
    if spec.requirement == "conditional" and not spec.when:
        # A conditional with no condition can never be evaluated. Treating it
        # as optional is the safe reading: we never chase a vendor for
        # something whose applicability we cannot determine.
        spec.requirement = "optional"
        spec.required = False
    return spec


class DocSpec(BaseModel):
    key: str                              # slot key, e.g. "insurance_certificate"
    label: str
    required: bool = True
    requirement: Optional[Requirement] = None   # see FieldSpec.requirement
    when: Optional[str] = None                  # for requirement="conditional"
    why: Optional[str] = None                   # why we ask for it
    accepted: list[str] = Field(default_factory=list)
    # Plain-English description for documents the classifier doesn't know.
    expects: Optional[str] = None
    freshness_months: Optional[int] = None

    @model_validator(mode="after")
    def _derive_requirement(self) -> "DocSpec":
        return _sync_requirement(self)


class RuleSpec(BaseModel):
    kind: Literal["field_match", "equals", "date_before",
                  "country_consistent", "semantic"]
    a: Optional[str] = None
    b: Optional[str] = None
    mode: Literal["exact", "fuzzy"] = "fuzzy"
    assert_: Optional[str] = Field(default=None, alias="assert")
    on_fail: Literal["ADVISORY", "NEEDS_INFO", "NEEDS_REVIEW", "REJECT"] = "NEEDS_REVIEW"

    model_config = {"populate_by_name": True}


class RequirementProfile(BaseModel):
    profile_id: str
    name: str
    version: int = 1
    description: Optional[str] = None
    # Set on category profiles (data/profiles/categories/*.json). A client
    # profile leaves this null and applies on top of whatever category the
    # vendor picked.
    category: Optional[str] = None
    extends: Literal["country_defaults", "blank"] = "country_defaults"
    fields: list[FieldSpec] = Field(default_factory=list)
    documents: list[DocSpec] = Field(default_factory=list)
    rules: list[RuleSpec] = Field(default_factory=list)
    thresholds: dict[str, Any] = Field(default_factory=dict)
