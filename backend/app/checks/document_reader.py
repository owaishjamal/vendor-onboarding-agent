"""Actually read the document — text layer first, OCR for scans.

This is the piece that separates a real solution from a validation form. The
`documents` check cross-references the name and number ON an attachment against
the form; this module is what turns a file on disk into those fields.

The routing mirrors the invoice build's proven approach:

    pdf text layer  →  if too thin, rasterise and OCR  →  parse labelled fields

Every read carries a CONFIDENCE. A crisp PDF text layer is trusted; an OCR of
a skewed scan is discounted. Downstream, a low-confidence read of a document we
actually need routes to the vendor ("resend a clearer copy") rather than being
silently trusted — the same principle the whole system runs on: when unsure,
ask, never guess.

Type is DETECTED from the document itself, not taken on faith from the vendor's
label. That is what lets us catch "attached the wrong document" — the vendor
says this file is a bank letter; the reader looks at it and sees a delivery note.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from backend.app import config

log = logging.getLogger("vo.document_reader")

# Below this many characters, a PDF page is not a real document — treat as a scan.
MIN_TEXT_CHARS = 60
OCR_DPI = 300

# Confidence a clean text layer earns, and the ceiling an OCR read can reach.
TEXT_CONFIDENCE = 0.97
OCR_CONFIDENCE = 0.82

# A document read below this cannot be relied on for a decision.
MIN_READ_CONFIDENCE = 0.70


# Title keyword -> canonical document kind. The renderer writes one of these
# titles at the top of every document; real documents carry them too.
TITLE_TO_KIND = [
    ("certificate of incorporation", "certificate_of_incorporation"),
    ("companies house", "companies_house_extract"),
    ("handelsregister", "handelsregisterauszug"),
    ("acra", "acra_bizfile"),
    ("business profile", "acra_bizfile"),
    ("form w-9", "w9"),
    ("w-9", "w9"),
    ("vat registration", "vat_certificate"),
    ("ust-idnr", "vat_certificate"),
    ("gst registration", "gst_certificate"),
    ("bank confirmation", "bank_letter"),
    ("bank letter", "bank_letter"),
    ("bank reference", "bank_letter"),
    ("bank statement", "bank_statement"),
    ("cancelled cheque", "cancelled_cheque"),
    ("voided cheque", "voided_cheque"),
    ("delivery note", "delivery_note"),        # the classic "wrong document"
    ("purchase order", "purchase_order"),
    ("invoice", "invoice"),
]

LABELS = {
    "name": ["account holder", "account name", "name of company", "legal name",
             "company name", "registered name", "name", "holder"],
    "number": ["registration number", "company number", "company no", "uen",
               "reg no", "vat number", "vat reg", "ein", "tax id", "gstin",
               "handelsregister no", "number"],
    "issue_date": ["issue date", "issued", "date of issue", "dated"],
    "expiry_date": ["expiry date", "expires", "valid until", "expiry"],
}


@dataclass
class ReadResult:
    source: str                       # text_layer | ocr | provided | none
    confidence: float
    detected_type: Optional[str]
    fields: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Low-level extraction
# ---------------------------------------------------------------------------

def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out).strip()


def _preprocess(pil_image):
    """Clean an image before OCR: greyscale, deskew, adaptive threshold.

    Real vendor documents are phone photos — skewed, unevenly lit, low
    contrast — which is exactly what wrecks OCR accuracy. This pass (OpenCV)
    straightens and binarises the page first. It degrades gracefully: if
    OpenCV isn't installed, or anything fails, we hand back the original image
    and OCR still runs, just without the boost.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return pil_image.convert("L")

    try:
        img = np.array(pil_image.convert("L"))

        # Deskew using the dominant text angle.
        coords = np.column_stack(np.where(img < 128))
        if len(coords) > 50:
            angle = cv2.minAreaRect(coords)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
            if abs(angle) > 0.3:
                h, w = img.shape
                m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
                img = cv2.warpAffine(img, m, (w, h),
                                     flags=cv2.INTER_CUBIC,
                                     borderMode=cv2.BORDER_REPLICATE)

        # Denoise + adaptive threshold to even out lighting.
        img = cv2.fastNlMeansDenoising(img, h=10)
        img = cv2.adaptiveThreshold(
            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)

        from PIL import Image
        return Image.fromarray(img)
    except Exception:
        return pil_image.convert("L")


