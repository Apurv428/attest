"""FastAPI backend for Attest."""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit, store
from .agent import loop
from .checks import synthetic

HERE = os.path.dirname(__file__)
STATIC = os.path.join(HERE, "static")

app = FastAPI(title="Attest", version="1.0")


class ReviewIn(BaseModel):
    action: str
    reviewer: str = "finance.reviewer"
    note: str = ""


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.post("/api/run")
def run():
    """Reset to a fresh fabricated batch and run the digital employee over it."""
    store.reset()
    expenses = synthetic.generate()
    with store.db() as con:
        results = loop.run_batch(con, expenses)
        chain = audit.verify_chain(con)
    flagged = [r for r in results if r["route"] == "review"]
    return {
        "processed": len(results),
        "auto_approved": len(results) - len(flagged),
        "review": len(flagged),
        "chain": chain,
    }


@app.get("/api/queue")
def queue():
    """Everything the agent processed, review items first."""
    with store.db() as con:
        rows = store.all_expenses(con)
        items = []
        for r in rows:
            d = store.decision_for(con, r["id"])
            if not d:
                continue
            rev = store.review_for(con, r["id"])
            items.append({
                "id": r["id"], "employee": r["employee"], "vendor": r["vendor"],
                "category": r["category"], "amount": r["amount"], "date": r["date"],
                "route": d["route"], "risk": d["risk"],
                "triggered": json.loads(d["triggered_checks"]),
                "rationale": d["rationale"],
                "review": dict(rev) if rev else None,
            })
        chain = audit.verify_chain(con)
    items.sort(key=lambda i: (i["route"] != "review", -i["risk"], i["id"]))
    pending = sum(1 for i in items if i["route"] == "review" and not i["review"])
    return {"items": items, "pending": pending, "chain": chain}


@app.get("/api/expense/{expense_id}")
def expense_detail(expense_id: str):
    with store.db() as con:
        pkt = audit.packet(con, expense_id)
    if not pkt:
        raise HTTPException(404, f"No expense {expense_id}")
    return pkt


@app.post("/api/expense/{expense_id}/review")
def review(expense_id: str, body: ReviewIn):
    with store.db() as con:
        if not store.expense(con, expense_id):
            raise HTTPException(404, f"No expense {expense_id}")
        try:
            out = loop.record_human_review(con, expense_id, body.action, body.reviewer, body.note)
        except ValueError as ex:
            raise HTTPException(400, str(ex))
    return out


@app.get("/api/audit")
def audit_log(expense_id: str | None = None, limit: int = 400):
    with store.db() as con:
        rows = store.audit_rows(con, expense_id, limit)
        events = []
        for r in rows:
            e = dict(r)
            e["detail"] = json.loads(e["detail"])
            events.append(e)
        chain = audit.verify_chain(con)
    return {"events": events, "chain": chain}


@app.get("/api/verify")
def verify():
    with store.db() as con:
        return audit.verify_chain(con)


@app.post("/api/tamper")
def tamper():
    """Demo only: silently edit one settled decision, the way a bad actor would.

    Nothing else changes. The row still looks correct on its own. Only the
    chain notices, which is the entire point.
    """
    with store.db() as con:
        row = con.execute(
            "SELECT expense_id FROM decisions WHERE route='review' ORDER BY risk DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise HTTPException(400, "Run the agent first.")
        eid = row["expense_id"]
        con.execute("UPDATE decisions SET route='auto_approved', risk=0.0, "
                    "triggered_checks='[]', "
                    "rationale='Cleared: no issues found.' WHERE expense_id=?", (eid,))
        con.execute("UPDATE check_results SET triggered=0, severity=0.0, "
                    "summary='No issue found.' WHERE expense_id=? AND triggered=1", (eid,))
        con.execute("UPDATE audit_log SET summary='Cleared: no issues found.' "
                    "WHERE expense_id=? AND action='decision'", (eid,))
        chain = audit.verify_chain(con)
    return {"tampered_expense": eid, "chain": chain}


@app.get("/api/packet/{expense_id}")
def packet_download(expense_id: str):
    with store.db() as con:
        pkt = audit.packet(con, expense_id)
    if not pkt:
        raise HTTPException(404, f"No expense {expense_id}")
    return JSONResponse(
        pkt,
        headers={"Content-Disposition": f'attachment; filename="attest-packet-{expense_id}.json"'},
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")
