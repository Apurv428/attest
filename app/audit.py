"""The trust layer: an append-only, hash-chained audit log.

Every action the digital employee or a human takes is recorded as an
AuditEvent. Each event's hash covers its own content *plus* the previous
event's hash, so history cannot be rewritten without breaking the chain.
`verify_chain` re-walks the whole log and reports the first break, if any.

This is the product. The fraud checks decide *what* to flag; this module
is what lets a CFO or an auditor trust the system with money.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 16

AGENT = "attest.agent/v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(prev_hash: str, ts: str, expense_id: str | None, actor: str,
          action: str, summary: str, detail_json: str) -> str:
    payload = "|".join([prev_hash, ts, expense_id or "", actor, action, summary, detail_json])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def append(con, *, actor: str, action: str, summary: str,
           expense_id: str | None = None, detail: dict | None = None,
           ts: str | None = None) -> str:
    """Append one event to the chain. Returns the new event's hash."""
    ts = ts or _now()
    detail_json = json.dumps(detail or {}, sort_keys=True, ensure_ascii=False)
    row = con.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    h = _hash(prev, ts, expense_id, actor, action, summary, detail_json)
    con.execute(
        "INSERT INTO audit_log (ts, expense_id, actor, action, summary, detail, prev_hash, hash) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (ts, expense_id, actor, action, summary, detail_json, prev, h),
    )
    return h


def verify_chain(con) -> dict:
    """Re-walk the entire log, recomputing every hash.

    Returns {"intact": bool, "events": n, "first_break": seq | None}.
    """
    rows = con.execute("SELECT * FROM audit_log ORDER BY seq ASC").fetchall()
    prev = GENESIS
    for r in rows:
        expected = _hash(prev, r["ts"], r["expense_id"], r["actor"],
                         r["action"], r["summary"], r["detail"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"intact": False, "events": len(rows), "first_break": r["seq"]}
        prev = r["hash"]
    return {"intact": True, "events": len(rows), "first_break": None}


def packet(con, expense_id: str) -> dict:
    """Export a self-contained audit packet for one expense.

    Everything an auditor needs: the expense, every check with its
    evidence, the agent's decision and rationale, any human review, and
    the ordered audit events with their hashes.
    """
    from . import store

    e = store.expense(con, expense_id)
    if not e:
        return {}
    checks = [dict(r) for r in store.checks_for(con, expense_id)]
    for c in checks:
        c["evidence"] = json.loads(c["evidence"])
        c["triggered"] = bool(c["triggered"])
    d = store.decision_for(con, expense_id)
    review = store.review_for(con, expense_id)
    events = []
    for r in store.audit_rows(con, expense_id):
        ev = dict(r)
        ev["detail"] = json.loads(ev["detail"])
        events.append(ev)
    chain = verify_chain(con)
    return {
        "packet_generated_at": _now(),
        "chain_status": chain,
        "expense": dict(e),
        "checks": checks,
        "decision": dict(d) if d else None,
        "human_review": dict(review) if review else None,
        "audit_events": events,
    }
