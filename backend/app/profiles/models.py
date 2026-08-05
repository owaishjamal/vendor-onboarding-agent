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



class FieldSpec(BaseModel):
    key: str
    label: str
    type: FieldType = "text"
    required: bool = False
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


class DocSpec(BaseModel):
    key: str                              # slot key, e.g. "food_license"
    label: str
    required: bool = True
    accepted: list[str] = Field(default_factory=list)
    # Plain-English description for documents the classifier doesn't know.
    expects: Optional[str] = None
    freshness_months: Optional[int] = None


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
    extends: Literal["country_defaults", "blank"] = "country_defaults"
    fields: list[FieldSpec] = Field(default_factory=list)
    documents: list[DocSpec] = Field(default_factory=list)
    rules: list[RuleSpec] = Field(default_factory=list)
    thresholds: dict[str, Any] = Field(default_factory=dict)
