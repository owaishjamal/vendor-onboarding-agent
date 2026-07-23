"""Volume evaluation — the number that actually means something.

"100% on nine cases I designed" proves the plumbing. This generates a few
hundred labelled submissions across realistic categories — including the
*plausible* fraud a similarity threshold alone would miss — and reports
precision, recall and false-positive rate at scale, per category and per check.

Everything is seeded, so the run is reproducible. Each generated case carries
its own ground-truth label (the category it was built from), and the legitimate
companies are injected into an in-memory registry so the registry check can
verify them — the fabricated category deliberately is not, so it must fail.

Run:  python scripts/eval_volume.py [N]
"""

from __future__ import annotations

import os
import pathlib
import random
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VO_DB_PATH", str(pathlib.Path(tempfile.gettempdir()) / "vo_vol.db"))
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.checks import registry as registry_check  # noqa: E402
from backend.app.models import Status, VendorSubmission  # noqa: E402
from backend.app.pipeline.runner import assess  # noqa: E402


# ---------------------------------------------------------------------------
# Valid-value generators (real algorithms, so the "clean" cases are truly clean)
# ---------------------------------------------------------------------------

def iban_check_digits(country: str, bban: str) -> str:
    s = bban + country + "00"
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in s.upper())
    rem = 0
    for i in range(0, len(digits), 7):
        rem = int(str(rem) + digits[i:i + 7]) % 97
    return f"{98 - rem:02d}"


def gb_iban(rng: random.Random) -> str:
    bank = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4))
    bban = bank + "".join(rng.choice("0123456789") for _ in range(14))
    return f"GB{iban_check_digits('GB', bban)}{bban}"


def broken_iban(rng: random.Random) -> str:
    """A valid IBAN with two digits transposed — passes regex, fails mod-97."""
    good = gb_iban(rng)
    i = next((k for k in range(len(good) - 1) if good[k].isdigit()
              and good[k + 1].isdigit() and good[k] != good[k + 1]), 6)
    return good[:i] + good[i + 1] + good[i] + good[i + 2:]


NAMES = ["Brightwater", "Kingsley", "Ashford", "Maple Ridge", "Copperfield",
         "Silverbrook", "Thornton", "Whitfield", "Redgate", "Bluestone",
         "Harborview", "Elmwood", "Fairbanks", "Grayson", "Halcyon",
         "Ironwood", "Larkspur", "Meadowvale", "Northgate", "Oakhurst"]
SECTORS = ["Components", "Logistics", "Analytics", "Interiors", "Textiles",
           "Systems", "Supplies", "Consulting", "Fabrication", "Trading Co"]
PEOPLE = ["James Porter", "Aisha Khan", "Liam O'Connor", "Sofia Rossi",
          "Chen Wei", "Marcus Bell", "Nadia Haddad", "Tom Fletcher"]


def company_name(rng: random.Random) -> str:
    return f"{rng.choice(NAMES)} {rng.choice(SECTORS)} Ltd"


CATEGORIES = [
    "clean", "missing_field", "missing_doc", "bad_iban", "bad_taxid",
    "country_mismatch", "subtle_name_fraud", "blatant_name_fraud",
    "fabricated", "denied_party", "namesake_variant",
]

EXPECTED = {
    "clean": "APPROVED",
    "missing_field": "PENDING_INFO",
    "missing_doc": "PENDING_INFO",
    "bad_iban": "PENDING_INFO",
    "bad_taxid": "PENDING_INFO",
    "country_mismatch": "PENDING_REVIEW",
    "subtle_name_fraud": "PENDING_REVIEW",
    "blatant_name_fraud": "PENDING_REVIEW",
    "fabricated": "PENDING_REVIEW",
    "denied_party": "REJECTED",
    # A director whose name is close to (but not) a sanctioned party, with no
    # DOB to resolve it. The cautious-correct answer is human review — NOT an
    # automatic rejection of a possible innocent namesake. This case is what
    # makes the screening threshold a real tradeoff.
    "namesake_variant": "PENDING_REVIEW",
}

# Categories that carry a genuine fraud / compliance signal that MUST be caught.
FRAUD_CATEGORIES = {"subtle_name_fraud", "blatant_name_fraud", "fabricated",
                    "denied_party", "country_mismatch"}


def _docs(name: str, reg: str, vat: str):
    return [
        {"doc_type": "incorporation", "filename": "coi.pdf",
         "extracted": {"kind": "certificate_of_incorporation",
                       "legal_name": name, "company_number": reg}},
        {"doc_type": "tax_form", "filename": "vat.pdf",
         "extracted": {"kind": "vat_certificate", "legal_name": name, "vat_number": vat}},
        {"doc_type": "bank_proof", "filename": "bank.pdf",
         "extracted": {"kind": "bank_letter", "account_name": name}},
    ]


