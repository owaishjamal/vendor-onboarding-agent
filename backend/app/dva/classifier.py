"""Document classifier — what IS this document, judged from its CONTENT.

The earlier reader recognised a document only by its heading, which is brittle:
a real bank letter without the exact words "BANK CONFIRMATION" came back
"unknown", and a resume in a bank-proof slot slipped through because nothing
matched. That is the "hardcoded to our layouts" problem.

This classifies by CONTENT SIGNALS instead. A document that mentions an IBAN, a
sort code, an account holder and a bank is a bank document, whatever its
heading. A page full of "work experience / skills / education / references" is
a CV, not a business document, and is flagged as irrelevant before it ever
reaches the cross-referencing logic. This generalises to documents we have
never seen, because it keys on what the document actually contains — the same
idea as an intake classifier in an agentic KYB pipeline.

When a vision model is configured (DOC_EXTRACTOR=vision) the model's own
classification is trusted instead; this content heuristic is the zero-key
default and the fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# Signals that IDENTIFY a business document type. Each hit is a point; the type
# with the most points wins. Weighted so a strong signal (an IBAN, a VAT-number
# pattern) counts more than a generic word.
TYPE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "bank_letter": [
        (r"\bbank\b", 1), (r"account holder", 2), (r"\biban\b", 2),
        (r"sort code", 2), (r"\bswift\b|\bbic\b", 1.5), (r"account number", 1.5),
        (r"confirmation letter|bank reference|bank confirmation", 2),
        (r"we (hereby )?confirm", 1),
    ],
    "bank_statement": [
        (r"bank statement", 3), (r"statement period", 2),
        (r"opening balance|closing balance", 2), (r"account number", 1.5),
    ],
    "vat_certificate": [
        (r"\bvat\b", 2), (r"value added tax", 2), (r"vat registration|vat number", 3),
        (r"ust-?id|umsatzsteuer", 3), (r"\bgst\b|gstin|goods and services tax", 3),
    ],
    "certificate_of_incorporation": [
        (r"certificate of incorporation", 4), (r"incorporat", 2),
        (r"registrar of companies", 2), (r"company (number|no)", 1.5),
        (r"hereby certif", 1),
    ],
    "companies_house_extract": [
        (r"companies house", 4), (r"company (number|no)", 1.5),
    ],
    "handelsregisterauszug": [
        (r"handelsregister", 4), (r"amtsgericht", 2), (r"\bhr[ab]\b", 2),
        (r"commercial register", 3),
    ],
    "acra_bizfile": [
        (r"\bacra\b", 4), (r"business profile", 2), (r"unique entity number|\buen\b", 2),
    ],
    "w9": [
        (r"\bw-?9\b", 4), (r"taxpayer identification", 3),
        (r"request for taxpayer", 3), (r"internal revenue", 2),
    ],
    "pan_card": [
        (r"permanent account number", 4), (r"\bpan\b", 2),
        (r"income tax department", 3), (r"आयकर", 2),
    ],
    "cancelled_cheque": [
        (r"cancelled cheque|canceled check|void(ed)? cheque|void(ed)? check", 4),
    ],
    "tax_form": [
        (r"tax certificate|tax registration", 3),
    ],
    # --- document types the category profiles ask for. Without these every
    # one of them came back unrecognised, which is a warning on every upload
    # and trains a reviewer to ignore warnings.
    "identity_proof": [
        (r"\bpassport\b", 3), (r"driving licen[cs]e|driver'?s licen[cs]e", 3),
        (r"\baadhaar\b|unique identification authority", 4),
        (r"voter (id|identity)|election commission", 3),
        (r"date of birth|\bdob\b", 1), (r"nationality", 1),
        (r"republic of|government of", 1),
    ],
    "insurance_certificate": [
        (r"certificate of insurance|insurance certificate", 4),
        (r"\bpolicy (number|no)\b", 3), (r"\binsured\b", 2),
        (r"goods in transit|public liability|professional indemnity", 3),
        (r"workers'? compensation|employer'?s liability", 3),
        (r"sum insured|period of insurance", 2),
    ],
    "licence": [
        (r"\blicen[cs]e (number|no)\b", 3),
        (r"contractor.{0,12}licen[cs]e|trade licen[cs]e", 4),
        (r"carrier licen[cs]e|transport (operator|licen[cs]e)", 4),
        (r"valid (until|upto|till)|licen[cs]e expiry", 2),
        (r"issuing authority|municipal corporation", 2),
    ],
    "msme_certificate": [
        (r"\budyam\b", 4), (r"\bmsme\b|micro,? small", 3),
        (r"ministry of micro", 3),
    ],
}

# Signals that a document is NOT a business onboarding document at all. If one
# of these dominates, the file is flagged as irrelevant — which is what should
# have happened to the uploaded resume.
IRRELEVANT_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "resume / CV": [
        (r"curriculum vitae|\bresume\b|\bcv\b", 3),
        (r"work experience|professional experience|employment history", 3),
        (r"\bskills\b", 1.5), (r"\beducation\b", 1.5),
        (r"references available", 2), (r"career objective|profile summary", 2),
        (r"\bprojects?\b.*\b(github|portfolio)\b", 2),
    ],
    "invoice": [
        (r"invoice (number|no|date)", 3), (r"amount due|total due", 2),
        (r"\bbill to\b", 2),
    ],
    "delivery / packing note": [
        (r"delivery note|despatch|dispatch note|packing (slip|list)", 3),
    ],
    "personal letter / other": [
        # A cover letter is the document most likely to be attached by mistake
        # alongside a CV, and it was sailing through unrecognised.
        (r"cover letter", 3),
        (r"dear (sir|madam|hiring)", 2),
        (r"i am writing to (express|apply)", 3),
        (r"(yours )?(sincerely|faithfully)|thank you for your consideration", 2),
        (r"please find (attached|enclosed) my", 3),
    ],
}


@dataclass
class Classification:
    detected_type: Optional[str]        # best business-document type, or None
    irrelevant_as: Optional[str]        # if it looks like a non-business doc
    confidence: float                   # 0-1
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def is_irrelevant(self) -> bool:
        return self.irrelevant_as is not None


def _score(text: str, signals: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    low = text.lower()
    out: dict[str, float] = {}
    for label, patterns in signals.items():
        s = 0.0
        for pat, weight in patterns:
            if re.search(pat, low):
                s += weight
        if s > 0:
            out[label] = round(s, 1)
    return out


def classify_text(text: str) -> Classification:
    """Classify a document from its extracted text."""
    if not text or len(text.strip()) < 15:
        return Classification(None, None, 0.0, ["Too little text to classify."])

    type_scores = _score(text, TYPE_SIGNALS)
    irr_scores = _score(text, IRRELEVANT_SIGNALS)

    best_type = max(type_scores, key=type_scores.get) if type_scores else None
    best_type_score = type_scores.get(best_type, 0.0) if best_type else 0.0
    best_irr = max(irr_scores, key=irr_scores.get) if irr_scores else None
    best_irr_score = irr_scores.get(best_irr, 0.0) if best_irr else 0.0

    reasons: list[str] = []

    # A clearly non-business document (resume etc.) that outscores any business
    # signal is flagged irrelevant. The >=3 floor avoids tripping on a stray
    # word like "skills" appearing once on a real certificate.
    if best_irr and best_irr_score >= 3 and best_irr_score >= best_type_score:
        reasons.append(f"Reads like a {best_irr} (content score {best_irr_score}).")
        conf = min(0.95, 0.5 + best_irr_score / 12)
        return Classification(None, best_irr, round(conf, 2), reasons,
                              {**type_scores, **{f"[irrelevant] {k}": v for k, v in irr_scores.items()}})

    if best_type and best_type_score >= 2:
        reasons.append(f"Content matches a {best_type.replace('_', ' ')} "
                       f"(score {best_type_score}).")
        # Confidence rises with the score and with the margin over the runner-up.
        others = sorted(type_scores.values(), reverse=True)
        margin = best_type_score - (others[1] if len(others) > 1 else 0)
        conf = min(0.95, 0.45 + best_type_score / 12 + margin / 20)
        return Classification(best_type, None, round(conf, 2), reasons, type_scores)

    reasons.append("No strong signal for any known business-document type.")
    return Classification(None, None, 0.2, reasons,
                          {**type_scores, **{f"[irrelevant] {k}": v for k, v in irr_scores.items()}})
