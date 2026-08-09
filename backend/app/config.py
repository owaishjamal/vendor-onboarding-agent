"""Runtime configuration.

Note how little is here compared with an invoice pipeline. Onboarding policy
is almost entirely per-country, so it lives in the YAML rule packs where a
compliance owner can read it. What remains here is infrastructure plus the two
or three thresholds that genuinely are global.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Read backend/.env into the environment, if it exists.

    Without this, putting GEMINI_API_KEY in backend/.env did nothing: every
    setting below reads os.getenv, and no part of the app ever opened the
    file. The app silently stayed in offline mode and the copilot answered
    "no language model is configured" — technically true, and completely
    baffling when you have just filled in the key.

    Real environment variables always win, so a container or a CI runner that
    sets values directly is never overridden by a stray file on disk.
    """
    # Both locations, nearest first. "backend/.env" is what the docs say, but
    # the project root is where people actually put it — and a key that is
    # silently ignored because it sits one directory up is a miserable way to
    # lose twenty minutes. Read both; the first file to define a variable wins.
    for env_path in (ROOT / "backend" / ".env", ROOT / ".env"):
        if not env_path.exists():
            continue
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")
        except OSError:
            continue


_load_dotenv()

DATA_DIR = ROOT / "data"
SUBMISSION_DIR = DATA_DIR / "submissions"
SEED_DIR = ROOT / "backend" / "seed"
CACHE_DIR = DATA_DIR / ".llm_cache"

for _d in (DATA_DIR, SUBMISSION_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Overridable so the database can sit on local disk when the project lives on
# a synced or network folder, where SQLite locking misbehaves.
DB_PATH = Path(os.getenv("VO_DB_PATH", str(DATA_DIR / "cases.db")))


# --- LLM -------------------------------------------------------------------

LLM_MODEL = os.getenv("LLM_MODEL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


def _resolve_provider() -> str:
    """offline | anthropic | openai | gemini.

    Setting a key should be enough. Requiring LLM_PROVIDER *as well* meant the
    obvious action — paste the key in .env — left the system in offline mode
    with no indication why, so a key is now sufficient on its own.

    An explicit LLM_PROVIDER still wins, including `offline`, which is how you
    force fixture mode for a demo or CI run even with a key present.
    """
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if GEMINI_API_KEY:
        return "gemini"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if OPENAI_API_KEY:
        return "openai"
    return "offline"


LLM_PROVIDER = _resolve_provider()
LLM_CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "1") == "1"

# Artificial pause between checks so the live view is readable. Presentation
# only - set to 0 for real throughput.
CHECK_DELAY_MS = int(os.getenv("CHECK_DELAY_MS", "400"))


# --- branding --------------------------------------------------------------
APP_TITLE = os.getenv("APP_TITLE", "Zamp")
APP_SUBTITLE = os.getenv("APP_SUBTITLE", "Vendor Onboarding & Verification")


# --- decisioning -----------------------------------------------------------
#
# The AI confidence score decides who handles a case:
#
#   >= AUTO_DECIDE_CONFIDENCE and clean        -> Auto Approve
#   >= AUTO_DECIDE_CONFIDENCE and clear fraud  -> Auto Reject
#   below that, or anything ambiguous          -> Manual Review
#
# Set deliberately high: we would rather send a borderline case to a human
# than auto-decide one we are not sure about.
AUTO_DECIDE_CONFIDENCE = float(os.getenv("AUTO_DECIDE_CONFIDENCE", "0.85"))


# --- document processing ---------------------------------------------------

# How attachments are turned into structured fields:
#   "offline" - the built-in label parser (no key, deterministic, tuned to
#               clean layouts). Default.
#   "vision"  - a vision-language model reads the page image directly and
#               returns fields. Generalises to arbitrary real documents.
#               Uses the same LLM_PROVIDER credentials.
DOC_EXTRACTOR = os.getenv("DOC_EXTRACTOR", "offline").lower()

# Cache document reads by file content hash, so re-running a case (or a demo)
# never re-OCRs the same file. Big win for both cost and latency.
DOC_READ_CACHE = os.getenv("DOC_READ_CACHE", "1") == "1"

# Upload safety limits (enterprise hygiene — uploads are an attack surface).
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "15"))
ALLOWED_UPLOAD_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


# --- global policy ---------------------------------------------------------

# Vendors from countries with no rule pack cannot be validated at all, so they
# go to a human rather than being approved on incomplete evidence.
UNSUPPORTED_COUNTRY_IS_BLOCKING = True

# A free email domain on a corporate vendor is weak evidence on its own - a
# small supplier legitimately using gmail is common. It is recorded as
# advisory, and only becomes interesting when it stacks with other findings.
FREE_EMAIL_IS_ADVISORY_ONLY = True
