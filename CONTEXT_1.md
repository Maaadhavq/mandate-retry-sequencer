# Razorpay AI Buildathon — Project Context

> Drop this at the repo root. It is the single source of truth for what this project is,
> who judges it, and what "done" means. Read it before writing code.

**Source:** https://razorpay.com/buildathon/
**Compiled:** 26 Aug 2026
**Applications close:** 5 September 2026

---

## 1. What this is

Razorpay is hiring **AI Builder Interns** through a student-only buildathon. There is no
resume screening and no aptitude test. The submission *is* the application.

| | |
|---|---|
| Stipend | ₹75,000 / month |
| Duration | 6 or 12 months (candidate's choice) |
| Location | In-person, Bangalore, starting September |
| Eligibility | Currently enrolled students only |
| Selection | Shortlisted builders go straight to a panel interview. No aptitude test, no GD. |
| Apply | https://forms.gle/d9r2gvxp8cmoZhon9 |

Razorpay's framing: *"No resume screening. No long application. Four steps: pick a track,
build something real, show your work (a public repo, a 5 minute pitch video, the
architecture), and if it has signal we call you in."*

Tagline: *"Your code speaks louder than your resume."*

### Deliverables (all tracks, non-negotiable)

1. **Public repository** — working code, not slides
2. **5-minute pitch video** — the demo has to fit in five minutes
3. **Architecture documentation** — how it works, not just what it does

**Track is selected at the time of application.** Committing to a track is committing.

---

## 2. The five tracks

### Track 01 — AI Growth & Agentic Commerce
> "Grow the merchant's revenue, and make them sellable to AI buyers."

Build an agent that grows revenue for a merchant on Razorpay test-mode APIs, or that makes
a merchant transactable by an AI buyer end to end.

**Why now (Razorpay's words):** NPCI's UAP and the global protocol race (ACP, AP2, x402)
make agent-to-agent commerce the open problem of the year, and Razorpay's in-app pilots are
already live.

**Example directions:** Conversational in-app checkout · Agent-readable catalog ·
Upsell & cross-sell agent · Campaign orchestrator

**The bar:** *"Every money action explainable, bounded and gated. Show the audit trail and
one failure handled gracefully."*

---

### Track 02 — AI Risk Manager
> "Stop the merchant losing money to fraud, returns and chargebacks."

Build a working detector, verifier or auto-responder for one class of loss, with measured
precision and recall on a held-out test set.

**Why now:** AI-enabled fraud is hitting Indian BFSI while returns and chargebacks quietly
eat margin. *"This track surfaces the risk and ML minded builders the others miss."*

**Example directions:** Chargeback evidence responder · Return-risk scorer ·
Fraud-spike detector · Abuse-ring sentinel

**The bar:** *"Honest metrics including false-positive cost. Strictly defense-only:
anything offense-capable is disqualified."*

---

### Track 03 — AI Revenue Recovery  ← **SELECTED**
> "Find revenue that's slipping away and win it back."

Build an agent that detects revenue at risk, determines the right intervention, and executes
a bounded recovery workflow: from payment failures and checkout abandonment to overdue
receivables.

**Why now:** *"Revenue loss rarely happens in one clean step. A payment degrades, a checkout
gets abandoned, a subscription fails, or an invoice goes overdue. AI can now close the loop
from detecting the problem to diagnosing it, choosing the right intervention, and recovering
the money."*

**Example directions:** Payment degradation → root cause → recovery action ·
Checkout drop-off recovery · Failed-subscription recovery · B2B receivables chaser ·
**Mandate retry sequencer** · Hinglish voice recovery · **Promise-to-pay tracker**

**The bar:** *"Don't just identify the problem. Show measured money recovered across a batch,
with compliant escalation, stopping rules, and an audit trail."*

---

### Track 04 — AI Finance Controller
> "Run the books and the cash position."

Build an agent that closes one finance-ops loop across a 50+ record batch of synthetic data,
reporting its match rate and the exceptions it could not resolve.

**Why now:** The 2026 builder consensus — verification capacity, not generation speed, is the
bottleneck. Reconciliation, settlement and forecasting are still done by hand.

**Example directions:** Multi-source reconciliation · Settlement Q&A agent ·
Forward cash forecaster · Tax-line matcher

**The bar:** *"Throughput plus measured accuracy plus an honest exception list. One
cherry-picked match proves nothing."*

---

### Track 05 — Open Track
> "Build what you believe should exist."

Any domain, workflow, or user is fair game.

**The bar:** *"Open doesn't mean easier. Show a real problem, a working product, meaningful
use of AI, and evidence that it creates value. The same bar for execution, reliability, and
depth applies here."*

---

## 3. Track decision and rationale

**Chosen: Track 03 — AI Revenue Recovery.**

Reasoning, so it stays consistent through the build:

- **The bar rewards a system, not a model.** Track 3's bar opens with *"Don't just identify
  the problem"* — Razorpay is explicitly pre-empting submissions that stop at a classifier.
  Most Track 3 entries will be the inverse failure: an LLM agent with no measurement. A
  submission with a real scored model underneath *and* a bounded workflow on top beats both.
- **Track 2 is the tempting alternative** and Razorpay says it *"surfaces the risk and ML
  minded builders the others miss"* — a genuine signal that it is under-crowded. But it is
  judged on precision/recall against a held-out set, everyone reaches for the same public
  fraud datasets, and it collapses into a leaderboard where entries sit a rounding error
  apart. Bring Track 2's metric rigor *into* Track 3 instead.
- **Track 1 is the hype track** — it will be the most crowded, and the protocol race (ACP,
  AP2, x402) is a knowledge moat, not a build advantage.
- **Revenue recovery is Razorpay's actual core business pain**, which reads well in the panel.
- Track 3's "why now" is the longest and most specific block on the page. They care about it.

---

## 4. Project spec

**Working title:** Mandate retry sequencer + promise-to-pay tracker for UPI Autopay
subscription failures.

Picked because mandate retries carry hard regulatory constraints, which means the
"compliant escalation and stopping rules" the bar demands come from the domain rather than
being bolted on for show.

### Pipeline

```
Batch of 500+ synthetic failed mandate debits
        │
        ▼
  [ SCORER ]  LightGBM
        ├── P(recovery | features)
        └── optimal retry window
            features: prior failure reason (insufficient balance vs. technical
            decline vs. revoked mandate), payday clustering, retry history,
            ticket size, merchant category, days since last success
        │
        ▼
  [ DECIDER ]  agent
        ├── retry now
        ├── retry at predicted window
        ├── dunning message
        ├── promise-to-pay capture + track
        └── stop / write off
        │
        ▼
  [ GUARDRAILS ]  hard rules, evaluated before every action
        ├── never retry a revoked mandate
        ├── max attempts per NPCI limits
        ├── cooling period between attempts
        └── escalation path with a defined terminal state
        │
        ▼
  [ LEDGER ]  every decision logged: input, score, rule fired, action, outcome
        │
        ▼
  [ DASHBOARD ]  ₹ recovered vs. ₹ at risk · recovery rate by cohort ·
                 attempts per recovery · honest list of what it failed to recover
```

### Why each piece exists (map to the bar)

| Bar requirement | Where it's satisfied |
|---|---|
| "measured money recovered across a batch" | Dashboard ₹ recovered vs. ₹ at risk, 500+ record batch |
| "compliant escalation" | Guardrail layer, NPCI-derived rules |
| "stopping rules" | Explicit stop / write-off terminal state |
| "audit trail" | Per-decision ledger |
| "Don't just identify the problem" | Decider + executor, not just the scorer |

### Borrowed from other tracks (free credibility)

- **SHAP on the scorer** → satisfies Track 1's *"every money action explainable"* without
  being asked.
- **Honest false-positive cost** (Track 2's bar) → report the cost of retrying a payment that
  was never going to succeed, not just the wins.
- **Honest exception list** (Track 4's bar) → the "what it failed to recover" panel.

### Non-negotiables

- Synthetic data only, generated by a script in the repo, seeded and reproducible.
- Held-out evaluation set. No metric reported on data the model trained on.
- Every ₹ figure traceable to a row in the ledger.
- Nothing offense-capable anywhere in the repo.

---

## 5. Builder profile (for reference when scoping)

Relevant existing experience to lean on:

- **LangGraph multi-agent KYC remediation** (hackathon) — detect → auto-resolve → customer
  outreach → compliance escalation, with mocked infrastructure and three demo scenarios.
  This project is the same graph with payments substituted for KYC.
- **PAD Detection** — LightGBM, AUC 0.88, six-model benchmark on EHR data. The scorer
  discipline transfers directly.
- **Finstox** — LSTM + LIME + SHAP explainable stock trading on NSE data. The explainability
  layer transfers directly.
- Stack: Python, LangChain/LangGraph, PyTorch, scikit-learn, LightGBM, FastAPI, React, SHAP/LIME.

---

## 6. Working agreements for anyone (or any agent) building in this repo

- The 5-minute video is the real constraint. If a feature cannot be shown in the video, it is
  not a priority.
- Prefer one loop closed completely over three loops half-built.
- Every number that appears in the pitch must be reproducible by running a script in the repo.
- Architecture doc is a deliverable, not an afterthought — write it as the system takes shape.
- Defense-only. Nothing that could be repurposed to attack a payment system.

---

## 7. Open questions

- [ ] Confirm current NPCI UPI Autopay retry limits and cooling periods before hardcoding them
- [ ] Decide whether to use Razorpay test-mode APIs for execution or keep it fully simulated
- [ ] Dashboard: static HTML report vs. small React app — pick based on video legibility
- [ ] Whether to include the Hinglish dunning message angle or keep scope tight
