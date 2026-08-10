#!/usr/bin/env bash
# Block writes that would put a credential into a tracked file.
#
# This exists because it already happened: a live Gemini key was committed in
# .env.example, a file that is tracked precisely so it can be shared. It sat in
# the history until someone read line 25.
#
# Wired as a PreToolUse hook on Write|Edit. Exit 2 blocks the call and returns
# stderr to the agent, so it sees why and can correct rather than retrying.

set -uo pipefail

payload=$(cat)

path=$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))
except Exception:
    print("")
')

# .env is gitignored and is the correct home for a real key.
case "$path" in
  *"/.env"|*"/.env.local") exit 0 ;;
esac

content=$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    ti = json.load(sys.stdin).get("tool_input", {})
except Exception:
    print(""); raise SystemExit
# Write uses content; Edit uses new_string.
print(ti.get("content") or ti.get("new_string") or "")
')

# Provider key shapes. Kept narrow on purpose — a pattern that fires on
# anything long and random trains people to ignore it.
#   AIza…       Google
#   gsk_…       Groq
#   csk-…       Cerebras
#   sk-ant-…    Anthropic
#   sk-proj-…   OpenAI
#   AQ.…        Google short-lived
if printf '%s' "$content" | grep -qE '(AIza[0-9A-Za-z_-]{30,}|gsk_[0-9A-Za-z]{40,}|csk-[0-9a-z]{40,}|sk-ant-[0-9A-Za-z_-]{90,}|sk-proj-[0-9A-Za-z_-]{40,}|AQ\.[0-9A-Za-z_-]{40,})'; then
  cat >&2 <<'EOF'
BLOCKED: this write contains something shaped like a live API key.

A real Gemini key was committed to .env.example in this repo once. Keys belong
in .env (gitignored) and nowhere else — not in examples, not in tests, not in
a comment showing "what it looks like".

  - .env.example holds EMPTY values.
  - Tests use obvious placeholders: "test-groq", "fake-key".
  - Adapters read keys from the environment at call time, so nothing needs a
    key written down to work.

If this is a false positive, use a placeholder that does not match a real key
prefix. Do not disable the hook.
EOF
  exit 2
fi

exit 0
