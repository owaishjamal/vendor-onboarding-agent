"""Generate the reference data and the seven test submissions.

Banking details are COMPUTED, not typed. IBAN check digits are generated with
the real mod-97 algorithm and ABA routing numbers with the real 3-7-1
checksum, so the valid ones are genuinely valid and the one deliberately
broken submission is broken in a realistic way — a transposed digit, which is
what actually happens when someone copies an IBAN off a bank statement by hand.

Hand-typing these would produce fixtures that pass a regex and fail a real
checksum, which would make the validators look broken when they were right.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "backend" / "seed"
SUBS = ROOT / "data" / "submissions"
SEED.mkdir(parents=True, exist_ok=True)
SUBS.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Banking helpers - the real algorithms
# ---------------------------------------------------------------------------

def iban_check_digits(country: str, bban: str) -> str:
    """ISO 13616: compute the two check digits for a country + BBAN."""
    rearranged = bban + country + "00"
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged.upper())
    remainder = 0
    for i in range(0, len(digits), 7):
        remainder = int(str(remainder) + digits[i:i + 7]) % 97
    return f"{98 - remainder:02d}"


def make_iban(country: str, bban: str) -> str:
    return f"{country}{iban_check_digits(country, bban)}{bban}".upper()


def iban_is_valid(iban: str) -> bool:
    s = re.sub(r"\s+", "", iban).upper()
    rearranged = s[4:] + s[:4]
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    rem = 0
    for i in range(0, len(digits), 7):
        rem = int(str(rem) + digits[i:i + 7]) % 97
    return rem == 1


def make_aba(prefix8: str) -> str:
    """Append the check digit that makes the weighted 3-7-1 sum divisible by 10."""
    assert len(prefix8) == 8 and prefix8.isdigit()
    weights = (3, 7, 1, 3, 7, 1, 3, 7)
    total = sum(int(d) * w for d, w in zip(prefix8, weights))
    check = (10 - (total % 10)) % 10
    return prefix8 + str(check)


def bank_fingerprint(iban=None, account_number=None, routing=None):
    """Must match backend/app/checks/duplicates.py exactly."""
    if iban:
        basis = re.sub(r"\s+", "", iban).upper()
    elif account_number:
        basis = re.sub(r"\D", "", account_number)
        if routing:
            basis = re.sub(r"\D", "", routing) + ":" + basis
    else:
        return None
    return hashlib.sha256(basis.encode()).hexdigest()[:24] if basis else None


# ---------------------------------------------------------------------------
# Computed banking details
# ---------------------------------------------------------------------------

# US
NORTHWIND_ROUTING = make_aba("02100002")
NORTHWIND_ACCOUNT = "483920117"

# The account Continental Freight submits, which already belongs to Atlas.
SHARED_ROUTING = make_aba("12100003")
SHARED_ACCOUNT = "770154288"

# Meridian Rail (VS-08 namesake case).
MERIDIAN_ROUTING = make_aba("04100003")

# VS-10 subtle name fraud (real GB company, account under a "Holdings" name).
HARBOURSTONE_IBAN = make_iban("GB", "LOYD30969912345678")
# VS-11 fabricated vendor (internally consistent, but not in any registry).
FABRICATED_IBAN = make_iban("GB", "HBUK40130099887766")

# GB
BRIGHTLINE_IBAN = make_iban("GB", "BARC20035387143214")
PINNACLE_IBAN_GOOD = make_iban("GB", "NWBK60161331926819")
# Transpose two digits in the BBAN and keep the ORIGINAL check digits, which is
# exactly what a mis-copied IBAN looks like: right shape, wrong checksum.
_p = PINNACLE_IBAN_GOOD
PINNACLE_IBAN_TYPO = _p[:10] + _p[11] + _p[10] + _p[12:]

# DE — BBAN is 8-digit Bankleitzahl + 10-digit account = 18, giving a 22-char IBAN.
KESSLER_IBAN = make_iban("DE", "370400440532013000")


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

VENDOR_MASTER = [
    {
        "vendor_id": "V-2001",
        "legal_name": "Atlas Haulage Group Inc",
        "country": "US",
        "tax_id": "82-4419307",
        "registration_number": "C3319022",
        # The collision that Continental Freight will trip.
        "bank_account_fingerprint": bank_fingerprint(
            account_number=SHARED_ACCOUNT, routing=SHARED_ROUTING),
        "status": "ACTIVE",
    },
    {
        "vendor_id": "V-2002",
        "legal_name": "Halden Office Interiors Ltd",
        "country": "GB",
        "tax_id": "GB884120993",
        "registration_number": "07741208",
        "bank_account_fingerprint": bank_fingerprint(
            iban=make_iban("GB", "HBUK40127698214477")),
        "status": "ACTIVE",
    },
    {
        "vendor_id": "V-2003",
        "legal_name": "Ferro Precision Components GmbH",
        "country": "DE",
        "tax_id": "DE811204739",
        "registration_number": "HRB 52108",
        "bank_account_fingerprint": bank_fingerprint(
            iban=make_iban("DE", "50010517547061853")),
        "status": "ACTIVE",
    },
]

# Mock company registry — the external source of truth the registry check
# verifies against. Every LEGITIMATE vendor identity is here (so they verify);
# the fabricated adversarial vendor deliberately is NOT, so it fails.
COMPANY_REGISTRY = [
    {"country": "US", "registration_number": "C4821990",
     "legal_name": "Northwind Components Inc", "status": "ACTIVE",
     "incorporation_date": "2016-04-11"},
    {"country": "GB", "registration_number": "09442817",
     "legal_name": "Brightline Analytics Ltd", "status": "ACTIVE",
     "incorporation_date": "2015-01-20"},
    {"country": "DE", "registration_number": "HRB84721",
     "legal_name": "Kessler Industrietechnik GmbH", "status": "ACTIVE",
     "incorporation_date": "2011-09-03"},
    {"country": "IN", "registration_number": "U17291TN2016PTC112884",
     "legal_name": "Sundara Textiles Private Limited", "status": "ACTIVE",
     "incorporation_date": "2016-06-22"},
    {"country": "US", "registration_number": "C7710455",
     "legal_name": "Continental Freight Services LLC", "status": "ACTIVE",
     "incorporation_date": "2018-02-14"},
    {"country": "SG", "registration_number": "201644821M",
     "legal_name": "Volkov Maritime Trading Pte Ltd", "status": "ACTIVE",
     "incorporation_date": "2016-11-30"},
    {"country": "GB", "registration_number": "11238904",
     "legal_name": "Pinnacle Design Studio Ltd", "status": "ACTIVE",
     "incorporation_date": "2018-03-19"},
    {"country": "US", "registration_number": "C9017722",
     "legal_name": "Meridian Rail Components LLC", "status": "ACTIVE",
     "incorporation_date": "2017-07-08"},
    # VS-10's real company (the fraud there is the bank account, not the entity).
    {"country": "GB", "registration_number": "08812204",
     "legal_name": "Harbourstone Interiors Ltd", "status": "ACTIVE",
     "incorporation_date": "2014-05-27"},
    # A dissolved company, to exercise the INACTIVE path if needed.
    {"country": "GB", "registration_number": "06655001",
     "legal_name": "Old Kiln Pottery Ltd", "status": "DISSOLVED",
     "incorporation_date": "2008-08-08"},
]


DENIED_PARTIES = [
    {
        "name": "Dmitri Volkov",
        "kind": "INDIVIDUAL",
        "list_name": "OFAC_SDN",
        "country": "RU",
        "dob": "1971-08-14",
        "nationality": "RU",
        "aliases": ["Dmitriy Volkov", "D. Volkov"],
    },
    {
        "name": "Kayenne Petrochemical Holdings",
        "kind": "ENTITY",
        "list_name": "EU_CFSP",
        "country": "IR",
        "aliases": ["Kayenne Petrochemical", "Kayenne Holdings"],
    },
    {
        "name": "Sergei Antonov",
        "kind": "INDIVIDUAL",
        "list_name": "UK_HMT",
        "country": "BY",
        "dob": "1968-02-03",
        "nationality": "BY",
        "aliases": ["S. Antonov"],
    },
    {
        "name": "Orion Maritime Logistics FZE",
        "kind": "ENTITY",
        "list_name": "OFAC_SDN",
        "country": "AE",
        "aliases": ["Orion Maritime"],
    },
]


# ---------------------------------------------------------------------------
# The seven submissions
# ---------------------------------------------------------------------------

def doc(doc_type, filename, kind, scan=False, **extracted):
    return {"doc_type": doc_type, "filename": filename, "readable": True,
            "_scan": scan, "extracted": {"kind": kind, **extracted}}


SUBMISSIONS = [
    # -- VS-01 HAPPY PATH ---------------------------------------------------
    {
        "_file": "VS-01_northwind_clean.json",
        "_expect": "APPROVED",
        "_scenario": "Complete, internally consistent, documents corroborate the form.",
        "legal_name": "Northwind Components Inc",
        "trading_name": "Northwind Components",
        "country": "US",
        "entity_type": "Corporation",
        "registration_number": "C4821990",
        "tax_id": "47-3821990",
        "address_line1": "1400 Quarry Road, Suite 320",
        "address_city": "Portland, OR",
        "address_postcode": "97209",
        "address_country": "US",
        "contact_name": "Dana Whitfield",
        "contact_email": "dana.whitfield@northwindcomponents.com",
        "website": "https://northwindcomponents.com",
        "directors": ["Dana Whitfield", "Michael Orr"],
        "bank": {
            "account_name": "Northwind Components Inc",
            "account_number": NORTHWIND_ACCOUNT,
            "routing_number": NORTHWIND_ROUTING,
            "bank_name": "Pacific Commerce Bank",
            "bank_country": "US",
        },
        "documents": [
            doc("tax_form", "w9_northwind.pdf", "w9",
                legal_name="Northwind Components Inc", ein="47-3821990",
                issue_date="2026-02-11"),
            doc("bank_proof", "bank_letter_northwind.pdf", "bank_letter",
                account_name="Northwind Components Inc",
                account_number=NORTHWIND_ACCOUNT, issue_date="2026-05-30"),
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-02 INCOMPLETE ---------------------------------------------------
    # Two things missing, of different kinds. The point of the case is that the
    # vendor is told about BOTH in one message, not one per round trip.
    {
        "_file": "VS-02_brightline_incomplete.json",
        "_expect": "PENDING_INFO",
        "_scenario": "Missing VAT number and missing bank proof document. Vendor-fixable.",
        "legal_name": "Brightline Analytics Ltd",
        "country": "GB",
        "entity_type": "Private limited company",
        "registration_number": "09442817",
        "tax_id": None,                       # missing
        "address_line1": "Unit 4, Bower Yard, Long Lane",
        "address_city": "Manchester",
        "address_postcode": "M4 6JN",
        "address_country": "GB",
        "contact_name": "Priya Raman",
        "contact_email": "priya@brightlineanalytics.co.uk",
        "website": "https://brightlineanalytics.co.uk",
        "directors": ["Priya Raman"],
        "bank": {
            "account_name": "Brightline Analytics Limited",
            "iban": BRIGHTLINE_IBAN,
            "swift_bic": "BARCGB22",
            "bank_name": "Barclays Bank",
            "bank_country": "GB",
        },
        "documents": [
            doc("incorporation", "coi_brightline.pdf", "certificate_of_incorporation",
                legal_name="Brightline Analytics Ltd", company_number="09442817",
                issue_date="2025-01-20"),
            # no bank_proof, no vat_certificate
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-03 BANK NAME MISMATCH ------------------------------------------
    # Everything valid in isolation. The account holder is a person, not the
    # company. This is the payment-redirection signature.
    {
        "_file": "VS-03_kessler_bank_mismatch.json",
        "_expect": "PENDING_REVIEW",
        "_scenario": "All fields individually valid; bank account holder is not the company.",
        "legal_name": "Kessler Industrietechnik GmbH",
        "country": "DE",
        "entity_type": "GmbH",
        "registration_number": "HRB 84721",
        "tax_id": "DE294817355",
        "address_line1": "Industriestrasse 47",
        "address_city": "Stuttgart",
        "address_postcode": "70565",
        "address_country": "DE",
        "contact_name": "Andreas Kessler",
        "contact_email": "a.kessler@kessler-it.de",
        "website": "https://kessler-it.de",
        "directors": ["Andreas Kessler"],
        "bank": {
            "account_name": "K. Weber Privatkonto",   # <- not the company
            "iban": KESSLER_IBAN,
            "swift_bic": "COBADEFFXXX",
            "bank_name": "Commerzbank",
            "bank_country": "DE",
        },
        "documents": [
            doc("incorporation", "hra_kessler.pdf", "handelsregisterauszug",
                legal_name="Kessler Industrietechnik GmbH",
                registration_number="HRB 84721", issue_date="2026-01-15"),
            doc("tax_form", "ustid_kessler.pdf", "vat_certificate",
                legal_name="Kessler Industrietechnik GmbH", vat_number="DE294817355",
                issue_date="2026-01-15"),
            doc("bank_proof", "bankbestaetigung.pdf", "bank_letter",
                account_name="K. Weber", issue_date="2026-06-02"),
        ],
        "payment_terms": "Net 45",
    },

    # -- VS-04 COUNTRY / TAX ID CONTRADICTION ------------------------------
    # Produces findings at two different severities. Status comes from the
    # higher one, and the lower one still reaches the vendor email later.
    {
        "_file": "VS-04_sundara_country_mismatch.json",
        "_expect": "PENDING_REVIEW",
        "_scenario": "Claims Indian registration but supplies a UK VAT number.",
        "legal_name": "Sundara Textiles Private Limited",
        "country": "IN",
        "entity_type": "Private Limited",
        "registration_number": "U17291TN2016PTC112884",
        "tax_id": "GB428193756",              # <- a UK VAT number on an Indian vendor
        "address_line1": "18 Anna Salai, Guindy",
        "address_city": "Chennai",
        "address_postcode": "600032",
        "address_country": "IN",
        "contact_name": "Ravi Subramanian",
        "contact_email": "ravi@sundaratextiles.in",
        "website": "https://sundaratextiles.in",
        "directors": ["Ravi Subramanian", "Lakshmi Subramanian"],
        "bank": {
            "account_name": "Sundara Textiles Private Limited",
            "account_number": "912847100338",
            "ifsc": "HDFC0001204",
            "swift_bic": "HDFCINBB",
            "bank_name": "HDFC Bank",
            "bank_country": "IN",
        },
        "documents": [
            doc("incorporation", "coi_sundara.pdf", "certificate_of_incorporation",
                legal_name="Sundara Textiles Private Limited",
                registration_number="U17291TN2016PTC112884", issue_date="2025-11-08"),
            doc("tax_form", "gst_sundara.pdf", "gst_certificate",
                legal_name="Sundara Textiles Private Limited",
                tax_id="GB428193756", issue_date="2026-03-01"),
            doc("bank_proof", "cheque_sundara.pdf", "cancelled_cheque",
                account_name="Sundara Textiles Private Limited",
                account_number="912847100338", issue_date="2026-04-19"),
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-05 SHARED BANK ACCOUNT -----------------------------------------
    # The strongest fraud signal in the set, and invisible from the submission
    # alone - it only exists relative to the vendor master.
    {
        "_file": "VS-05_continental_shared_account.json",
        "_expect": "PENDING_REVIEW",
        "_scenario": "Clean submission, but the bank account belongs to another vendor.",
        "legal_name": "Continental Freight Services LLC",
        "country": "US",
        "entity_type": "LLC",
        "registration_number": "C7710455",
        "tax_id": "31-8827104",
        "address_line1": "2255 Corporate Drive",
        "address_city": "Memphis, TN",
        "address_postcode": "38118",
        "address_country": "US",
        "contact_name": "Marcus Hale",
        "contact_email": "m.hale@continentalfreightsvc.com",
        "website": "https://continentalfreightsvc.com",
        "directors": ["Marcus Hale"],
        "bank": {
            "account_name": "Continental Freight Services LLC",
            "account_number": SHARED_ACCOUNT,      # <- already Atlas Haulage's
            "routing_number": SHARED_ROUTING,
            "bank_name": "Mid-South National Bank",
            "bank_country": "US",
        },
        "documents": [
            doc("tax_form", "w9_continental.pdf", "w9",
                legal_name="Continental Freight Services LLC", ein="31-8827104",
                issue_date="2026-04-02"),
            doc("bank_proof", "voided_cheque_continental.pdf", "voided_cheque",
                account_name="Continental Freight Services LLC",
                account_number=SHARED_ACCOUNT, issue_date="2026-06-11"),
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-06 DENIED PARTY -------------------------------------------------
    # Also carries a missing document, so the demo can show that a rejected
    # case still runs every check - and that the vendor email is suppressed
    # even though a vendor-fixable finding exists.
    {
        "_file": "VS-06_volkov_denied_party.json",
        "_expect": "REJECTED",
        "_scenario": "Director appears on a denied-party list. Terminal.",
        "legal_name": "Volkov Maritime Trading Pte Ltd",
        "country": "SG",
        "entity_type": "Private Limited",
        "registration_number": "201644821M",
        "tax_id": "201644821M",
        "address_line1": "12 Marina Boulevard, #18-04",
        "address_city": "Singapore",
        "address_postcode": "018980",
        "address_country": "SG",
        "contact_name": "Dmitri Volkov",
        "contact_email": "dvolkov@volkovmaritime.sg",
        "website": "https://volkovmaritime.sg",
        "directors": ["Dmitri Volkov", "Chen Wei Ling"],
        # DOB matches the OFAC entry — the hit is confirmed on secondary
        # identifiers, not just the name.
        "director_details": [
            {"name": "Dmitri Volkov", "dob": "1971-08-14", "nationality": "RU"},
            {"name": "Chen Wei Ling", "dob": "1985-11-02", "nationality": "SG"},
        ],
        "bank": {
            "account_name": "Volkov Maritime Trading Pte Ltd",
            "account_number": "3841205577",
            "swift_bic": "DBSSSGSG",
            "bank_name": "DBS Bank",
            "bank_country": "SG",
        },
        "documents": [
            doc("incorporation", "acra_volkov.pdf", "acra_bizfile",
                legal_name="Volkov Maritime Trading Pte Ltd",
                registration_number="201644821M", issue_date="2026-02-20"),
            # bank_proof deliberately absent
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-07 IBAN TYPO ----------------------------------------------------
    # Contrast with VS-03: this one is also a banking problem, but it is a
    # mistake rather than a signal, so it is routed to the vendor and not to a
    # reviewer. Getting that triage right is the point.
    {
        "_file": "VS-07_pinnacle_iban_typo.json",
        "_expect": "PENDING_INFO",
        "_scenario": "Everything present and consistent; IBAN fails its checksum (transposed digits).",
        "legal_name": "Pinnacle Design Studio Ltd",
        "country": "GB",
        "entity_type": "Private limited company",
        "registration_number": "11238904",
        "tax_id": "GB331920477",
        "address_line1": "Second Floor, 88 Rivington Street",
        "address_city": "London",
        "address_postcode": "EC2A 3AY",
        "address_country": "GB",
        "contact_name": "Elena Marsh",
        "contact_email": "elena@pinnacledesign.studio",
        "website": "https://pinnacledesign.studio",
        "directors": ["Elena Marsh", "Tom Alderton"],
        "bank": {
            "account_name": "Pinnacle Design Studio Ltd",
            "iban": PINNACLE_IBAN_TYPO,       # <- fails mod-97
            "swift_bic": "NWBKGB2L",
            "bank_name": "NatWest",
            "bank_country": "GB",
        },
        "documents": [
            doc("incorporation", "coi_pinnacle.pdf", "certificate_of_incorporation",
                legal_name="Pinnacle Design Studio Ltd", company_number="11238904",
                issue_date="2025-06-30"),
            doc("tax_form", "vat_pinnacle.pdf", "vat_certificate",
                legal_name="Pinnacle Design Studio Ltd", vat_number="GB331920477",
                issue_date="2025-07-14"),
            doc("bank_proof", "bank_letter_pinnacle.pdf", "bank_letter", scan=True,
                account_name="Pinnacle Design Studio Ltd", issue_date="2026-05-22"),
        ],
        "payment_terms": "Net 14",
    },

    # -- VS-08 INNOCENT NAMESAKE -------------------------------------------
    # A director shares a name EXACTLY with a party on the UK sanctions list.
    # On name alone this rejects. But the vendor supplied a date of birth and
    # nationality that DIFFER from the listed person — so it's a different
    # individual, the hit clears, and the vendor is approved. This is the case
    # that proves screening doesn't punish people for their surname, and it's
    # why the near/confirm bands and secondary matching exist.
    {
        "_file": "VS-08_meridian_namesake.json",
        "_expect": "APPROVED",
        "_scenario": "Director's name matches a sanctions entry exactly, but DOB and nationality clear it as a different person.",
        "legal_name": "Meridian Rail Components LLC",
        "trading_name": "Meridian Rail",
        "country": "US",
        "entity_type": "LLC",
        "registration_number": "C9017722",
        "tax_id": "58-2019447",
        "address_line1": "870 Industrial Parkway",
        "address_city": "Cleveland, OH",
        "address_postcode": "44114",
        "address_country": "US",
        "contact_name": "Grace Halloran",
        "contact_email": "grace.halloran@meridianrail.com",
        "website": "https://meridianrail.com",
        "directors": ["Grace Halloran", "Sergei Antonov"],   # exact list-name collision
        "director_details": [
            {"name": "Grace Halloran", "dob": "1979-04-17", "nationality": "US"},
            # Same name as the UK_HMT entry, but born 1990 in the US, not 1968 in BY.
            {"name": "Sergei Antonov", "dob": "1990-09-25", "nationality": "US"},
        ],
        "bank": {
            "account_name": "Meridian Rail Components LLC",
            "account_number": "550120933",
            "routing_number": MERIDIAN_ROUTING,
            "bank_name": "Great Lakes Bank",
            "bank_country": "US",
        },
        "documents": [
            doc("tax_form", "w9_meridian.pdf", "w9",
                legal_name="Meridian Rail Components LLC", ein="58-2019447",
                issue_date="2026-03-05"),
            doc("bank_proof", "bank_letter_meridian.pdf", "bank_letter",
                account_name="Meridian Rail Components LLC", issue_date="2026-05-28"),
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-09 CORRECTED RESUBMISSION OF VS-02 -----------------------------
    # Same Companies House number as Brightline (VS-02), so the system knows
    # it's the same vendor coming back. The VAT number and the bank letter that
    # were missing the first time are now supplied. Standalone this approves;
    # run AFTER VS-02 it supersedes it and shows "2 of 2 items resolved". This
    # closes the loop the problem statement describes: ask, wait, re-check.
    {
        "_file": "VS-09_brightline_resubmitted.json",
        "_expect": "APPROVED",
        "_scenario": "Brightline resubmits with the previously-missing VAT number and bank letter. Supersedes VS-02.",
        "legal_name": "Brightline Analytics Ltd",
        "country": "GB",
        "entity_type": "Private limited company",
        "registration_number": "09442817",          # same entity as VS-02
        "tax_id": "GB417029558",                     # now supplied
        "address_line1": "Unit 4, Bower Yard, Long Lane",
        "address_city": "Manchester",
        "address_postcode": "M4 6JN",
        "address_country": "GB",
        "contact_name": "Priya Raman",
        "contact_email": "priya@brightlineanalytics.co.uk",
        "website": "https://brightlineanalytics.co.uk",
        "directors": ["Priya Raman"],
        "bank": {
            "account_name": "Brightline Analytics Limited",
            "iban": BRIGHTLINE_IBAN,
            "swift_bic": "BARCGB22",
            "bank_name": "Barclays Bank",
            "bank_country": "GB",
        },
        "documents": [
            doc("incorporation", "coi_brightline.pdf", "certificate_of_incorporation",
                legal_name="Brightline Analytics Ltd", company_number="09442817",
                issue_date="2025-01-20"),
            doc("tax_form", "vat_brightline.pdf", "vat_certificate",       # now supplied
                legal_name="Brightline Analytics Ltd", vat_number="GB417029558",
                issue_date="2025-02-02"),
            doc("bank_proof", "bank_letter_brightline.pdf", "bank_letter",  # now supplied
                account_name="Brightline Analytics Limited", issue_date="2026-05-30"),
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-10 SUBTLE NAME FRAUD -------------------------------------------
    # The plausible version of VS-03. The company is real and verifies against
    # the registry. Every field is valid. But the bank account is held by
    # "Harbourstone Interiors Holdings Ltd" — one added word, ~90%+ string
    # similarity, and a threshold alone waves it through. It's a different legal
    # entity, and it's exactly how real payment redirection looks: not "K.
    # Weber", but a name close enough to pass a glance. Caught by the added-
    # entity-token rule, routed to review (group treasury is a legitimate
    # explanation), never auto-approved.
    {
        "_file": "VS-10_harbourstone_related_account.json",
        "_expect": "PENDING_REVIEW",
        "_scenario": "Real, verified company; bank account held by '<Company> Holdings' — a subtle redirection a similarity threshold alone would miss.",
        "legal_name": "Harbourstone Interiors Ltd",
        "country": "GB",
        "entity_type": "Private limited company",
        "registration_number": "08812204",
        "tax_id": "GB194887201",
        "address_line1": "3 Cooperage Yard, Ropewalk",
        "address_city": "Bristol",
        "address_postcode": "BS1 6WE",
        "address_country": "GB",
        "contact_name": "Owen Hartley",
        "contact_email": "owen@harbourstoneinteriors.co.uk",
        "website": "https://harbourstoneinteriors.co.uk",
        "directors": ["Owen Hartley", "Rachel Nunes"],
        "bank": {
            "account_name": "Harbourstone Interiors Holdings Ltd",   # <- added word
            "iban": HARBOURSTONE_IBAN,
            "swift_bic": "LOYDGB2L",
            "bank_name": "Lloyds Bank",
            "bank_country": "GB",
        },
        "documents": [
            doc("incorporation", "coi_harbourstone.pdf", "certificate_of_incorporation",
                legal_name="Harbourstone Interiors Ltd", company_number="08812204",
                issue_date="2014-05-27"),
            doc("tax_form", "vat_harbourstone.pdf", "vat_certificate",
                legal_name="Harbourstone Interiors Ltd", vat_number="GB194887201",
                issue_date="2024-06-01"),
            doc("bank_proof", "bank_letter_harbourstone.pdf", "bank_letter",
                account_name="Harbourstone Interiors Holdings Ltd", issue_date="2026-05-20"),
        ],
        "payment_terms": "Net 30",
    },

    # -- VS-11 FABRICATED VENDOR -------------------------------------------
    # The case that proves internal consistency is not enough. Everything lines
    # up: valid VAT format, valid IBAN, documents that agree with the form,
    # clean screening, no duplicate. It looks perfect. But the registration
    # number does not exist in the registry — the company is invented. Without
    # external verification this auto-approves; with it, it can't. This is the
    # hole registry verification closes.
    {
        "_file": "VS-11_fabricated_vendor.json",
        "_expect": "PENDING_REVIEW",
        "_scenario": "Internally flawless, but the registration number exists in no registry — a fabricated company that only external verification catches.",
        "legal_name": "Ashcroft Medical Supplies Ltd",
        "country": "GB",
        "entity_type": "Private limited company",
        "registration_number": "13998201",         # not in the registry
        "tax_id": "GB556201884",
        "address_line1": "Suite 2, 44 Kingsway",
        "address_city": "London",
        "address_postcode": "WC2B 6EN",
        "address_country": "GB",
        "contact_name": "Julia Bennett",
        "contact_email": "j.bennett@ashcroftmedical.co.uk",
        "website": "https://ashcroftmedical.co.uk",
        "directors": ["Julia Bennett"],
        "bank": {
            "account_name": "Ashcroft Medical Supplies Ltd",
            "iban": FABRICATED_IBAN,
            "swift_bic": "HBUKGB4B",
            "bank_name": "HSBC",
            "bank_country": "GB",
        },
        "documents": [
            doc("incorporation", "coi_ashcroft.pdf", "certificate_of_incorporation",
                legal_name="Ashcroft Medical Supplies Ltd", company_number="13998201",
                issue_date="2022-04-12"),
            doc("tax_form", "vat_ashcroft.pdf", "vat_certificate",
                legal_name="Ashcroft Medical Supplies Ltd", vat_number="GB556201884",
                issue_date="2022-05-01"),
            doc("bank_proof", "bank_letter_ashcroft.pdf", "bank_letter",
                account_name="Ashcroft Medical Supplies Ltd", issue_date="2026-05-25"),
        ],
        "payment_terms": "Net 30",
    },
]


def main() -> None:
    # --- sanity on the computed banking details
    assert iban_is_valid(BRIGHTLINE_IBAN), "Brightline IBAN should be valid"
    assert iban_is_valid(KESSLER_IBAN), "Kessler IBAN should be valid"
    assert iban_is_valid(PINNACLE_IBAN_GOOD), "Pinnacle base IBAN should be valid"
    assert not iban_is_valid(PINNACLE_IBAN_TYPO), (
        "the typo IBAN must FAIL mod-97, otherwise VS-07 proves nothing")
    for r in (NORTHWIND_ROUTING, SHARED_ROUTING):
        w = (3, 7, 1, 3, 7, 1, 3, 7, 1)
        assert sum(int(d) * x for d, x in zip(r, w)) % 10 == 0, f"bad ABA {r}"

    assert iban_is_valid(HARBOURSTONE_IBAN), "Harbourstone IBAN should be valid"
    assert iban_is_valid(FABRICATED_IBAN), "Fabricated-vendor IBAN should be valid (only the registry fails)"

    (SEED / "vendor_master.json").write_text(json.dumps(VENDOR_MASTER, indent=2) + "\n")
    (SEED / "denied_parties.json").write_text(json.dumps(DENIED_PARTIES, indent=2) + "\n")
    (SEED / "company_registry.json").write_text(json.dumps(COMPANY_REGISTRY, indent=2) + "\n")
    print(f"vendor_master.json    {len(VENDOR_MASTER)} vendors")
    print(f"denied_parties.json   {len(DENIED_PARTIES)} listed parties")
    print(f"company_registry.json {len(COMPANY_REGISTRY)} registered companies")
    print()

    from render_documents import render_document
    DOCS = ROOT / "data" / "documents"
    rendered_count = 0

    manifest = []
    for s in SUBMISSIONS:
        s = dict(s)
        fname, expect, scenario = s.pop("_file"), s.pop("_expect"), s.pop("_scenario")
        sid = fname.split("_")[0]
        s["submission_id"] = sid

        # Render every attached document to a real file the pipeline reads for
        # real, and point the submission at it via `path`.
        s["documents"] = [dict(d) for d in s.get("documents", [])]
        for d in s["documents"]:
            scan = d.pop("_scan", False)
            actual = render_document(
                d["extracted"], d["filename"], DOCS / sid / d["filename"], scan=scan)
            d["path"] = f"{sid}/{actual}"
            d["filename"] = actual
            rendered_count += 1

        (SUBS / fname).write_text(json.dumps(s, indent=2) + "\n")
        manifest.append({
            "file": fname, "submission_id": sid,
            "legal_name": s["legal_name"], "country": s["country"],
            "scenario": scenario, "expected_status": expect,
        })
        print(f"  {fname:<42} -> {expect}")

    print(f"\n{rendered_count} documents rendered to {DOCS}")

    (SUBS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n{len(SUBMISSIONS)} submissions written to {SUBS}")
    print(f"\nComputed banking details:")
    print(f"  Northwind routing (valid ABA)  {NORTHWIND_ROUTING}")
    print(f"  Shared routing    (valid ABA)  {SHARED_ROUTING}")
    print(f"  Brightline IBAN   (valid)      {BRIGHTLINE_IBAN}")
    print(f"  Kessler IBAN      (valid)      {KESSLER_IBAN}")
    print(f"  Pinnacle IBAN     (VALID base) {PINNACLE_IBAN_GOOD}")
    print(f"  Pinnacle IBAN     (typo, bad)  {PINNACLE_IBAN_TYPO}")


if __name__ == "__main__":
    main()
