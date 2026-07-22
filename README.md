# Attest

**A digital employee that reviews expense claims, and can prove every decision it made.**

An AI employee runs seven checks on every expense, approves what is clean, and hands
anything doubtful to a person. Every step it takes is written to an append-only,
hash-chained ledger. Alter a settled decision after the fact and the ledger says so,
naming the exact entry where the record stops being trustworthy.

All data in this project is fabricated. No real employee, vendor, or transaction appears
anywhere in it.

---

## The argument

AI employees in finance are no longer capability-limited. A model can read an invoice,
apply a policy, and make a call. That part is solved.

They are **trust-limited**. A CFO cannot deploy an agent that touches money unless she
can answer, months later and under audit: what did it decide, on what evidence, under
which policy, and has anyone changed the record since?

Most agent products answer the first question and none of the rest. That gap is why so
much of this category stalls at pilot. The wedge is not a better model, it is a
**provable one**: every action attributable, explainable in plain English, and
tamper-evident.

Attest is the smallest working demonstration of that idea.

---

## Run it

```bash
pip install -r requirements.txt
uvicorn app.server:app --reload
# open http://127.0.0.1:8000
```

```bash
pytest        # 36 tests
```

## The demo, in five clicks

1. **Run the digital employee.** 46 fabricated claims are processed. 39 clear
   automatically, 7 go to a person. The ledger seal reads INTACT.
2. **Open a flagged claim.** The agent explains itself in plain English: which of the
   seven checks fired, the evidence behind each, the combined risk, and the threshold it
   crossed. No score without a reason.
3. **Decide it.** Approve or reject. The decision appends to the ledger; it never
   overwrites anything.
4. **Tamper with a decision.** This silently rewrites a settled escalation to read
   *"Cleared: no issues found."* The row looks perfectly ordinary on its own.
5. **Verify the ledger.** BROKEN, first break at entry #N. The forged entry even
   contradicts the untouched entry directly beneath it.

Step 5 is the product. Steps 1–3 are table stakes.

---

## How it works

```
expense → intake → seven checks → policy → route ─┬─→ auto-approved
                                                  └─→ human review queue
   every stage appends to the hash-chained ledger ──────────────────────→
```

**Checks** (`app/checks/rules.py`) are pure functions returning evidence, never verdicts:
duplicate claims, reused receipts, policy ceilings, threshold hugging, weekend expenses,
round-number amounts, and first-time vendors.

**Policy** (`app/policy.py`) turns evidence into a decision. Every threshold is a visible
number a finance team can argue with.

**Ledger** (`app/audit.py`) hashes each entry together with the previous entry's hash.
`verify_chain()` re-walks the whole log from genesis and reports the first mismatch.

---

## Four decisions worth defending

**The agent can approve. Only a human can reject.** An AI that quietly denies a
colleague's legitimate expense creates a trust problem no accuracy number fixes. The
asymmetry is deliberate and enforced by a test.

**No LLM in the decision path.** The reasoning a finance team defends to an auditor must
be reproducible: same inputs, same output, every time. An LLM that re-words its rationale
between runs is unauditable. Models belong at the edges — summarising a queue, drafting a
note to an employee — never in the step where money moves.

**Risk combines by noisy-OR, not max or sum.** Several weak signals should compound into
something worth a look, but no single weak signal should be able to summon a human on its
own. `1 - Π(1 - severity)` does that; `max()` ignores corroboration and `sum()` panics.

**Patterns escalate, instances do not.** One claim at 97% of the limit is a coincidence,
and escalating it spends a reviewer's attention on nothing. Three from the same person in
a month is someone optimising against a policy they have read. Severity scales with
priors. `test_a_pattern_escalates_even_when_one_instance_does_not` pins it.

---

## Tests

36 tests. The four that matter:

| Test | What it proves |
|---|---|
| `test_tampering_with_a_settled_decision_breaks_the_chain` | The core claim. Edit history, the ledger notices. |
| `test_no_lookahead_earlier_decisions_do_not_change` | Truncating the batch changes no earlier verdict. Without it the ledger would record decisions made on evidence that did not yet exist. |
| `test_eval_catches_every_planted_case` | Scored against deliberately seeded fraud (`synthetic.PLANTED`), not vibes. Recall must be perfect. |
| `test_clean_claims_mostly_pass_through` | ≥80% of clean claims auto-approve. An agent that escalates everything has saved nobody any work. |

Precision is deliberately not asserted. A false positive costs a reviewer thirty seconds;
a false negative costs money.

---

## Where this goes

**Now:** one workflow, seven rules, a local ledger.

**Next:** the ledger becomes the primitive, not the feature. Any digital employee — expense
review, invoice matching, collections, reconciliation — writes to the same chain in the
same shape. The auditor's question stops being "can I trust this agent" and becomes "show
me the ledger," once, for the whole workforce.

**Then:** the ledger is the training signal. Every human override is a labelled correction
telling you exactly where the policy and the agent disagree. A digital employee that
cannot be audited also cannot be improved, because you have no ground truth. Trust
infrastructure and the improvement loop turn out to be the same thing, which is why it
should be built early rather than bolted on.

**The wedge:** whoever makes AI employees provable gets deployed *inside* regulated
enterprises instead of piloted next to them.

---

Built by Apurv Sonawane · [github.com/Apurv428](https://github.com/Apurv428)
