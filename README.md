# Mandate Retry Sequencer

**Razorpay AI Buildathon — Track 03, AI Revenue Recovery**

A bounded recovery workflow for failed UPI Autopay mandate debits. It scores each failure for
recoverability, decides an intervention, refuses anything the compliance rules forbid, executes
against a simulated payment rail over a 14-day campaign, and reports money recovered against money
at risk — with every rupee traceable to a row in an append-only ledger.

> **Status: Gate C.** The pipeline is live end to end. On the committed seed it recovers
> **₹44,25,090 of ₹1,26,32,606 at risk (35.0%)** across 500 records, with 187 stopped by a hard
> rule and every rupee traceable to a ledger row. 152 tests. See [SPEC.md](SPEC.md) §10.

---

## Run it

Requires [uv](https://docs.astral.sh/uv/) and Node 18+. **No API key is needed.**

```bash
uv sync --extra dev                    # creates .venv on Python 3.12

# Generate the data. Both sets are needed and neither is committed - they are
# reproducible byte-for-byte from these two commands (SPEC 2.1).
.venv/Scripts/python -m backend.scripts.generate_data --seed 42   --n 500  --name batch
.venv/Scripts/python -m backend.scripts.generate_data --seed 1042 --n 8000 --name corpus --split

.venv/Scripts/python -m uvicorn backend.app.main:app --port 8000
```

The trained scorer (`models/scorer.txt`) **is committed**, so you do not need to train to run the
demo. To retrain it from the corpus and see the metrics:

```bash
.venv/Scripts/python -m backend.scripts.train_scorer --prove-seal
```

`--prove-seal` demonstrates that the holdout cannot be read before the model is fit - the guard
raises rather than quietly returning a better-looking number.

In a second terminal:

```bash
cd frontend && npm install && npm run dev              # http://localhost:5173
```

Open http://localhost:5173 and press **Run batch**.

On macOS or Linux, use `.venv/bin/python` in place of `.venv/Scripts/python`.

### Tests

```bash
.venv/Scripts/python -m pytest
```

### Check the headline figure yourself

After pressing **Run batch**, re-derive the total straight from the ledger. This script
deliberately does not import `ledger.py` - if it shared the aggregation code, agreement would be a
tautology rather than a check (SPEC 8.2 gate 2):

```bash
.venv/Scripts/python -m backend.scripts.verify_totals
```

---

## Why there is no API key requirement

An LLM agent decides the ambiguous middle of the score distribution — the records where the model's
probability alone does not determine the right action. That would normally make results
irreproducible and lock the repo behind a key.

It does not here, because decisions resolve in three layers (SPEC §4.3):

1. **A committed response cache.** Every agent decision is stored in `cache/llm/`, keyed by a hash
   of the record. A clone with no key replays them and reproduces the numbers exactly.
2. **A deterministic fallback policy.** Pure, tested, no network. Covers anything uncached.
3. **An ablation.** `--no-llm` runs the whole pipeline without the agent, so the rupee difference
   between the two runs is the agent's *measured* contribution rather than a claim about it.

---

## How it works

```
500 failed mandate debits
        │
        ▼
  [ SCORER ]      LightGBM · P(recover | features)
        │
        ▼
  [ GUARDRAILS ]  hard rules, evaluated first and always
        │         revoked mandate · attempt cap · cooling period · horizon
        ▼
  [ DECIDER ]     agent, called only for the ambiguous band (0.15–0.65)
        │         proposes an action; the guardrails re-validate before execution
        ▼
  [ EXECUTOR ]    simulated rail, stepped over a 14-day clock
        │
        ▼
  [ LEDGER ]      append-only · input, score, rules fired, action, outcome, ₹
        │
        ▼
  [ DASHBOARD ]   ₹ recovered vs ₹ at risk · cohorts · attempts per recovery
                  · honest list of everything it failed to recover
```

A hard rule always beats the score, and always beats the agent. An agent proposal is a request, not
an authority — it is re-checked against rules 1–4 before anything executes.

Full design in [ARCHITECTURE.md](ARCHITECTURE.md). The contract this is built against is
[SPEC.md](SPEC.md), which is also where every design decision and every reversal is recorded.

---

## Honest notes

- **All data is synthetic**, generated in-repo by a seeded script. No real merchant data is used or
  needed.
- **The NPCI retry constraints are assumptions.** Attempt caps, cooling periods, and the retry
  ladder come from public industry summaries of UPI Autopay behaviour, not from the primary NPCI
  circular. They live in one file, `backend/app/policy.py`, and are labelled as such.
- **The scorer is trained on data this repo generates**, so its AUC measures the pipeline, not a
  real-world result. The generator is deliberately built with an interaction the model must
  discover, genuine label noise, and one cohort it systematically gets wrong — see SPEC §2.2.
- **Defense-only.** Nothing in this repository is offense-capable.

## Licence

MIT