def generate(n: int, seed: int = 7):
    """Yield (submission_dict, category). Also returns a registry list to inject."""
    rng = random.Random(seed)
    cases = []
    registry = []

    for i in range(n):
        cat = CATEGORIES[i % len(CATEGORIES)]
        name = company_name(rng)
        reg = "".join(rng.choice("0123456789") for _ in range(8))
        vat = "GB" + "".join(rng.choice("0123456789") for _ in range(9))
        iban = gb_iban(rng)
        acct = name

        sub = {
            "legal_name": name, "country": "GB",
            "entity_type": "Private limited company",
            "registration_number": reg, "tax_id": vat,
            "address_line1": f"{rng.randint(1, 200)} High Street",
            "address_city": "London", "address_postcode": "EC1A 1BB",
            "address_country": "GB",
            "contact_name": rng.choice(PEOPLE),
            "contact_email": f"info@{name.split()[0].lower()}.co.uk",
            "website": f"https://{name.split()[0].lower()}.co.uk",
            "directors": [rng.choice(PEOPLE)],
            "bank": {"account_name": acct, "iban": iban, "swift_bic": "BARCGB22",
                     "bank_name": "Barclays", "bank_country": "GB"},
            "documents": _docs(name, reg, vat),
            "payment_terms": "Net 30",
        }

        # A registry entry for every legitimate company. The 'fabricated'
        # category is deliberately withheld so it fails verification.
        in_registry = True

        if cat == "missing_field":
            sub["tax_id"] = None
        elif cat == "missing_doc":
            sub["documents"] = [d for d in sub["documents"] if d["doc_type"] != "bank_proof"]
        elif cat == "bad_iban":
            sub["bank"]["iban"] = broken_iban(rng)
        elif cat == "bad_taxid":
            sub["tax_id"] = "GB" + "".join(rng.choice("0123456789") for _ in range(5))  # too short
            for d in sub["documents"]:
                if d["extracted"].get("kind") == "vat_certificate":
                    d["extracted"]["vat_number"] = sub["tax_id"]
        elif cat == "country_mismatch":
            sub["tax_id"] = "DE" + "".join(rng.choice("0123456789") for _ in range(9))
            for d in sub["documents"]:
                if d["extracted"].get("kind") == "vat_certificate":
                    d["extracted"]["vat_number"] = sub["tax_id"]
        elif cat == "subtle_name_fraud":
            twist = rng.choice(["Holdings", "Group", "Trading", "Ventures"])
            sub["bank"]["account_name"] = f"{name.rsplit(' ', 1)[0]} {twist} Ltd"
            sub["documents"][-1]["extracted"]["account_name"] = sub["bank"]["account_name"]
        elif cat == "blatant_name_fraud":
            sub["bank"]["account_name"] = rng.choice(PEOPLE)
            sub["documents"][-1]["extracted"]["account_name"] = sub["bank"]["account_name"]
        elif cat == "fabricated":
            in_registry = False
        elif cat == "denied_party":
            # Reuse a real seed-list party with the matching DOB so it confirms.
            sub["directors"] = ["Dmitri Volkov", rng.choice(PEOPLE)]
            sub["director_details"] = [
                {"name": "Dmitri Volkov", "dob": "1971-08-14", "nationality": "RU"}]
        elif cat == "namesake_variant":
            # ~85% similar to the listed 'Dmitri Volkov', no DOB supplied.
            sub["directors"] = ["Dmytro Volkov", rng.choice(PEOPLE)]

        if in_registry:
            registry.append({"country": "GB", "registration_number": reg,
                             "legal_name": name, "status": "ACTIVE",
                             "incorporation_date": "2015-01-01"})

        cases.append((sub, cat))

    return cases, registry


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    n = max(n, len(CATEGORIES) * 5)      # enough per category to be meaningful
    cases, registry = generate(n)
    registry_check.set_registry_override(registry)

    per_cat: dict[str, list[bool]] = {c: [] for c in CATEGORIES}
    auto_tp = auto_fp = 0
    fraud_total = fraud_caught = 0
    fp_flags = 0                          # clean vendors wrongly sent to a human
    status_ok = 0

    try:
        for sub, cat in cases:
            status, findings, _ = assess(VendorSubmission(**sub))
            got = status.value
            want = EXPECTED[cat]
            ok = got == want
            per_cat[cat].append(ok)
            status_ok += ok

            if want == "APPROVED":
                if got == "APPROVED":
                    pass
                else:
                    fp_flags += 1         # clean vendor not approved = false positive
            if got == "APPROVED":
                auto_tp += want == "APPROVED"
                auto_fp += want != "APPROVED"

            if cat in FRAUD_CATEGORIES:
                fraud_total += 1
                fraud_caught += got in ("PENDING_REVIEW", "REJECTED")
    finally:
        registry_check.set_registry_override(None)

    print("=" * 76)
    print(f"  VOLUME EVALUATION — {len(cases)} generated submissions")
    print("=" * 76)
    print(f"  {'category':<22}{'n':>4}  {'accuracy':>9}   expected")
    print("  " + "-" * 72)
    for cat in CATEGORIES:
        r = per_cat[cat]
        acc = sum(r) / len(r) * 100 if r else 0
        print(f"  {cat:<22}{len(r):>4}  {acc:>8.0f}%   {EXPECTED[cat]}")

    auto_precision = auto_tp / (auto_tp + auto_fp) * 100 if (auto_tp + auto_fp) else 100
    fraud_recall = fraud_caught / fraud_total * 100 if fraud_total else 100
    fp_rate = fp_flags / sum(1 for _, c in cases if EXPECTED[c] == "APPROVED") * 100

    print("\n" + "=" * 76)
    print("  HEADLINE METRICS")
    print("=" * 76)
    print(f"  Status accuracy .............. {status_ok}/{len(cases)}  ({status_ok / len(cases) * 100:.1f}%)")
    print(f"  Auto-approve precision ....... {auto_precision:.1f}%   "
          f"({auto_tp} correct, {auto_fp} WRONG of {auto_tp + auto_fp} auto-approvals)")
    print(f"  Fraud / compliance recall .... {fraud_recall:.1f}%   "
          f"({fraud_caught}/{fraud_total} signal cases caught)")
    print(f"  False-positive rate .......... {fp_rate:.1f}%   "
          f"(clean vendors wrongly sent to review)")
    print("=" * 76)

    # The two numbers that must hold: no fraudulent auto-approval, and no
    # missed fraud signal.
    clean = auto_fp == 0 and fraud_caught == fraud_total
    print("  " + ("PASS — zero false approvals, every fraud signal caught."
                  if clean else "FAIL — see wrong auto-approvals or missed signals above."))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
