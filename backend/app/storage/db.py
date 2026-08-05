"""SQLite storage.

The `case_check` and `case_finding` tables are append-only — inserted, never
updated or deleted. The problem statement is explicit that today "the only
audit trail is whatever's in someone's inbox"; replacing that with a mutable
table would not be much of an improvement.

Reference data (the vendor master, the denied-party list) is read from JSON on
each check rather than loaded into the database, because unlike an invoice
pipeline nothing here mutates it. An onboarding decision produces a case, not
a change to the master file — promoting an approved vendor onto the master is a
separate step that a human authorises.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from backend.app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS onboarding_case (
    case_id          TEXT PRIMARY KEY,
    legal_name       TEXT NOT NULL,
    trading_name     TEXT,
    country          TEXT NOT NULL,
    contact_email    TEXT,
    status           TEXT NOT NULL,
    reviewer_summary TEXT NOT NULL DEFAULT '',
    vendor_email     TEXT,
    submission       TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    completed_at     TEXT,
    org_id           TEXT NOT NULL DEFAULT 'demo',
    vendor_token     TEXT,
    profile_id       TEXT,
    confidence       TEXT,
    -- Resubmission tracking. entity_key ties a vendor's attempts together;
    -- supersedes points at the prior case this one replaces.
    entity_key       TEXT,
    revision         INTEGER NOT NULL DEFAULT 1,
    supersedes       TEXT,
    superseded_by    TEXT,
    change_summary   TEXT,
    -- Reviewer resolution, once a human acts (separate from the automated status).
    resolution       TEXT
);

-- Append-only log of reviewer actions. This is the record that replaces
-- "whatever's in someone's inbox" with an actual system of record.
CREATE TABLE IF NOT EXISTS case_action (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL,
    action      TEXT NOT NULL,       -- approve | reject | request_info | resolve | reopen
    reviewer    TEXT,
    note        TEXT,
    prev_status TEXT,
    new_status  TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES onboarding_case(case_id)
);
CREATE INDEX IF NOT EXISTS idx_action_case ON case_action(case_id, id);

-- Append-only.
CREATE TABLE IF NOT EXISTS case_check (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    check_name  TEXT NOT NULL,
    label       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES onboarding_case(case_id)
);
CREATE INDEX IF NOT EXISTS idx_check_case ON case_check(case_id, seq);

-- Append-only.
CREATE TABLE IF NOT EXISTS case_finding (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL,
    code           TEXT NOT NULL,
    severity       INTEGER NOT NULL,
    severity_name  TEXT NOT NULL,
    check_name     TEXT NOT NULL,
    field          TEXT,
    message        TEXT NOT NULL,
    vendor_message TEXT,
    evidence       TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES onboarding_case(case_id)
);
CREATE INDEX IF NOT EXISTS idx_finding_case ON case_finding(case_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # DELETE rather than WAL: WAL needs shared-memory mapping, unavailable on
    # network and FUSE-mounted filesystems (OneDrive, mapped drives, bind
    # mounts). Write volume here is trivial.
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as c:
        c.executescript(SCHEMA)
        # Lightweight forward-migration: add any columns a pre-existing DB is
        # missing, so upgrading doesn't require dropping the database.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(onboarding_case)")}
        for name, ddl in [
            ("entity_key", "TEXT"), ("revision", "INTEGER NOT NULL DEFAULT 1"),
            ("supersedes", "TEXT"), ("superseded_by", "TEXT"),
            ("change_summary", "TEXT"), ("resolution", "TEXT"),
            ("org_id", "TEXT NOT NULL DEFAULT 'demo'"),
            ("vendor_token", "TEXT"),
            ("profile_id", "TEXT"),
            ("confidence", "TEXT"),
        ]:
            if name not in cols:
                c.execute(f"ALTER TABLE onboarding_case ADD COLUMN {name} {ddl}")


def reset_db() -> dict[str, int]:
    """Clear all cases. Reference data is JSON and is untouched."""
    init_db()
    with get_conn() as c:
        for t in ("case_action", "case_finding", "case_check", "onboarding_case"):
            c.execute(f"DELETE FROM {t}")
    return {"cases_cleared": 1}
