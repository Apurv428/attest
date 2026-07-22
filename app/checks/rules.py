"""The seven checks the digital employee runs on every expense.

Each check is a pure function: (expense, history) -> CheckResult. No check
knows about the others, and none of them decides anything. They report
evidence; policy.py decides what to do with it. That separation is what
makes the decision auditable — you can point at exactly which signal
produced which part of the risk score.

Severities are deliberately conservative. A single check rarely exceeds
0.5, because no single signal should be able to route an expense to a
human on its own except outright duplication.
"""
from __future__ import annotations

from datetime import datetime, date
import re

from ..models import CheckResult

# Category ceilings in USD. Policy, not law — these are the numbers a
# finance team would set and revise, so they live in one visible place.
CATEGORY_CEILINGS = {
    "meals": 120.0,
    "travel": 1200.0,
    "lodging": 450.0,
    "software": 800.0,
    "supplies": 300.0,
    "entertainment": 400.0,
}

ROUND_NUMBER_MIN = 200.0


def _d(s: str) -> date:
    return datetime.fromisoformat(s).date()


# --- 1. duplicate --------------------------------------------------------

def check_duplicate(e, history) -> CheckResult:
    """Same employee, same vendor, same amount, within 3 days.

    The strongest single signal in expense fraud, and the one most often
    innocent (genuinely repeated purchases), which is why it routes to a
    human rather than auto-rejecting.
    """
    matches = []
    for h in history:
        if h.id == e.id or h.employee != e.employee:
            continue
        if h.vendor == e.vendor and abs(h.amount - e.amount) < 0.01:
            if abs((_d(h.date) - _d(e.date)).days) <= 3:
                matches.append(h.id)
    if matches:
        return CheckResult(
            check="duplicate",
            label="Duplicate claim",
            triggered=True,
            severity=0.55,
            summary=f"Same vendor and amount as {', '.join(matches)} within 3 days.",
            evidence={"matching_expense_ids": matches, "amount": e.amount, "vendor": e.vendor},
        )
    return CheckResult("duplicate", "Duplicate claim", False, 0.0,
                       "No matching claim from this employee in the surrounding 3 days.", {})


# --- 2. receipt reuse ----------------------------------------------------

def check_receipt_reuse(e, history) -> CheckResult:
    """The same receipt fingerprint attached to more than one claim.

    Distinct from `duplicate`: amounts and dates can differ, which is what
    makes it worse. A reused receipt is rarely a filing mistake.
    """
    if not e.receipt_id:
        return CheckResult("receipt_reuse", "Receipt reused", False, 0.0,
                           "No receipt attached to compare.", {})
    others = [h.id for h in history if h.id != e.id and h.receipt_id == e.receipt_id]
    if others:
        return CheckResult(
            check="receipt_reuse",
            label="Receipt reused",
            triggered=True,
            severity=0.6,
            summary=f"Receipt {e.receipt_id} is already attached to {', '.join(others)}.",
            evidence={"receipt_id": e.receipt_id, "also_on": others},
        )
    return CheckResult("receipt_reuse", "Receipt reused", False, 0.0,
                       f"Receipt {e.receipt_id} appears on this claim only.", {})


# --- 3. policy ceiling ---------------------------------------------------

def check_policy_ceiling(e, history) -> CheckResult:
    ceiling = CATEGORY_CEILINGS.get(e.category)
    if ceiling is None:
        return CheckResult("policy_ceiling", "Over policy limit", False, 0.0,
                           f"No ceiling defined for category '{e.category}'.", {})
    if e.amount > ceiling:
        over = (e.amount - ceiling) / ceiling
        severity = min(0.5, 0.15 + over * 0.5)
        return CheckResult(
            check="policy_ceiling",
            label="Over policy limit",
            triggered=True,
            severity=round(severity, 3),
            summary=f"${e.amount:,.2f} exceeds the ${ceiling:,.0f} {e.category} limit by {over:.0%}.",
            evidence={"amount": e.amount, "ceiling": ceiling, "over_by_pct": round(over * 100, 1)},
        )
    return CheckResult("policy_ceiling", "Over policy limit", False, 0.0,
                       f"${e.amount:,.2f} is within the ${ceiling:,.0f} {e.category} limit.", {})


# --- 4. threshold hugging ------------------------------------------------

