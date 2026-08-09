"""Why is the LLM still offline? — a diagnostic, not a guess.

    python scripts/check_env.py

Prints exactly what the application sees: which .env file it looked for,
whether it found a key, what provider it resolved, and — if a key is present —
whether Google actually accepts it. Every line names the file and variable
involved so a wrong answer tells you which thing to change.

Safe to run any time. It never prints your key.
"""

from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK, BAD, WARN = "  [ok] ", "  [!!] ", "  [ ?] "


def main() -> int:
    print("=" * 66)
    print("  LLM CONFIGURATION DIAGNOSTIC")
    print("=" * 66)

    # ---- 1. the file -----------------------------------------------------
    candidates = [ROOT / "backend" / ".env", ROOT / ".env"]
    env_path = next((p for p in candidates if p.exists()), candidates[0])
    print("\n1. Env files the app reads (nearest first):")
    for c in candidates:
        print(f"     {'FOUND  ' if c.exists() else 'absent '} {c}")
    if env_path.exists():
        print(OK + "file exists")
        keys = {}
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            keys[k.strip()] = v.strip().strip('"').strip("'")
        print(f"     variables set in it: {', '.join(sorted(keys)) or '(none)'}")
        if "LLM_PROVIDER" in keys:
            val = keys["LLM_PROVIDER"]
            if val.lower() == "offline":
                print(BAD + f"LLM_PROVIDER={val} is pinning the app to offline.")
                print("     -> delete that line, or change it to: LLM_PROVIDER=gemini")
            else:
                print(OK + f"LLM_PROVIDER={val}")
        if "GEMINI_API_KEY" not in keys:
            print(WARN + "no GEMINI_API_KEY line in this file")
        elif not keys["GEMINI_API_KEY"]:
            print(BAD + "GEMINI_API_KEY is present but empty")
    else:
        print(BAD + "No .env in either location.")
        print("     Copy the template:  copy backend\\.env.example backend\\.env")

    # ---- 2. the shell ----------------------------------------------------
    print("\n2. Variables already in your shell (these override the file)")
    for name in ("LLM_PROVIDER", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        raw = os.environ.get(name)
        if raw is None:
            print(f"     {name:16s} not set")
        elif name.endswith("KEY"):
            print(f"     {name:16s} set ({len(raw)} chars)")
        else:
            print(f"     {name:16s} {raw}")
            if name == "LLM_PROVIDER" and raw.strip().lower() == "offline":
                print(BAD + "your shell is forcing offline mode.")
                print("     -> in PowerShell:  Remove-Item Env:LLM_PROVIDER")

    # ---- 3. what the app resolved ---------------------------------------
    from backend.app import config
    print("\n3. What the application resolved")
    print(f"     provider : {config.LLM_PROVIDER}")
    print(f"     key seen : {'yes (' + str(len(config.GEMINI_API_KEY)) + ' chars)' if config.GEMINI_API_KEY else 'NO'}")
    print(f"     model    : {config.LLM_MODEL or 'gemini-flash-latest (default)'}")

    if config.LLM_PROVIDER == "offline":
        print("\n" + BAD + "Still offline. Work through the flags above, then RESTART")
        print("     the backend — configuration is read once, at import.")
        return 1

    # ---- 4. does the key actually work? ---------------------------------
    print("\n4. Live call to Google")
    try:
        from backend.app.llm.client import get_llm
        llm = get_llm()
        print(f"     client provider: {llm.provider}")
        reply = llm._complete("Reply with the single word OK.", "ping", 16)
        print(OK + f"Google answered: {reply.strip()[:60]!r}")
        print("\n  Everything is wired. The copilot will now answer open questions.")
        return 0
    except Exception as exc:
        msg = str(exc)
        print(BAD + f"{type(exc).__name__}: {msg[:200]}")
        low = msg.lower()
        # Network failures must not be reported as key problems. A blocked
        # proxy and a rejected key both surface as "403" somewhere in the
        # message, and sending someone to regenerate a perfectly good key is
        # the worst possible advice.
        if any(s in low for s in ("tunnel connection failed", "proxy",
                                  "name or service not known", "temporary failure",
                                  "connection refused", "timed out", "ssl")):
            print("     -> this is a NETWORK problem, not a key problem. Something")
            print("        between you and generativelanguage.googleapis.com is")
            print("        blocking the request — a corporate proxy, VPN or firewall.")
            print("        Your key and configuration look correct.")
        elif "api key not valid" in low or "api_key_invalid" in low or "400" in low:
            print("     -> the key was rejected. Regenerate it at")
            print("        https://aistudio.google.com/apikey")
        elif "403" in low or "permission" in low:
            print("     -> enable the Generative Language API for that key's project.")
        elif "429" in low or "quota" in low:
            print("     -> rate limited. Wait a minute and retry.")
        elif "404" in low:
            print("     -> that model name is not available to this key.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