def _ocr_pdf(pdf_bytes: bytes, preprocess: bool = False) -> str:
    import fitz
    import pytesseract
    from PIL import Image
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    chunks = []
    for page in doc:
        pix = page.get_pixmap(dpi=OCR_DPI)
        im = Image.open(io.BytesIO(pix.tobytes("png")))
        im = _preprocess(im) if preprocess else im.convert("L")
        chunks.append(pytesseract.image_to_string(im))
    return "\n".join(chunks).strip()


def _ocr_image(img_bytes: bytes, preprocess: bool = False) -> str:
    import pytesseract
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes))
    im = _preprocess(im) if preprocess else im.convert("L")
    return pytesseract.image_to_string(im).strip()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _detect_type(text: str) -> Optional[str]:
    low = text.lower()
    for keyword, kind in TITLE_TO_KIND:
        if keyword in low:
            return kind
    return None


def _find_labelled(lines: list[str], labels: list[str]) -> Optional[str]:
    for ln in lines:
        low = ln.lower()
        for lab in labels:
            m = re.search(rf"{re.escape(lab)}\s*[:\-]\s*(.+)", low)
            if m:
                # Return the value from the original-case line, not lowered.
                idx = ln.lower().find(lab)
                rest = ln[idx + len(lab):]
                rest = re.sub(r"^[\s:\-]+", "", rest).strip()
                if rest:
                    return rest
    return None


def _normalise_date(raw: str) -> Optional[str]:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def _parse_fields(text: str) -> dict[str, Any]:
    lines = [l for l in text.splitlines() if l.strip()]
    fields: dict[str, Any] = {}

    name = _find_labelled(lines, LABELS["name"])
    if name:
        # Strip trailing noise a label grab might catch.
        fields["name"] = re.split(r"\s{2,}", name)[0].strip()

    number = _find_labelled(lines, LABELS["number"])
    if number:
        # Keep the full identifier, not just the first token — registration
        # numbers like "HRB 84721" or "U74999 MH2015" contain spaces. Match a
        # letter-prefixed or numeric identifier sequence.
        m = re.match(r"([A-Z]{0,6}\s?-?\d[\w\-\/]*(?:\s\d[\w\-\/]*)*)", number.strip(), re.I)
        fields["number"] = (m.group(1).strip() if m else number.split()[0]).strip()

    for key in ("issue_date", "expiry_date"):
        raw = _find_labelled(lines, LABELS[key])
        if raw:
            d = _normalise_date(raw)
            if d:
                fields[key] = d

    return fields


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

EXTRACTOR_VERSION = "read.v3"   # v3: full multi-token identifiers (HRB 84721)


def _cache_path(digest: str) -> Path:
    d = config.CACHE_DIR / "docreads"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json"


def _cache_get(digest: str) -> Optional[ReadResult]:
    if not config.DOC_READ_CACHE:
        return None
    p = _cache_path(digest)
    if p.exists():
        try:
            import json
            return ReadResult(**json.loads(p.read_text()))
        except Exception:
            return None
    return None


def _cache_put(digest: str, r: ReadResult) -> None:
    if not config.DOC_READ_CACHE:
        return
    try:
        import json
        _cache_path(digest).write_text(json.dumps(r.__dict__, default=str))
    except Exception:
        pass


