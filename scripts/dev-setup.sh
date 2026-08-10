#!/usr/bin/env bash
# Get a working environment from a fresh clone.
#
# Written for agents as much as people: an agent that has to guess at setup
# will guess wrong, install the wrong things, and report success from a broken
# environment. This is the one command, and it verifies rather than assumes.
#
#   bash scripts/dev-setup.sh

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

ok=0; warn=0
say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
pass() { printf '  ok    %s\n' "$1"; ok=$((ok+1)); }
miss() { printf '  WARN  %s\n' "$1"; warn=$((warn+1)); }

say "Python"
python3 -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null \
  && pass "$(python3 --version)" \
  || { printf '  FAIL  need Python 3.10+\n'; exit 1; }

say "Backend dependencies"
if python3 -m pip install -q -r backend/requirements.txt 2>/dev/null; then
  pass "installed"
elif python3 -m pip install -q --break-system-packages -r backend/requirements.txt 2>/dev/null; then
  pass "installed (--break-system-packages)"
else
  printf '  FAIL  pip install failed\n'; exit 1
fi

say "System binaries"
command -v tesseract >/dev/null \
  && pass "tesseract" \
  || miss "tesseract not found — OCR falls back to the PDF text layer.
        Debian/Ubuntu: sudo apt-get install -y tesseract-ocr
        macOS:         brew install tesseract"

say "Frontend"
if command -v npm >/dev/null; then
  (cd frontend && npm install --silent) && pass "npm packages"
else
  miss "npm not found — the backend runs fine; you cannot build the UI"
fi

say "Configuration"
if [ -f .env ]; then
  pass ".env present"
else
  cp .env.example .env
  pass ".env created from .env.example (empty keys — offline mode)"
fi
grep -qE '^\.env$' .gitignore && pass ".env is gitignored" \
                              || miss ".env is NOT gitignored — fix before committing"

say "LLM providers"
python3 scripts/check_env.py 2>/dev/null | sed 's/^/  /' \
  || miss "check_env.py did not run"

say "Verifying the install actually works"
if timeout 200 python3 -m pytest tests/ -q --no-header 2>&1 | tail -1 | tee /tmp/vo_setup.txt | grep -q "passed"; then
  pass "$(cat /tmp/vo_setup.txt)"
else
  printf '  FAIL  %s\n' "$(cat /tmp/vo_setup.txt 2>/dev/null)"
  printf '\nSetup did not produce a working tree. Do not start work until this passes.\n'
  exit 1
fi

printf '\n%d ok, %d warning(s)\n' "$ok" "$warn"
cat <<'EOF'

Run it:
  uvicorn backend.app.api.app:app --reload --port 8000
  cd frontend && npm run dev

The app works with no API key — LLM_PROVIDER=offline composes the two generated
documents from templates. Every check, finding, status and screen is identical.

Read CLAUDE.md before changing a decision path.
EOF
