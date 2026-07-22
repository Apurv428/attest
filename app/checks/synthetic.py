"""Synthetic expense generator.

Every record produced here is fabricated. No real employee, vendor, or
transaction appears in this project, and the demo says so out loud.

The generator plants specific, known patterns so the checks have
something true to find. `PLANTED` documents exactly what was seeded,
which is what makes the eval in tests/test_eval.py meaningful rather than
self-congratulatory.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from ..models import Expense

SEED = 428

EMPLOYEES = ["R. Iyer", "M. Fernandes", "A. Kulkarni", "S. Nair", "D. Bose", "P. Menon"]

VENDORS = {
    "meals": ["Blue Tokai", "Rasa Kitchen", "Kopi Lane", "Sattvik", "The Table"],
    "travel": ["IndiGo", "Meru Cabs", "Rapido", "Konkan Rail", "Air India"],
    "lodging": ["Ibis Whitefield", "Treebo Central", "Lemon Tree", "Ginger HSR"],
    "software": ["Figma", "Linear", "Vercel", "Datadog", "Notion Labs"],
    "supplies": ["Office Depot IN", "Stationers Co", "PaperTrail"],
    "entertainment": ["Prithvi Cafe", "Cinepolis", "The Comedy Room"],
}

START = date(2026, 5, 4)

# What we deliberately seeded, for the eval to score against.
PLANTED: dict[str, list[str]] = {
    "duplicate": [],
    "receipt_reuse": [],
    "policy_ceiling": [],
    "threshold_hugging": [],
    "round_amount": [],
    "unknown_vendor": [],
}


def _rid(rng) -> str:
    return "RC-" + "".join(rng.choice("0123456789ABCDEF") for _ in range(6))


def generate(n_clean: int = 34) -> list[Expense]:
    """Return a fabricated batch: mostly ordinary claims, plus planted cases."""
    rng = random.Random(SEED)
    for k in PLANTED:
        PLANTED[k] = []

    out: list[Expense] = []
    counter = 1000

    def nxt() -> str:
        nonlocal counter
        counter += 1
        return f"E-{counter}"

    def add(employee, vendor, category, amount, day_offset, receipt=None, desc=""):
        d = START + timedelta(days=day_offset)
        e = Expense(
            id=nxt(), employee=employee, vendor=vendor, category=category,
            amount=round(amount, 2), date=d.isoformat(),
            submitted_at=(d + timedelta(days=rng.randint(0, 2))).isoformat() + "T10:00:00",
            description=desc, receipt_id=receipt or _rid(rng),
        )
        out.append(e)
        return e

    # --- ordinary traffic -------------------------------------------------
    for _ in range(n_clean):
        cat = rng.choice(list(VENDORS))
        vendor = rng.choice(VENDORS[cat])
        emp = rng.choice(EMPLOYEES)
        base = {"meals": 45, "travel": 380, "lodging": 210, "software": 120,
                "supplies": 60, "entertainment": 90}[cat]
        amt = base * rng.uniform(0.4, 1.5) + rng.uniform(0.01, 0.99)
        day = rng.randint(0, 27)
        # keep ordinary traffic off weekends for the weekend-sensitive categories
        d = START + timedelta(days=day)
        if cat in {"meals", "entertainment", "supplies"} and d.weekday() >= 5:
            day += 2
        add(emp, vendor, cat, amt, day, desc="Routine claim")

    # --- planted: duplicate pair -----------------------------------------
    a = add("D. Bose", "Meru Cabs", "travel", 412.60, 9, desc="Airport transfer")
    b = add("D. Bose", "Meru Cabs", "travel", 412.60, 10, desc="Airport transfer")
    PLANTED["duplicate"].append(b.id)

    # --- planted: reused receipt across different amounts ----------------
    shared = _rid(rng)
    c = add("A. Kulkarni", "Ibis Whitefield", "lodging", 288.40, 12, receipt=shared)
    d2 = add("A. Kulkarni", "Lemon Tree", "lodging", 317.15, 18, receipt=shared)
    PLANTED["receipt_reuse"].append(d2.id)

    # --- planted: over policy ceiling ------------------------------------
    e1 = add("S. Nair", "The Table", "meals", 268.75, 14, desc="Client dinner")
    PLANTED["policy_ceiling"].append(e1.id)

    # --- planted: threshold hugging (repeat offender) --------------------
    # Deliberately just under the $120 meals ceiling, never over it: the
    # point of this pattern is that each claim is individually compliant.
    for off, amt in ((5, 112.40), (13, 115.80), (21, 118.30)):
        h = add("M. Fernandes", rng.choice(VENDORS["meals"]), "meals", amt, off + 1)
        PLANTED["threshold_hugging"].append(h.id)

    # --- planted: round numbers from one employee ------------------------
    for off, amt in ((7, 250.00), (16, 400.00), (24, 300.00)):
        r = add("R. Iyer", rng.choice(VENDORS["travel"]), "travel", amt, off)
        PLANTED["round_amount"].append(r.id)

    # --- planted: invented vendor, large claim ---------------------------
    v = add("P. Menon", "Northgate Consulting LLP", "software", 740.00, 20,
            desc="Annual license")
    PLANTED["unknown_vendor"].append(v.id)
    PLANTED["round_amount"].append(v.id)  # 740.00 is also round

    return out


def summary() -> dict:
    return {k: list(v) for k, v in PLANTED.items()}