def read_document(doc) -> ReadResult:
    """Read a SubmittedDocument. Prefers a real file; falls back to `extracted`."""
    # --- path 1: a real file
    if getattr(doc, "path", None):
        try:
            from backend.app.storage.documents import get_storage
            data = get_storage().read(doc.path)
            import hashlib
            digest = hashlib.sha256(
                data + f"|{config.DOC_EXTRACTOR}|{EXTRACTOR_VERSION}".encode()
            ).hexdigest()[:32]
            hit = _cache_get(digest)
            if hit is not None:
                return hit
            suffix = Path(doc.path).suffix.lower()
            result = _read_data(data, suffix)
            _cache_put(digest, result)
            return result
        except FileNotFoundError:
            log.warning("document path set but file missing: %s", doc.path)

    # --- path 2: pre-parsed block supplied with the submission
    if doc.extracted:
        ex = dict(doc.extracted)
        detected = ex.get("kind")
        fields = {
            "name": ex.get("legal_name") or ex.get("account_name") or ex.get("name"),
            "number": ex.get("registration_number") or ex.get("tax_id")
                      or ex.get("company_number") or ex.get("vat_number"),
            "issue_date": ex.get("issue_date"),
            "expiry_date": ex.get("expiry_date"),
        }
        fields = {k: v for k, v in fields.items() if v}
        return ReadResult(source="provided", confidence=1.0,
                          detected_type=detected, fields=fields,
                          note="Field block supplied with the submission (no file to read).")

    # --- nothing to read
    if not doc.readable:
        return ReadResult(source="none", confidence=0.0, detected_type=None,
                          note="Marked unreadable and no content supplied.")
    return ReadResult(source="none", confidence=0.0, detected_type=None,
                      note="No file and no field block supplied.")


def _read_data(data: bytes, suffix: str) -> ReadResult:

    # Vision path: hand the page image straight to a VLM. This is the
    # generalising extractor — it reads arbitrary real layouts a label parser
    # can't. Enabled with DOC_EXTRACTOR=vision (uses the LLM credentials); it
    # falls back to the OCR+parser path on any error, so it never fails a run.
    if config.DOC_EXTRACTOR == "vision":
        try:
            vr = _extract_vision(data, suffix)
            if vr is not None:
                return vr
        except Exception as exc:
            log.warning("vision extractor failed (%s); falling back to OCR", exc)

    try:
        if suffix == ".pdf":
            text = _pdf_text(data)
            if len(text) >= MIN_TEXT_CHARS:
                return _build(text, source="text_layer", confidence=TEXT_CONFIDENCE)
            return _ocr_best(lambda pp: _ocr_pdf(data, pp))
        elif suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            return _ocr_best(lambda pp: _ocr_image(data, pp))
    except Exception as exc:
        log.warning("failed to read document: %s", exc)
        return ReadResult(source="none", confidence=0.0, detected_type=None,
                          note=f"Could not be read ({type(exc).__name__}).")

    return ReadResult(source="none", confidence=0.0, detected_type=None,
                      note=f"Unsupported document format: {suffix}")


def _ocr_best(ocr_fn) -> ReadResult:
    """OCR plain first; if the read is thin, retry with image preprocessing.

    A clean scan reads best without heavy preprocessing (thresholding can hurt
    crisp text); a noisy phone photo reads best WITH it. Rather than guess,
    try plain, and only pay for the preprocessing pass when the plain read
    recovered too little — then keep whichever found more fields.
    """
    plain = _build(_ocr_fn_safe(ocr_fn, False), source="ocr",
                   confidence=OCR_CONFIDENCE, note="Read by OCR.")
    if len(plain.fields) >= 2:
        return plain
    pre = _build(_ocr_fn_safe(ocr_fn, True), source="ocr",
                 confidence=OCR_CONFIDENCE,
                 note="Read by OCR with image preprocessing (deskew/threshold).")
    return pre if len(pre.fields) > len(plain.fields) else plain


def _ocr_fn_safe(ocr_fn, preprocess: bool) -> str:
    try:
        return ocr_fn(preprocess)
    except Exception as exc:
        log.warning("OCR pass failed (preprocess=%s): %s", preprocess, exc)
        return ""


