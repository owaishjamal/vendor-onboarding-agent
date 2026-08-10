#!/usr/bin/env bash
# After a Python edit, run the tests that cover the file that changed.
#
# Wired as PostToolUse on Write|Edit. Advisory: it never blocks. The point is
# to put a failure in front of the agent within seconds of causing it, rather
# than at the end of a long task when the cause is ten edits back.
#
# Deliberately NOT the whole suite. 330 tests take 95 seconds; running them on
# every keystroke-sized edit means they get skipped, and a check that gets
# skipped protects nothing. `/verify` runs everything before you claim done.

set -uo pipefail

path=$(cat | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))
except Exception:
    print("")
')

case "$path" in
  *.py) ;;
  *) exit 0 ;;
esac

cd "$(dirname "$0")/../.." || exit 0

# Map the edited file to the narrowest suite that covers it.
case "$path" in
  */llm/router/*|*/llm/client.py)   suite="tests/test_llm_router.py" ;;
  */scenarios.py|*/checks/*|*/dva/*|*/pipeline/*)
                                    suite="tests/test_scenarios.py" ;;
  */profiles/*|*/rules/*)           suite="tests/test_generalized.py" ;;
  tests/*)                          suite="$path" ;;
  *)                                exit 0 ;;
esac

[ -f "$suite" ] || exit 0

out=$(timeout 150 python3 -m pytest "$suite" -q --no-header 2>&1 | tail -3)

if printf '%s' "$out" | grep -qE 'failed|error'; then
  printf 'Tests covering that file are now failing:\n\n%s\n\nFix before continuing.\n' "$out" >&2
  exit 2   # surfaces to the agent
fi

exit 0
