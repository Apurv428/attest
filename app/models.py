"""Core data models for Attest.

An Expense flows through the digital employee's pipeline and accumulates
CheckResults, a Decision, and a fully auditable trail of AuditEvents.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class Expense:
    id: str                     # e.g. "E-1042"
    employee: str
    vendor: str
    category: str
    amount: float               # USD
    date: str                   # ISO date the expense was incurred
    submitted_at: str           # ISO datetime it was submitted
    description: str = ""
    receipt_id: str = ""        # simulated receipt fingerprint

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    check: str                  # machine name, e.g. "duplicate"
    label: str                  # human name, e.g. "Duplicate claim"
    triggered: bool
    severity: float             # 0.0 - 1.0 contribution to risk
    summary: str                # one plain-English sentence
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    expense_id: str
    route: str                  # "auto_approved" | "review"
    risk: float                 # combined 0.0 - 1.0
    triggered_checks: list[str]
    rationale: str              # plain-English paragraph
    decided_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def noisy_or(severities: list[float]) -> float:
    """Combine independent risk signals: 1 - prod(1 - s).

    Chosen over max() or sum() deliberately: several weak signals should
    compound into meaningful risk, but no single weak signal should
    dominate. Documented here because the combination rule is itself an
    auditable policy choice.
    """
    p = 1.0
    for s in severities:
        s = max(0.0, min(1.0, s))
        p *= (1.0 - s)
    return round(1.0 - p, 4)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