def check_threshold_hugging(e, history) -> CheckResult:
    """Claims parked just under the approval ceiling.

    One claim at 97% of the limit is a coincidence. A pattern of them is
    someone who has read the policy and is optimising against it, which is
    why severity scales with how many the employee has filed.
    """
    ceiling = CATEGORY_CEILINGS.get(e.category)
    if ceiling is None or e.amount > ceiling:
        return CheckResult("threshold_hugging", "Just under the limit", False, 0.0,
                           "Not applicable.", {})
    ratio = e.amount / ceiling
    if ratio < 0.93:
        return CheckResult("threshold_hugging", "Just under the limit", False, 0.0,
                           f"At {ratio:.0%} of the {e.category} limit.", {})
    priors = [
        h.id for h in history
        if h.id != e.id and h.employee == e.employee
        and CATEGORY_CEILINGS.get(h.category)
        and 0.93 <= h.amount / CATEGORY_CEILINGS[h.category] <= 1.0
    ]
    severity = 0.2 + min(0.25, 0.08 * len(priors))
    return CheckResult(
        check="threshold_hugging",
        label="Just under the limit",
        triggered=True,
        severity=round(severity, 3),
        summary=(f"At {ratio:.0%} of the {e.category} limit; this employee has "
                 f"{len(priors)} other claim(s) in the same band."),
        evidence={"ratio_pct": round(ratio * 100, 1), "prior_similar": priors},
    )


# --- 5. weekend / holiday ------------------------------------------------

WEEKEND = {5, 6}


def check_out_of_hours(e, history) -> CheckResult:
    """Business expenses incurred on a weekend.

    Weak on its own — plenty of legitimate weekend travel — so severity is
    low. It earns its place by compounding with other signals.
    """
    d = _d(e.date)
    if d.weekday() in WEEKEND and e.category in {"meals", "entertainment", "supplies"}:
        return CheckResult(
            check="out_of_hours",
            label="Weekend expense",
            triggered=True,
            severity=0.15,
            summary=f"{e.category.title()} expense incurred on a {d.strftime('%A')}.",
            evidence={"weekday": d.strftime("%A"), "date": e.date},
        )
    return CheckResult("out_of_hours", "Weekend expense", False, 0.0,
                       "Incurred on a business day, or a category where weekends are normal.", {})


# --- 6. round-number amounts ---------------------------------------------

def check_round_amount(e, history) -> CheckResult:
    """Fabricated amounts cluster on round numbers.

    Real receipts have cents. A $250.00 taxi is possible; a pattern of
    them is a tell.
    """
    if e.amount >= ROUND_NUMBER_MIN and abs(e.amount - round(e.amount, -1)) < 0.005:
        priors = [
            h.id for h in history
            if h.id != e.id and h.employee == e.employee
            and h.amount >= ROUND_NUMBER_MIN
            and abs(h.amount - round(h.amount, -1)) < 0.005
        ]
        severity = 0.12 + min(0.2, 0.06 * len(priors))
        return CheckResult(
            check="round_amount",
            label="Suspiciously round amount",
            triggered=True,
            severity=round(severity, 3),
            summary=(f"${e.amount:,.2f} is an exact round figure; this employee has "
                     f"{len(priors)} other round-number claim(s)."),
            evidence={"amount": e.amount, "prior_round": priors},
        )
    return CheckResult("round_amount", "Suspiciously round amount", False, 0.0,
                       "Amount carries cents, consistent with a real receipt.", {})


# --- 7. new vendor -------------------------------------------------------

VENDOR_TOKEN = re.compile(r"[^a-z0-9]+")


def _norm(v: str) -> str:
    return VENDOR_TOKEN.sub("", v.lower())


def check_unknown_vendor(e, history) -> CheckResult:
    """A vendor nobody in the company has ever paid, on a large claim.

    Small purchases from new vendors are ordinary. Large ones are how
    invented vendors enter the ledger.
    """
    known = {_norm(h.vendor) for h in history if h.id != e.id}
    if _norm(e.vendor) in known:
        return CheckResult("unknown_vendor", "First-time vendor", False, 0.0,
                           f"{e.vendor} has been paid before.", {})
    if e.amount < 150:
        return CheckResult("unknown_vendor", "First-time vendor", False, 0.0,
                           f"{e.vendor} is new, but the amount is small.", {})
    severity = 0.2 if e.amount < 500 else 0.35
    return CheckResult(
        check="unknown_vendor",
        label="First-time vendor",
        triggered=True,
        severity=severity,
        summary=f"{e.vendor} has never been paid by this company, on a ${e.amount:,.2f} claim.",
        evidence={"vendor": e.vendor, "amount": e.amount, "known_vendor_count": len(known)},
    )


ALL_CHECKS = [
    check_duplicate,
    check_receipt_reuse,
    check_policy_ceiling,
    check_threshold_hugging,
    check_out_of_hours,
    check_round_amount,
    check_unknown_vendor,
]


def run_all(e, history) -> list[CheckResult]:
    return [fn(e, history) for fn in ALL_CHECKS]
