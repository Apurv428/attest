"""The digital employee.

A deterministic four-stage loop per expense:

    intake -> run checks -> decide -> route

Every stage appends to the hash-chained audit log before moving on, so a
crash mid-run leaves a truthful partial trail rather than a silent gap.

There is no LLM in the decision path, and that is a product decision, not
a limitation. The reasoning a finance team has to defend to an auditor
should be reproducible: same inputs, same output, every time. The LLM
belongs at the edges — summarising a queue, drafting the note to an
employee — never in the step that decides whether money moves.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import audit, store
from ..checks import rules
from ..models import Expense
from ..policy import decide


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_expense(r) -> Expense:
    return Expense(
        id=r["id"], employee=r["employee"], vendor=r["vendor"],
        category=r["category"], amount=r["amount"], date=r["date"],
        submitted_at=r["submitted_at"], description=r["description"],
        receipt_id=r["receipt_id"],
    )


def process_one(con, expense: Expense, history: list[Expense]) -> dict:
    """Run the full pipeline for a single expense. Returns a summary dict."""
    audit.append(
        con, actor=audit.AGENT, action="intake", expense_id=expense.id,
        summary=f"Received {expense.id}: ${expense.amount:,.2f} to {expense.vendor} "
                f"({expense.category}) from {expense.employee}.",
        detail=expense.to_dict(),
    )

    results = rules.run_all(expense, history)
    for r in results:
        store.insert_check(con, expense.id, r)

    fired = [r for r in results if r.triggered]
    audit.append(
        con, actor=audit.AGENT, action="checks_run", expense_id=expense.id,
        summary=(f"Ran {len(results)} checks on {expense.id}: "
                 f"{len(fired)} triggered ({', '.join(r.label.lower() for r in fired) or 'none'})."),
        detail={"results": [r.to_dict() for r in results]},
    )

    d = decide(expense, results)
    store.insert_decision(con, d)
    audit.append(
        con, actor=audit.AGENT, action="decision", expense_id=expense.id,
        summary=d.rationale,
        detail={"route": d.route, "risk": d.risk, "triggered": d.triggered_checks,
                "threshold": 0.35},
    )

    if d.route == "review":
        audit.append(
            con, actor=audit.AGENT, action="escalated", expense_id=expense.id,
            summary=f"Placed {expense.id} in the review queue and paused. Awaiting a human decision.",
            detail={"risk": d.risk},
        )
    return {"expense_id": expense.id, "route": d.route, "risk": d.risk,
            "triggered": d.triggered_checks}


def run_batch(con, expenses: list[Expense]) -> list[dict]:
    """Process a batch in submission order, each seeing only prior claims.

    History is strictly backward-looking: expense N is checked against
    1..N-1 only. Without this, a duplicate pair would flag both claims and
    the log would imply the agent saw the future. The no_lookahead test
    pins this behaviour.
    """
    audit.append(con, actor=audit.AGENT, action="batch_start",
                 summary=f"Starting run over {len(expenses)} submitted expenses.",
                 detail={"count": len(expenses)})

    ordered = sorted(expenses, key=lambda e: (e.submitted_at, e.id))
    out = []
    for i, e in enumerate(ordered):
        store.insert_expense(con, e)
        out.append(process_one(con, e, ordered[:i]))

    flagged = sum(1 for o in out if o["route"] == "review")
    audit.append(
        con, actor=audit.AGENT, action="batch_end",
        summary=(f"Finished. {len(out)} processed, {len(out) - flagged} auto-approved, "
                 f"{flagged} sent to a human."),
        detail={"processed": len(out), "auto_approved": len(out) - flagged, "review": flagged},
    )
    return out


def record_human_review(con, expense_id: str, action: str, reviewer: str, note: str = "") -> dict:
    """Record a human's approve/reject. The only path to a rejection."""
    if action not in {"approved", "rejected"}:
        raise ValueError("action must be 'approved' or 'rejected'")
    ts = _now()
    store.insert_review(con, expense_id, action, reviewer, note, ts)
    audit.append(
        con, actor=reviewer, action=f"human_{action}", expense_id=expense_id,
        summary=(f"{reviewer} {action} {expense_id}"
                 + (f": {note}" if note else ".")),
        detail={"action": action, "note": note}, ts=ts,
    )
    return {"expense_id": expense_id, "action": action, "reviewed_at": ts}
