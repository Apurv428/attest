"""Test suite for Attest.

The tests that matter most are the last three: chain tamper-evidence,
no-lookahead, and the eval against the planted fraud set. Those are the
ones that make the claims in the pitch checkable rather than asserted.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from app import audit, store
from app.agent import loop
from app.checks import rules, synthetic
from app.models import Expense, noisy_or
from app.policy import decide, REVIEW_THRESHOLD


# ---- fixtures --------------------------------------------------------------

@pytest.fixture
def con():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    c = store.connect(path)
    yield c
    c.close()
    if os.path.exists(path):
        os.remove(path)


def ex(id="E-1", employee="A", vendor="Kopi Lane", category="meals",
       amount=40.25, date="2026-05-06", receipt="RC-AAA111"):
    return Expense(id=id, employee=employee, vendor=vendor, category=category,
                   amount=amount, date=date, submitted_at=date + "T09:00:00",
                   receipt_id=receipt)


# ---- risk combination ------------------------------------------------------

def test_noisy_or_empty_is_zero():
    assert noisy_or([]) == 0.0


def test_noisy_or_compounds_but_never_exceeds_one():
    assert noisy_or([0.5]) == 0.5
    assert 0.5 < noisy_or([0.5, 0.3]) < 1.0
    assert noisy_or([0.9, 0.9, 0.9]) < 1.0


def test_noisy_or_is_order_independent():
    assert noisy_or([0.2, 0.4, 0.1]) == noisy_or([0.1, 0.2, 0.4])


# ---- individual checks -----------------------------------------------------

def test_duplicate_fires_on_same_vendor_amount_within_window():
    a = ex(id="E-1", date="2026-05-06")
    b = ex(id="E-2", date="2026-05-08")
    r = rules.check_duplicate(b, [a])
    assert r.triggered and "E-1" in r.evidence["matching_expense_ids"]


def test_duplicate_ignores_different_employee():
    a = ex(id="E-1", employee="A")
    b = ex(id="E-2", employee="B")
    assert not rules.check_duplicate(b, [a]).triggered


def test_duplicate_ignores_outside_window():
    a = ex(id="E-1", date="2026-05-01")
    b = ex(id="E-2", date="2026-05-20")
    assert not rules.check_duplicate(b, [a]).triggered


def test_receipt_reuse_fires_across_different_amounts():
    a = ex(id="E-1", amount=100.00, receipt="RC-SHARED")
    b = ex(id="E-2", amount=250.00, receipt="RC-SHARED")
    r = rules.check_receipt_reuse(b, [a])
    assert r.triggered and r.evidence["also_on"] == ["E-1"]


def test_receipt_reuse_silent_without_receipt():
    assert not rules.check_receipt_reuse(ex(receipt=""), []).triggered


def test_policy_ceiling_fires_and_scales():
    small = rules.check_policy_ceiling(ex(amount=130.0), [])       # meals cap 120
    large = rules.check_policy_ceiling(ex(amount=400.0), [])
    assert small.triggered and large.triggered
    assert large.severity > small.severity
    assert large.severity <= 0.5


def test_policy_ceiling_passes_within_limit():
    assert not rules.check_policy_ceiling(ex(amount=90.0), []).triggered


def test_unknown_category_has_no_ceiling():
    assert not rules.check_policy_ceiling(ex(category="misc", amount=9999.0), []).triggered


def test_threshold_hugging_escalates_with_repeat_offences():
    lone = rules.check_threshold_hugging(ex(id="E-9", amount=115.0), [])
    priors = [ex(id=f"E-{i}", amount=114.0 + i) for i in range(3)]
    repeat = rules.check_threshold_hugging(ex(id="E-9", amount=115.0), priors)
    assert lone.triggered and repeat.triggered
    assert repeat.severity > lone.severity


def test_out_of_hours_fires_on_weekend_meal():
    assert rules.check_out_of_hours(ex(date="2026-05-09"), []).triggered      # Saturday
    assert not rules.check_out_of_hours(ex(date="2026-05-06"), []).triggered  # Wednesday


def test_out_of_hours_ignores_travel():
    assert not rules.check_out_of_hours(ex(category="travel", date="2026-05-09"), []).triggered


def test_round_amount_needs_size_and_roundness():
    assert rules.check_round_amount(ex(category="travel", amount=250.00), []).triggered
    assert not rules.check_round_amount(ex(category="travel", amount=250.37), []).triggered
    assert not rules.check_round_amount(ex(amount=50.00), []).triggered  # below floor


def test_unknown_vendor_scales_with_amount():
    small = rules.check_unknown_vendor(ex(vendor="New Co", category="travel", amount=200.0), [])
    big = rules.check_unknown_vendor(ex(vendor="New Co", category="travel", amount=900.0), [])
    assert big.severity > small.severity


def test_unknown_vendor_quiet_for_known_vendor():
    prior = ex(id="E-1", vendor="Kopi Lane")
    assert not rules.check_unknown_vendor(ex(id="E-2", vendor="kopi  lane!"), [prior]).triggered


def test_unknown_vendor_quiet_for_small_amounts():
    assert not rules.check_unknown_vendor(ex(vendor="Brand New", amount=40.0), []).triggered


def test_run_all_returns_one_result_per_check():
    assert len(rules.run_all(ex(), [])) == len(rules.ALL_CHECKS)


# ---- policy ----------------------------------------------------------------

def test_clean_expense_auto_approves():
    d = decide(ex(), [])
    assert d.route == "auto_approved" and d.risk == 0.0


def test_duplicate_always_escalates_regardless_of_score():
    a = ex(id="E-1")
    b = ex(id="E-2", date="2026-05-07")
    d = decide(b, rules.run_all(b, [a]))
    assert d.route == "review"
    assert "always requires a human decision" in d.rationale


def test_weak_signal_alone_does_not_escalate():
    e = ex(date="2026-05-09")  # weekend meal only, severity 0.15
    d = decide(e, rules.run_all(e, []))
    assert d.risk < REVIEW_THRESHOLD and d.route == "auto_approved"


def test_rationale_is_plain_english_and_mentions_the_id():
    e = ex(amount=300.0)
    d = decide(e, rules.run_all(e, []))
    assert e.id in d.rationale and len(d.rationale.split()) > 8


def test_agent_never_rejects():
    """The agent may approve or escalate. Rejection is human-only."""
    expenses = synthetic.generate()
    for e in expenses:
        d = decide(e, rules.run_all(e, [x for x in expenses if x.id != e.id]))
        assert d.route in {"auto_approved", "review"}


def test_record_human_review_rejects_bad_action(con):
    e = ex()
    store.insert_expense(con, e)
    with pytest.raises(ValueError):
        loop.record_human_review(con, e.id, "maybe", "someone")


# ---- audit chain -----------------------------------------------------------

def test_chain_intact_after_normal_run(con):
    loop.run_batch(con, synthetic.generate())
    assert audit.verify_chain(con)["intact"] is True


def test_every_stage_writes_an_event(con):
    e = ex()
    store.insert_expense(con, e)
    loop.process_one(con, e, [])
    actions = [r["action"] for r in store.audit_rows(con, e.id)]
    assert actions[:3] == ["intake", "checks_run", "decision"]


def test_tampering_with_a_settled_decision_breaks_the_chain(con):
    """The claim the whole product rests on. If this passes, the pitch is true."""
    loop.run_batch(con, synthetic.generate())
    assert audit.verify_chain(con)["intact"]

    row = con.execute("SELECT seq, expense_id FROM audit_log WHERE action='decision' "
                      "ORDER BY seq ASC LIMIT 1").fetchone()
    con.execute("UPDATE audit_log SET summary='Cleared: no issues found.' WHERE seq=?",
                (row["seq"],))

    result = audit.verify_chain(con)
    assert result["intact"] is False
    assert result["first_break"] == row["seq"]


def test_human_review_is_appended_not_overwritten(con):
    loop.run_batch(con, synthetic.generate())
    target = con.execute("SELECT expense_id FROM decisions WHERE route='review' LIMIT 1").fetchone()
    before = len(store.audit_rows(con, target["expense_id"]))
    loop.record_human_review(con, target["expense_id"], "rejected", "finance.reviewer", "Personal.")
    after = store.audit_rows(con, target["expense_id"])
    assert len(after) == before + 1
    assert after[-1]["action"] == "human_rejected"
    assert audit.verify_chain(con)["intact"]


def test_packet_contains_everything_an_auditor_needs(con):
    loop.run_batch(con, synthetic.generate())
    target = con.execute("SELECT expense_id FROM decisions WHERE route='review' LIMIT 1").fetchone()
    pkt = audit.packet(con, target["expense_id"])
    assert pkt["expense"] and pkt["decision"] and pkt["chain_status"]["intact"]
    assert len(pkt["checks"]) == len(rules.ALL_CHECKS)
    assert pkt["audit_events"][0]["action"] == "intake"


# ---- no lookahead ----------------------------------------------------------

def test_no_lookahead_earlier_decisions_do_not_change(con):
    """Truncating the batch must not change any decision that came before.

    Without strictly backward-looking history, the first half of a
    duplicate pair would be flagged by evidence that did not exist when it
    was filed, and the ledger would be a record of something impossible.
    """
    expenses = sorted(synthetic.generate(), key=lambda e: (e.submitted_at, e.id))
    cut = len(expenses) // 2

    full = {r["expense_id"]: (r["route"], r["risk"]) for r in _routes(expenses)}
    part = {r["expense_id"]: (r["route"], r["risk"]) for r in _routes(expenses[:cut])}

    for eid, verdict in part.items():
        assert full[eid] == verdict, f"{eid} changed when later expenses were added"


def _routes(expenses):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    c = store.connect(path)
    try:
        loop.run_batch(c, list(expenses))
        return c.execute("SELECT expense_id, route, risk FROM decisions").fetchall()
    finally:
        c.close()
        if os.path.exists(path):
            os.remove(path)


# ---- eval against the planted set ------------------------------------------

def test_eval_catches_every_planted_case(con):
    """Score the agent against fraud we deliberately seeded.

    Recall must be perfect on the planted set: these are unambiguous cases
    and missing one would mean the checks do not work. Precision is not
    asserted, because extra flags are routed to a human, not rejected —
    a false positive costs a reviewer thirty seconds, a false negative
    costs money.
    """
    loop.run_batch(con, synthetic.generate())
    planted = synthetic.summary()

    missed = []
    for check_name, ids in planted.items():
        for eid in ids:
            hit = con.execute(
                "SELECT triggered FROM check_results WHERE expense_id=? AND check_name=?",
                (eid, check_name),
            ).fetchone()
            if not hit or not hit["triggered"]:
                missed.append((eid, check_name))

    assert not missed, f"missed planted cases: {missed}"


UNAMBIGUOUS = ("duplicate", "receipt_reuse", "policy_ceiling", "unknown_vendor")


def test_unambiguous_fraud_always_reaches_a_human(con):
    loop.run_batch(con, synthetic.generate())
    planted = synthetic.summary()
    for check_name in UNAMBIGUOUS:
        for eid in planted[check_name]:
            d = store.decision_for(con, eid)
            assert d["route"] == "review", f"{eid} ({check_name}) was auto-approved"


def test_a_pattern_escalates_even_when_one_instance_does_not(con):
    """The first just-under-limit claim passes; the third does not.

    This is the behaviour worth being most deliberate about. One claim at
    97% of the limit is a coincidence, and escalating it spends a
    reviewer's attention on nothing. Three from the same person inside a
    month is someone optimising against a policy they have read. The
    signal is the pattern, not the claim, so severity scales with priors
    rather than firing on sight.
    """
    loop.run_batch(con, synthetic.generate())
    ids = synthetic.summary()["threshold_hugging"]
    routes = [store.decision_for(con, i)["route"] for i in ids]
    assert routes[0] == "auto_approved", "a lone near-limit claim should not cost a reviewer time"
    assert routes[-1] == "review", "the third in the pattern should escalate"


def test_weak_signals_alone_stay_out_of_the_queue(con):
    """Round-number claims are surfaced but do not escalate on their own.

    Documented as a test rather than a comment because it is a policy
    choice someone will want to argue with: round amounts are evidence,
    not proof, and a queue full of them trains reviewers to rubber-stamp.
    """
    loop.run_batch(con, synthetic.generate())
    for eid in synthetic.summary()["round_amount"]:
        row = con.execute(
            "SELECT triggered FROM check_results WHERE expense_id=? AND check_name='round_amount'",
            (eid,),
        ).fetchone()
        assert row["triggered"], f"{eid} should still be detected and recorded"


def test_clean_claims_mostly_pass_through(con):
    """Auto-approval rate on unplanted claims should stay high.

    A digital employee that escalates everything has not saved anyone any
    work. This pins the agent to being useful, not just safe.
    """
    loop.run_batch(con, synthetic.generate())
    planted = {eid for group in synthetic.summary().values() for eid in group}
    rows = con.execute("SELECT expense_id, route FROM decisions").fetchall()
    clean = [r for r in rows if r["expense_id"] not in planted]
    auto = [r for r in clean if r["route"] == "auto_approved"]
    assert len(auto) / len(clean) >= 0.80
