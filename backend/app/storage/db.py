"""Database storage using SQLAlchemy Core."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Text, ForeignKey,
    Index, text
)
from sqlalchemy.engine import Connection

from backend.app import config

log = logging.getLogger("vo.db")

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{config.DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True
)

metadata = MetaData()

onboarding_case = Table(
    "onboarding_case", metadata,
    Column("case_id", String, primary_key=True),
    Column("legal_name", String, nullable=False),
    Column("trading_name", String),
    Column("country", String, nullable=False),
    Column("contact_email", String),
    Column("status", String, nullable=False),
    Column("reviewer_summary", String, nullable=False, server_default=''),
    Column("vendor_email", String),
    Column("submission", String, nullable=False, server_default='{}'),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
    Column("org_id", String, nullable=False, server_default='demo'),
    Column("vendor_token", String),
    Column("profile_id", String),
    Column("confidence", String),
    Column("entity_key", String),
    Column("revision", Integer, nullable=False, server_default='1'),
    Column("supersedes", String),
    Column("superseded_by", String),
    Column("change_summary", String),
    Column("resolution", String)
)

case_action = Table(
    "case_action", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String, ForeignKey("onboarding_case.case_id"), nullable=False),
    Column("action", String, nullable=False),
    Column("reviewer", String),
    Column("note", String),
    Column("prev_status", String),
    Column("new_status", String),
    Column("created_at", String, nullable=False),
    Index("idx_action_case", "case_id", "id")
)

case_check = Table(
    "case_check", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String, ForeignKey("onboarding_case.case_id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("check_name", String, nullable=False),
    Column("label", String, nullable=False),
    Column("summary", String, nullable=False, server_default=''),
    Column("data", String, nullable=False, server_default='{}'),
    Column("duration_ms", Integer, nullable=False, server_default='0'),
    Column("created_at", String, nullable=False),
    Index("idx_check_case", "case_id", "seq")
)

case_finding = Table(
    "case_finding", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", String, ForeignKey("onboarding_case.case_id"), nullable=False),
    Column("code", String, nullable=False),
    Column("severity", Integer, nullable=False),
    Column("severity_name", String, nullable=False),
    Column("check_name", String, nullable=False),
    Column("field", String),
    Column("message", String, nullable=False),
    Column("vendor_message", String),
    Column("evidence", String, nullable=False, server_default='{}'),
    Column("created_at", String, nullable=False),
    Index("idx_finding_case", "case_id")
)

@contextmanager
def get_conn() -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn

def init_db() -> None:
    metadata.create_all(engine)
    if DATABASE_URL.startswith("sqlite"):
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys = ON"))
            conn.execute(text("PRAGMA journal_mode = DELETE"))
            conn.execute(text("PRAGMA synchronous = NORMAL"))
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Additive migration for SQLite databases created by an older schema.

    `metadata.create_all` creates missing TABLES but never alters existing
    ones. So the moment a column is added to a model, every database already
    on disk — a developer's, a deployed volume, a stale test fixture — starts
    failing every INSERT with "table has no column named X".

    Adding columns is the only schema change this project makes, and SQLite
    supports `ADD COLUMN` cheaply, so a full migration tool would be overkill.
    Renames or type changes would need one; if that day comes, reach for
    Alembic rather than extending this.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return                      # Postgres deployments get a real migration tool
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            try:
                existing = {
                    row[1] for row in
                    conn.execute(text(f'PRAGMA table_info("{table.name}")'))
                }
            except Exception:
                continue
            if not existing:
                continue            # table absent; create_all already handled it
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" ' \
                      f'{col.type.compile(engine.dialect)}'
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    ddl += f" DEFAULT {col.default.arg!r}"
                try:
                    conn.execute(text(ddl))
                    log.info("migrated: added %s.%s", table.name, col.name)
                except Exception as exc:      # pragma: no cover - defensive
                    log.warning("could not add %s.%s: %s", table.name, col.name, exc)

def reset_db() -> dict[str, int]:
    """Clear all cases. Reference data is JSON and is untouched."""
    init_db()
    with get_conn() as c:
        c.execute(case_action.delete())
        c.execute(case_finding.delete())
        c.execute(case_check.delete())
        c.execute(onboarding_case.delete())
    return {"cases_cleared": 1}
