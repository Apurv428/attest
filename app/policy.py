"""Policy: how check results become a decision.

Deliberately separate from the checks themselves. Checks gather evidence;
this file decides what to do about it, and every threshold here is a
number a finance team can see, argue with, and change.

The design choice worth defending: the agent can auto-approve, but it can
never auto-reject. Rejection is a human-only action. An AI employee that
can quietly deny a colleague's legitimate expense creates a trust problem
no accuracy number solves.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import Decision, noisy_or

REVIEW_THRESHOLD = 0.35

# Checks that always route to a human, regardless of combined score.
ALWAYS_REVIEW = {"duplicate", "receipt_reuse"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decide(expense, results) -> Decision:
    triggered = [r for r in results if r.triggered]
    risk = noisy_or([r.severity for r in triggered])
    names = [r.check for r in triggered]

    forced = sorted(set(names) & ALWAYS_REVIEW)
    route = "review" if (forced or risk >= REVIEW_THRESHOLD) else "auto_approved"

    rationale = _explain(expense, triggered, risk, route, forced)
    return Decision(
        expense_id=expense.id,
        route=route,
        risk=risk,
        triggered_checks=names,
        rationale=rationale,
        decided_at=_now(),
    )


def _explain(expense, triggered, risk, route, forced) -> str:
    """Write the decision the way a person would explain it.

    An auditor should be able to read this and reconstruct the reasoning
    without opening the JSON.
    """
    if not triggered:
        return (f"Cleared {expense.id}: all seven checks passed. "
                f"${expense.amount:,.2f} to {expense.vendor} for {expense.category} "
                f"sits within policy, the receipt is unique, and the vendor is on file. "
                f"Auto-approved at risk {risk:.2f}.")

    parts = "; ".join(f"{r.label.lower()} ({r.summary.rstrip('.')})" for r in triggered)

    if route == "review":
        if forced:
            reason = (f"{', '.join(f.replace('_', ' ') for f in forced)} always requires a "
                      f"human decision, regardless of score")
        else:
            reason = f"combined risk {risk:.2f} is at or above the {REVIEW_THRESHOLD:.2f} review threshold"
        return (f"Routed {expense.id} to human review because {reason}. "
                f"Signals: {parts}. "
                f"I have not approved or rejected this claim — a reviewer decides.")

    return (f"Auto-approved {expense.id} at risk {risk:.2f}, below the "
            f"{REVIEW_THRESHOLD:.2f} review threshold. "
            f"Minor signals noted but not sufficient to involve a person: {parts}.")
