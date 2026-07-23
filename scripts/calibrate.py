"""Threshold calibration — turn the magic numbers into justified choices.

Every threshold in the system (name-match bands, screening bands) is a lever
between two failure modes: set it loose and fraud slips through (low recall);
set it tight and legitimate vendors get flagged (false positives, reviewer
fatigue). "Why 85?" deserves a better answer than "it felt right".

This sweeps a threshold across a range, re-runs the volume evaluation at each
value, and prints precision / recall / false-positive rate so the chosen number
sits on a curve you can point at. It uses the in-memory rule override, so
nothing on disk changes.

Run:  python scripts/calibrate.py [name_strong|screening]
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VO_DB_PATH", str(pathlib.Path(tempfile.gettempdir()) / "vo_cal.db"))
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.checks import registry as registry_check  # noqa: E402
from backend.app.models import VendorSubmission  # noqa: E402
from backend.app.pipeline.runner import assess  # noqa: E402
from backend.app.rules import load_common_rules, set_common_override  # noqa: E402
from eval_volume import EXPECTED, FRAUD_CATEGORIES, generate  # noqa: E402


def score_at(cases, registry, override_patch) -> dict:
    set_common_override(override_patch)
    registry_check.set_registry_override(registry)
    try:
        auto_tp = auto_fp = fraud_total = fraud_caught = fp_flags = clean_total = 0
        false_reject = 0        # a non-reject case wrongly rejected outright
        for sub, cat in cases:
            status, _, _ = assess(VendorSubmission(**sub))
            got = status.value
            want = EXPECTED[cat]
            if want == "APPROVED":
                clean_total += 1
                if got != "APPROVED":
                    fp_flags += 1
            if want != "REJECTED" and got == "REJECTED":
                false_reject += 1
            if got == "APPROVED":
                auto_tp += want == "APPROVED"
                auto_fp += want != "APPROVED"
            if cat in FRAUD_CATEGORIES:
                fraud_total += 1
                fraud_caught += got in ("PENDING_REVIEW", "REJECTED")
    finally:
        set_common_override(None)
        registry_check.set_registry_override(None)

    return {
        "precision": auto_tp / (auto_tp + auto_fp) * 100 if (auto_tp + auto_fp) else 100.0,
        "recall": fraud_caught / fraud_total * 100 if fraud_total else 100.0,
        "fp_rate": fp_flags / clean_total * 100 if clean_total else 0.0,
        "false_reject": false_reject,
        "auto_fp": auto_fp,
    }


SWEEPS = {
    "name_strong": {
        "label": "name-match 'strong' threshold (bank account holder vs legal name)",
        "values": [70, 75, 80, 85, 90, 95],
        "patch": lambda v: {"name_matching": {"strong": v}},
        "chosen": None,   # filled from the live config
        "config": lambda: load_common_rules().get("name_matching", {}).get("strong"),
    },
    "screening": {
        "label": "denied-party 'confirm' threshold",
        "values": [80, 84, 88, 92, 96],
        "patch": lambda v: {"denied_party_screening": {"match_threshold": v}},
        "config": lambda: load_common_rules().get("denied_party_screening", {}).get("match_threshold"),
    },
}


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "screening"
    if which not in SWEEPS:
        print(f"unknown sweep '{which}'. options: {', '.join(SWEEPS)}")
        return 1
    sweep = SWEEPS[which]

    cases, registry = generate(250)
    chosen = sweep["config"]()

    print("=" * 74)
    print(f"  CALIBRATION — {sweep['label']}")
    print(f"  (currently configured at {chosen})")
    print("=" * 74)
    print(f"  {'threshold':>10}   {'recall':>8}  {'false rejects':>14}   note")
    print("  " + "-" * 66)

    for v in sweep["values"]:
        m = score_at(cases, registry, sweep["patch"](v))
        note = ""
        if m["auto_fp"] > 0:
            note = f"{m['auto_fp']} fraud auto-approved"
        elif m["false_reject"] > 0:
            note = "namesakes wrongly rejected"
        marker = "  *chosen*" if v == chosen else ""
        print(f"  {v:>10}   {m['recall']:>7.1f}%  {m['false_reject']:>14}   {note}{marker}")

    print("\n  Read: lower the confirm threshold (top) and borderline namesakes get")
    print("  rejected outright — innocent people blocked for a similar name. Raise it")
    print("  (bottom) and confirmed hits still reject (DOB confirms them), while")
    print("  ambiguous ones correctly go to a human. The chosen value is the point")
    print("  that rejects nobody on name alone yet still catches every real hit.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