def _build(text: str, source: str, confidence: float, note: str = "") -> ReadResult:
    if not text or len(text) < MIN_TEXT_CHARS:
        return ReadResult(source="none", confidence=0.0, detected_type=None,
                          raw_text=text, note="Document opened but no readable text found.")
    fields = _parse_fields(text)
    detected = _detect_type(text)
    # If we recovered very few fields from an OCR read, discount further —
    # partial reads are exactly where silent errors hide.
    if source == "ocr" and len(fields) < 2:
        confidence = min(confidence, 0.6)
    return ReadResult(source=source, confidence=confidence, detected_type=detected,
                      fields=fields, raw_text=text, note=note)


# ---------------------------------------------------------------------------
# Vision extractor (the generalising path)
# ---------------------------------------------------------------------------

_VISION_SYSTEM = (
    "You read a scanned business document (bank letter, certificate of "
    "incorporation, tax certificate, etc.) and return a single JSON object with "
    "exactly these keys: kind (one of: certificate_of_incorporation, "
    "companies_house_extract, handelsregisterauszug, acra_bizfile, w9, "
    "vat_certificate, gst_certificate, bank_letter, bank_statement, "
    "cancelled_cheque, voided_cheque, delivery_note, invoice, unknown), name "
    "(the entity or account-holder name on the document), number (the "
    "registration / tax / company number if present), issue_date and "
    "expiry_date (YYYY-MM-DD or null). Return null for anything not present. "
    "No prose, only the JSON object."
)


def _page_png(data: bytes, suffix: str) -> bytes:
    if suffix == ".pdf":
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        return doc[0].get_pixmap(dpi=200).tobytes("png")
    return data  # already an image


def _extract_vision(data: bytes, suffix: str) -> Optional[ReadResult]:
    """Send the page image to a vision model and parse its structured reply.

    Implemented for Anthropic and OpenAI vision. Returns None (so the caller
    falls back to OCR) if no vision-capable provider is configured.
    """
    import base64
    import json

    from backend.app import config as cfg

    png = _page_png(data, suffix)
    b64 = base64.standard_b64encode(png).decode()

    raw: Optional[str] = None
    if cfg.LLM_PROVIDER == "anthropic" and cfg.ANTHROPIC_API_KEY:
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
        r = client.messages.create(
            model=cfg.LLM_MODEL or "claude-haiku-4-5-20251001",
            max_tokens=500, system=_VISION_SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/png", "data": b64}},
                {"type": "text", "text": "Extract the fields as JSON."},
            ]}],
        )
        raw = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    elif cfg.LLM_PROVIDER == "openai" and cfg.OPENAI_API_KEY:
        from openai import OpenAI
        client = OpenAI(api_key=cfg.OPENAI_API_KEY)
        r = client.chat.completions.create(
            model=cfg.LLM_MODEL or "gpt-4o-mini", max_tokens=500,
            messages=[{"role": "system", "content": _VISION_SYSTEM},
                      {"role": "user", "content": [
                          {"type": "text", "text": "Extract the fields as JSON."},
                          {"type": "image_url", "image_url":
                           {"url": f"data:image/png;base64,{b64}"}}]}],
        )
        raw = r.choices[0].message.content
    elif cfg.LLM_PROVIDER == "gemini" and cfg.GEMINI_API_KEY:
        import urllib.request
        model = cfg.LLM_MODEL or "gemini-flash-latest"
        body = {
            "system_instruction": {"parts": [{"text": _VISION_SYSTEM}]},
            "contents": [{"parts": [
                {"text": "Extract the fields as JSON."},
                {"inline_data": {"mime_type": "image/png", "data": b64}},
            ]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 500},
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-goog-api-key": cfg.GEMINI_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            gdata = json.loads(resp.read().decode())
        raw = gdata["candidates"][0]["content"]["parts"][0]["text"]
    else:
        return None  # no vision provider configured -> fall back to OCR

    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip()).strip()
    obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    fields = {k: obj.get(k) for k in ("name", "number", "issue_date", "expiry_date")
              if obj.get(k)}
    kind = obj.get("kind")
    return ReadResult(source="vision", confidence=0.9,
                      detected_type=None if kind == "unknown" else kind,
                      fields=fields, raw_text=raw,
                      note="Read by a vision-language model.")
