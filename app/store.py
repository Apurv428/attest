"""SQLite persistence for Attest.

Deliberately boring: three tables (expenses, decisions, audit_log) plus a
human_reviews table. The audit_log is append-only and hash-chained; see
audit.py for the chaining logic.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("ATTEST_DB", os.path.join(os.path.dirname(__file__), "..", "data", "attest.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    employee TEXT NOT NULL,
    vendor TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    date TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    description TEXT DEFAULT '',
    receipt_id TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS check_results (
    expense_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    label TEXT NOT NULL,
    triggered INTEGER NOT NULL,
    severity REAL NOT NULL,
    summary TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (expense_id, check_name)
);

CREATE TABLE IF NOT EXISTS decisions (
    expense_id TEXT PRIMARY KEY,
    route TEXT NOT NULL,              -- auto_approved | review
    risk REAL NOT NULL,
    triggered_checks TEXT NOT NULL,   -- JSON list
    rationale TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_reviews (
    expense_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,             -- approved | rejected
    reviewer TEXT NOT NULL,
    note TEXT DEFAULT '',
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    expense_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT NOT NULL,             -- JSON
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def db(db_path: str | None = None):
    con = connect(db_path)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def reset(db_path: str | None = None):
    path = db_path or DB_PATH
    if os.path.exists(path):
        os.remove(path)


# ---- writes ----------------------------------------------------------------

def insert_expense(con, e):
    con.execute(
        "INSERT OR REPLACE INTO expenses VALUES (?,?,?,?,?,?,?,?,?)",
        (e.id, e.employee, e.vendor, e.category, e.amount, e.date,
         e.submitted_at, e.description, e.receipt_id),
    )


def insert_check(con, expense_id: str, r):
    con.execute(
        "INSERT OR REPLACE INTO check_results VALUES (?,?,?,?,?,?,?)",
        (expense_id, r.check, r.label, int(r.triggered), r.severity,
         r.summary, json.dumps(r.evidence, sort_keys=True)),
    )


def insert_decision(con, d):
    con.execute(
        "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?)",
        (d.expense_id, d.route, d.risk, json.dumps(d.triggered_checks),
         d.rationale, d.decided_at),
    )


def insert_review(con, expense_id: str, action: str, reviewer: str, note: str, ts: str):
    con.execute(
        "INSERT OR REPLACE INTO human_reviews VALUES (?,?,?,?,?)",
        (expense_id, action, reviewer, note, ts),
    )


# ---- reads -----------------------------------------------------------------

def all_expenses(con) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM expenses ORDER BY date, id").fetchall()


def expense(con, expense_id: str):
    return con.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()


def checks_for(con, expense_id: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM check_results WHERE expense_id=? ORDER BY triggered DESC, severity DESC",
        (expense_id,),
    ).fetchall()


def decision_for(con, expense_id: str):
    return con.execute("SELECT * FROM decisions WHERE expense_id=?", (expense_id,)).fetchone()


def review_for(con, expense_id: str):
    return con.execute("SELECT * FROM human_reviews WHERE expense_id=?", (expense_id,)).fetchone()


def audit_rows(con, expense_id: str | None = None, limit: int = 500) -> list[sqlite3.Row]:
    if expense_id:
        return con.execute(
            "SELECT * FROM audit_log WHERE expense_id=? ORDER BY seq ASC", (expense_id,)
        ).fetchall()
    return con.execute(
        "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,)
    ).fetchall()
