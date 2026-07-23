"""Evaluation harness — the numbers a buyer actually asks for.

"It works on the seven cases I designed" is not evidence. These are:

  * Auto-approve precision — of the vendors we cleared automatically, how many
    SHOULD have been cleared. A single false approval here is a vendor paid
    without proper checks, so the bar is 100%.

  * Fraud recall — of the cases carrying a genuine fraud/compliance signal
    (denied party, shared bank account, payment-redirection name mismatch),
    how many we caught. A miss here is the expensive kind.

  * False-positive flags — clean vendors sent to a human for no good reason.
    Every one of these erodes reviewer trust and slows good suppliers down.

  * Status accuracy — did each case land on its intended status.

Each fixture is labelled below with its ground truth. Run:  python scripts/evaluate.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VO_DB_PATH", str(pathlib.Path(tempfile.gettempdir()) / "vo_eval.db"))
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.models import VendorSubmission  # noqa: E402
from backend.app.pipeline.runner import run_pipeline  # noqa: E402
from backend.app.storage import db  # noqa: E402

SUBS = ROOT / "data" / "submissions"


# Ground truth per fixture:
#   status         — the status it must reach
#   fraud          — carries a genuine fraud/compliance signal that MUST be caught
#   must_find      — finding codes that must be present
#   must_not_find  — finding codes whose presence would be a false positive
LABELS = {
    "VS-01_northwind_clean.json": {
        "status": "APPROVED", "fraud": False, "must_find": [],
        "must_not_find": ["BANK_NAME_MISMATCH", "DENIED_PARTY_MATCH",
                          "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR", "TAX_ID_FORMAT_INVALID"],
    },
    "VS-02_brightline_incomplete.json": {
        "status": "PENDING_INFO", "fraud": False,
        "must_find": ["MISSING_REQUIRED_FIELD", "MISSING_REQUIRED_DOCUMENT"],
        "must_not_find": ["DENIED_PARTY_MATCH", "BANK_NAME_MISMATCH"],
    },
    "VS-03_kessler_bank_mismatch.json": {
        "status": "PENDING_REVIEW", "fraud": True,
        "must_find": ["BANK_NAME_MISMATCH"], "must_not_find": [],
    },
    "VS-04_sundara_country_mismatch.json": {
        "status": "PENDING_REVIEW", "fraud": True,
        "must_find": ["TAX_ID_COUNTRY_MISMATCH"], "must_not_find": [],
    },
    "VS-05_continental_shared_account.json": {
        "status": "PENDING_REVIEW", "fraud": True,
        "must_find": ["BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR"], "must_not_find": [],
    },
    "VS-06_volkov_denied_party.json": {
        "status": "REJECTED", "fraud": True,
        "must_find": ["DENIED_PARTY_MATCH"], "must_not_find": [],
    },
    "VS-07_pinnacle_iban_typo.json": {
        "status": "PENDING_INFO", "fraud": False,
        "must_find": ["IBAN_CHECKSUM_FAILED"],
        "must_not_find": ["BANK_NAME_MISMATCH", "DENIED_PARTY_MATCH"],
    },
    "VS-08_meridian_namesake.json": {
        "status": "APPROVED", "fraud": False, "must_find": [],
        # The whole point: an exact name match to a sanctioned party must NOT
        # reject an innocent namesake once DOB clears it.
        "must_not_find": ["DENIED_PARTY_MATCH", "DENIED_PARTY_NEAR_MATCH"],
    },
    "VS-09_brightline_resubmitted.json": {
        "status": "APPROVED", "fraud": False, "must_find": [],
        "must_not_find": ["MISSING_REQUIRED_FIELD", "MISSING_REQUIRED_DOCUMENT"],
    },
    "VS-10_harbourstone_related_account.json": {
        "status": "PENDING_REVIEW", "fraud": True,
        # Subtle redirection: '<Company> Holdings' on the account. Must catch it.
        "must_find": ["BANK_NAME_MISMATCH"], "must_not_find": [],
    },
    "VS-11_fabricated_vendor.json": {
        "status": "PENDING_REVIEW", "fraud": True,
        # Internally flawless, but not in the registry. Only external verification catches it.
        "must_find": ["REGISTRY_NOT_FOUND"], "must_not_find": ["DENIED_PARTY_MATCH"],
    },
}


def run_case(filename: str) -> dict:
    sub = VendorSubmission(**json.loads((SUBS / filename).read_text()))
    events = list(run_pipeline(sub))
    return [e for e in events if e["type"] == "done"][0]["case"]


def main() -> int:
    db.reset_db()

    rows = []
    status_ok = 0
    fp_total = 0                 # false-positive flags on clean vendors
    fraud_total = fraud_caught = 0
    auto_tp = auto_fp = 0        # auto-approve precision components
    missed = []

    for filename, truth in LABELS.items():
        case = run_case(filename)
        codes = {f["code"] for f in case["findings"] if f["severity"] >= 2}
        status = case["status"]

        s_ok = status == truth["status"]
        status_ok += s_ok

        # required findings present?
        missing_required = [c for c in truth["must_find"] if c not in codes]
        # false positives present?
        false_pos = [c for c in truth["must_not_find"] if c in codes]
        fp_total += len(false_pos)

        # fraud recall
        if truth["fraud"]:
            fraud_total += 1
            caught = status in ("PENDING_REVIEW", "REJECTED") and not missing_required
            fraud_caught += caught
            if not caught:
                missed.append(filename)

        # auto-approve precision: was it auto-approved, and rightly so?
        if status == "APPROVED":
            if truth["status"] == "APPROVED":
                auto_tp += 1
            else:
                auto_fp += 1        # approved something that shouldn't have been

        ok = s_ok and not missing_required and not false_pos
        rows.append((filename.split("_")[0], status, "PASS" if ok else "FAIL",
                     missing_required, false_pos))

    # --- report
    print("=" * 74)
    print("  VENDOR ONBOARDING — EVALUATION")
    print("=" * 74)
    print(f"  {'case':<8}{'status':<18}{'result':<8}notes")
    print("  " + "-" * 70)
    for cid, status, result, miss, fp in rows:
        note = ""
        if miss:
            note += f"missing {miss} "
        if fp:
            note += f"false-positive {fp}"
        print(f"  {cid:<8}{status:<18}{result:<8}{note}")

    n = len(LABELS)
    auto_precision = (auto_tp / (auto_tp + auto_fp) * 100) if (auto_tp + auto_fp) else 100.0
    fraud_recall = (fraud_caught / fraud_total * 100) if fraud_total else 100.0

    print("\n" + "=" * 74)
    print("  METRICS")
    print("=" * 74)
    print(f"  Status accuracy .............. {status_ok}/{n}  ({status_ok / n * 100:.0f}%)")
    print(f"  Auto-approve precision ....... {auto_precision:.0f}%   "
          f"({auto_tp} correct, {auto_fp} wrong of {auto_tp + auto_fp} auto-approvals)")
    print(f"  Fraud / compliance recall .... {fraud_recall:.0f}%   "
          f"({fraud_caught}/{fraud_total} signal cases caught)")
    print(f"  False-positive flags ......... {fp_total}   (clean vendors wrongly flagged)")
    if missed:
        print(f"\n  !! MISSED FRAUD SIGNALS: {missed}")

    all_pass = all(r[2] == "PASS" for r in rows) and auto_fp == 0 and not missed
    print("\n  " + ("ALL CHECKS PASS — no false approvals, no missed signals."
                    if all_pass else "SEE FAILURES ABOVE."))
    print("=" * 74)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
