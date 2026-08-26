# SPEC — Mandate Retry Sequencer

Razorpay AI Buildathon, Track 03 (AI Revenue Recovery). Solo build.
Companion to `CONTEXT_1.md` — the buildathon brief, kept locally and not published in this
repo. That document is *why this project*, this one is *what "done" means*. Citations to its
§7 questions below are traceability notes, not links.

**Repo:** `mandate-retry-sequencer` (public)
**Written:** 26 Aug 2026 · **Submit:** 4 Sep 2026 · **Deadline:** 5 Sep 2026
**Status of this document:** contract. Changing a decision here means editing this file first.

---

## 1. Scope

### 1.1 What this is

A batch recovery system for failed UPI Autopay mandate debits. It scores each failure for
recoverability, decides an intervention, refuses anything the rules forbid, executes against a
simulated payment rail over a 14-day horizon, and reports money recovered against money at risk
with a per-decision audit trail.

The system is a **deterministic spine with a narrow model-driven segment**. A LightGBM scorer
produces P(recover). Hard compliance rules run first and can veto anything. An LLM agent decides
only where the score is genuinely ambiguous. Every action, model-chosen or not, passes back
through the guardrails before it executes.

### 1.2 In scope

| # | Component | File |
|---|---|---|
| F1 | Synthetic data generator | `backend/scripts/generate_data.py` |
| F2 | LightGBM recovery scorer | `backend/scripts/train_scorer.py` |
| F3 | Guardrail layer | `backend/app/guardrails.py` |
| F4 | Ledger + executor + simulation clock | `backend/app/ledger.py`, `executor.py`, `clock.py` |
| F5 | Decider agent | `backend/app/decider.py`, `backend/app/llm_cache.py` |
| F6 | Batch runner | `backend/app/main.py` |
| F7 | Dashboard | `frontend/` |
| F8 | Promise-to-pay tracker | folded into F4/F6 |
| F9 | SHAP explainability | `backend/app/explain.py` |

### 1.3 Out of scope — decided, not deferred

- **No real payment APIs.** No Razorpay test-mode calls, no NPCI integration. The executor is a
  simulator. (Closes `CONTEXT_1.md` §7 Q2.)
- **No database.** JSONL and CSV on disk. A Postgres dependency is a judge who cannot run the repo.
- **No auth, no multi-tenancy, no merchant onboarding.**
- **No Hinglish / voice / message-copy generation.** (Closes `CONTEXT_1.md` §7 Q4.)
- **No deployment as a priority.** A live URL is not a listed deliverable. The bar that replaces it
  is §8.4: a judge clones the repo and it runs. Deploy only if 2 Sep has spare hours.
- **Nothing offense-capable.** Non-negotiable, repo-wide.

### 1.4 Stack

- **Backend:** Python **3.12** pinned via `uv` — *not* the system 3.14, which has unreliable
  LightGBM and SHAP wheels. FastAPI. LightGBM. SHAP.
- **Frontend:** React + Vite + Recharts. (Closes `CONTEXT_1.md` §7 Q3 — a React app, because the
  video needs legible motion and live filtering, not a static export.)
- **LLM:** `anthropic` Python SDK, model `claude-haiku-4-5`.
- **Storage:** `data/*.csv`, `data/ledger.jsonl`, `models/*`, `cache/llm/*.json`.

---

## 2. Data, features, and contracts

### 2.1 The record

One row = one failed mandate debit awaiting recovery. Money is **integer paise everywhere**.
A float never touches a currency value.

| Field | Type | Range / values |
|---|---|---|
| `row_id` | str | `mrs_<6 hex>`, unique across train and holdout |
| `failure_reason` | enum | `insufficient_balance` · `technical_decline` · `revoked_mandate` |
| `days_to_payday` | int | 0–30 |
| `attempt_number` | int | 1–4 (1 = original debit, 2–4 = retries) |
| `ticket_size_paise` | int | 4,900 – 4,999,900 (₹49 – ₹49,999) |
| `merchant_category` | enum | `saas` · `edtech` · `ott` · `fitness` · `utilities` |
| `days_since_last_success` | int | 0–180 |
| `mandate_age_days` | int | 1–1095 |
| `last_attempt_at` | ISO ts | drives the cooling-period rule |

Batch size **500**, default seed **42**, 80/20 train/holdout split written **at generation time,
before any modelling**.

### 2.2 Ground truth — deliberately hard

The generator holds a hidden `P(recover | features, retry_delay_hours)` that is **never a column**.
It is built to be learnable but not trivially so, because a generator with clean separable signal
produces a metric that means nothing to a judge who reads `generate_data.py`.

Three properties are required:

1. **An interaction the model must discover.** `days_to_payday` dominates recovery for
   `insufficient_balance` and is near-irrelevant for `technical_decline`. Recovery peaks when the
   retry *lands on or just after* payday — i.e. it is a function of
   `days_to_payday - retry_delay_hours/24`, not of either alone.
2. **Label noise.** ~8% of outcomes flip against the ground-truth probability. Real recovery data
   is not clean.
3. **A blind spot.** `edtech` recovery follows a hidden academic fee cycle that is **not exposed as
   a feature**. The scorer will systematically over-predict recovery for this cohort. This is
   intentional: it gives the honest-failures panel a real cluster, gives the limitations section of
   `ARCHITECTURE.md` something true to say, and gives you a genuine answer when a panel asks where
   the model is weak.

`revoked_mandate` recovery is **exactly 0.0, always**, with no noise applied. It is a hard rule,
not a probability.

Expected holdout AUC: **0.78–0.84**. Above 0.90 means the generator leaked something — investigate,
do not celebrate. Below 0.70 is a build failure.

### 2.3 Score bands

The scorer emits `P(recover)` in `[0,1]`. Bands are exhaustive and non-overlapping:

| Band | Range | Default action |
|---|---|---|
| High | `score >= 0.65` | `RETRY_NOW` |
| **Ambiguous** | `0.35 <= score < 0.65` | **agent decides** (§4) |
| **Ambiguous** | `0.15 <= score < 0.35` | **agent decides** (§4) |
| Low | `score < 0.15` | `STOP` — write off |

The agent's call surface is exactly `0.15 <= score < 0.65`, and only on records where no hard rule
fired. Roughly 150–200 of 500. Everything else is deterministic.

### 2.4 Actions

`RETRY_NOW` · `RETRY_SCHEDULED` (at 24h, 72h, or 168h) · `DUNNING_P2P` (capture a promise) ·
`STOP` (terminal write-off) · `BLOCKED_COOLING` (guardrail-imposed, not choosable)

### 2.5 Dashboard contract

Six stat cards plus four panels. The dashboard renders whatever `POST /batch/run` returns (§7.2).

**Headline:** ₹ recovered against ₹ at risk, as one number with a fill bar.

**Stat cards:** ₹ at risk · ₹ recovered · recovery rate · attempts per recovery (mean) ·
records stopped by a hard rule · ₹ spent on failed retries.

**Panels:**

1. **Recovery by cohort** — grouped bars by `failure_reason` and by `merchant_category`. *(Cut #2.)*
2. **Attempts per recovery** — histogram, 1–4.
3. **Honest failures** — every unrecovered record, the rule or score that stopped it, and the ₹ left
   on the table. Sorted by ₹ descending. **Never paginated away or collapsed by default.** This
   panel is a deliberate part of the submission.
4. **False-positive cost** — ₹ spent retrying payments that were never recoverable, split by whether
   a guardrail or the score should have caught it.

---

## 3. Guardrails

Pure functions. No I/O, no model import, no network.

```
evaluate(record, score, now) -> Decision(action, rules_fired, reason, retry_delay_hours)
```

### 3.1 Precedence — a hard rule always beats a score, and always beats the agent

| # | Rule | Result | Overridable |
|---|---|---|---|
| 1 | `failure_reason == revoked_mandate` | `STOP` | **Never** |
| 2 | `attempt_number >= 4` | `STOP` (write off) | **Never** |
| 3 | hours since `last_attempt_at` < 24 | `BLOCKED_COOLING` | **Never** |
| 4 | outside the 14-day campaign horizon | `STOP` | **Never** |
| 5 | score bands per §2.3 | see §2.3 | agent decides within the ambiguous band |

Rules 1–4 short-circuit. **The agent is never called when rules 1–4 fire.** When the agent proposes
an action, that proposal is re-validated against rules 1–4 before execution — an agent proposal is a
*request*, never an authority.

### 3.2 Invariants

- Every `Decision` carries a non-empty `rules_fired`. There are no silent paths.
- Bands are exhaustive and non-overlapping across `[0.0, 1.0]` inclusive.
- Boundaries are closed below: `24.0` hours is **not** cooling; `23.9` is.
- `evaluate` is deterministic and referentially transparent — same inputs, same `Decision`, forever.

### 3.3 Constraint provenance — flag these honestly

Max 4 attempts, 24h cooling, and the 24/72/168h retry ladder are **derived from industry summaries
of NPCI UPI Autopay behaviour, not read from the primary NPCI circular.** They are encoded in one
place, `backend/app/policy.py`, as named constants with source comments.

`ARCHITECTURE.md` must state this explicitly. Claiming regulatory precision you have not verified is
the fastest way to lose a payments panel. *(Closes `CONTEXT_1.md` §7 Q1 — as a stated assumption.)*

---

## 4. The decider agent

### 4.1 Why it exists

Track 03 asks for an agent that "determines the right intervention." In the ambiguous band the
score alone does not determine it — a ₹49 OTT renewal two days before payday and a ₹40,000 SaaS
invoice nineteen days out can carry the same 0.44 and warrant different treatment. That judgment is
what the agent supplies, and its one-sentence reasoning is written to the ledger and rendered on
screen.

### 4.2 Mechanics

- **Model:** `claude-haiku-4-5`.
- **Structured output**, not a tool: `output_config.format` with a strict JSON schema, read back via
  `client.messages.parse()`. Schema: `action` (enum, §2.4), `retry_delay_hours` (enum 24/72/168,
  null unless `RETRY_SCHEDULED`), `confidence` (0–1), `reasoning` (one sentence, max 200 chars).
- **Prompt caching:** the policy prompt is byte-identical across every call. It goes in `system`
  with a `cache_control` breakpoint; the per-record data goes in the user message. Assert
  `usage.cache_read_input_tokens > 0` after the first call — a zero means something volatile leaked
  into the prefix.
- `max_tokens: 256`. No thinking configuration.
- **Never** pass `temperature` — it is removed on current models and returns 400. Determinism comes
  from §4.3, not from sampling parameters.

### 4.3 Reproducibility — three layers

This is the design that reconciles an LLM in the money path with the non-negotiable that every ₹
figure be reproducible by running a script.

1. **Response cache.** Keyed on `sha256(model + policy_version + canonical_record_json)`. Stored as
   `cache/llm/<key>.json`. **Committed to the repo.** A clone with no API key replays every decision
   and reproduces the video's totals byte for byte.
2. **Deterministic fallback policy** — `decide_fallback(record, score)` in
   `backend/app/guardrails.py`, beside the band logic it mirrors. (`policy.py` stays
   import-free constants, so putting it there would cycle with `models.py`.) Used when there is
   no cache entry and no API key. Pure, tested, no network.
3. **Ablation.** Because layer 2 exists, `--no-llm` runs the entire pipeline without the agent. The
   ₹ delta between the two runs is the measured contribution of the agent. Report this number in the
   video — it is a better answer than any claim about the model.

A cache miss with no key is a fallback, logged as `agent_source: "fallback"` on the ledger row —
never a crash, never a silent substitution. Every ledger row records `agent_source` as one of
`live` · `cache` · `fallback` · `deterministic`.

### 4.4 Failure handling

Schema-invalid response → retry once → fall back to layer 2. API error or timeout → fall back
immediately. The pipeline **never** blocks on the API and never fails a batch because of it.

---

## 5. Ledger, executor, and the clock

### 5.1 Ledger

Append-only, `data/ledger.jsonl`, one row per decision. An existing row is never rewritten — a
correction is a new row. Fields: `row_id`, `sim_ts`, `attempt_number`, input snapshot, `score`,
`rules_fired`, `action`, `retry_delay_hours`, `agent_source`, `agent_reasoning`, `outcome`,
`amount_paise`, `recovered_paise`.

`aggregate()` returns exactly the §7.2 response shape.

### 5.2 Executor

Samples an outcome by evaluating the generator's ground-truth function at the *actual* realised
retry delay, plus noise. It **never mutates the input record** and **never writes to the ledger** —
the runner owns both.

Returns `RECOVERED` · `FAILED` · `PROMISED` (P2P captured) · `PROMISE_KEPT` · `PROMISE_BROKEN`.

A `revoked_mandate` never returns a recovery under any code path.

### 5.3 The 14-day clock

`POST /batch/run` steps a simulated clock across **14 days in 1-hour ticks** from a fixed
`sim_start`. Cooling periods gate real attempts, scheduled retries fire in their window, and
promises come due. Without this, the guardrails are assertions rather than demonstrated behaviour
and "attempts per recovery" is not a distribution.

Per tick: wake due records → guardrails → (agent if ambiguous) → re-validate → execute → ledger.

At horizon end, every record is in a terminal state: `RECOVERED`, `WRITTEN_OFF`, or `EXPIRED`.

### 5.4 Promise-to-pay (F8)

Minimal loop. `DUNNING_P2P` captures `promised_amount_paise` and `promised_date` (2–7 days out,
sampled). The clock advances to that date; the executor resolves the promise kept or broken. A
**broken promise re-enters the pipeline** with `attempt_number + 1`, and is therefore still subject
to rules 1–4 — a broken promise cannot escape the attempt cap.

Adds one cohort to the dashboard: promises made, kept, broken, ₹ recovered via promise.

---

## 6. Repo layout

```
backend/
  app/     main.py policy.py guardrails.py decider.py llm_cache.py
           ledger.py executor.py clock.py explain.py
  scripts/ generate_data.py train_scorer.py verify_totals.py
  tests/   test_generate_data.py test_scorer.py test_guardrails.py
           test_ledger.py test_decider.py test_e2e.py
frontend/  src/ (React + Vite + Recharts)
data/      batch_train.csv batch_holdout.csv ground_truth.json ledger.jsonl
models/    scorer.txt metrics.json
cache/llm/ <sha256>.json   # committed
ARCHITECTURE.md  README.md  SPEC.md  CLAUDE.md
```

---

## 7. Interfaces

### 7.1 Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{"status":"ok","version":"..."}` |
| POST | `/batch/run` | run the campaign, return §7.2 |
| GET | `/ledger` | paged raw rows, for the audit-trail moment in the video |
| GET | `/explain/{row_id}` | SHAP contributions *(cut #1)* |

`POST /batch/run` body: `{"seed": 42, "n": 500, "use_llm": true}`.

### 7.2 `/batch/run` response — frozen at skeleton time

**This shape is locked before feature work begins. The frontend is built against it and the batch
runner replaces the stub beneath it without changing it.**

```json
{
  "run_id": "run_8hexchars",
  "seed": 42,
  "config": { "n": 500, "horizon_days": 14, "use_llm": true },
  "totals": {
    "at_risk_paise": 0, "recovered_paise": 0, "recovery_rate": 0.0,
    "attempts_per_recovery": 0.0, "false_positive_cost_paise": 0,
    "stopped_by_hard_rule": 0
  },
  "cohorts": {
    "by_failure_reason": [ { "key": "", "at_risk_paise": 0, "recovered_paise": 0, "n": 0 } ],
    "by_merchant_category": [ { "key": "", "at_risk_paise": 0, "recovered_paise": 0, "n": 0 } ]
  },
  "attempts_histogram": [ { "attempts": 1, "count": 0 } ],
  "promises": { "made": 0, "kept": 0, "broken": 0, "recovered_paise": 0 },
  "failures": [
    { "row_id": "", "amount_paise": 0, "stopped_by": "", "rules_fired": [],
      "score": 0.0, "agent_reasoning": "" }
  ],
  "agent": {
    "records_routed": 0,
    "sources": { "live": 0, "cache": 0, "fallback": 0, "deterministic": 0 }
  }
}
```

---

## 8. End-to-end verification

The definition of done. Every item produces evidence, not an assertion.

### 8.1 Per-component gates

- **F1:** two runs at `--seed 42` are byte-identical; `--seed 43` differs; no `row_id` in both
  splits; every `revoked_mandate` row has ground truth exactly `0.0`; all three failure reasons
  present at 5% or more each.
- **F2:** holdout AUC in 0.78–0.84; precision and recall at 0.65 / 0.35 / 0.15; confusion matrix at
  0.65; the holdout file is provably not read before the model is fit; same seed reproduces
  `metrics.json` exactly.
- **F3:** one test per rule, plus — revoked with score 0.99 still STOPs; `attempt_number 4` with
  score 0.99 still STOPs; 23.9h is cooling and 24.1h is not; every Decision has non-empty
  `rules_fired`; bands exhaustive and non-overlapping across `[0.0, 1.0]`.
- **F4:** exactly one ledger row per decision, asserted by count; append-only; `aggregate()` total
  equals a manual sum; executor never recovers a revoked mandate; paise round-trip with zero drift.
- **F5:** a cache hit returns byte-identical output to the recorded live call; an invalid schema
  response falls back rather than crashing; `--no-llm` completes a full run with no key set;
  `cache_read_input_tokens > 0` after the first call.

### 8.2 Whole-pipeline gates

1. Every input record has at least one ledger row, and ends in a terminal state. Assert both; show
   the counts.
2. Returned `recovered_paise` equals a manual sum over `data/ledger.jsonl` computed by
   `verify_totals.py`, **which does not import `ledger.py`**.
3. At least one record hits each of hard rules 1, 2, and 3 on a 500-record batch. Print one example
   ledger row for each. If any never fires, the generator is not producing that case — fix the
   generator, do not move on.
4. **At least one record where the agent proposed a retry and a hard rule vetoed it.** Print that
   row. This is the demo's centrepiece; if the batch never produces one, seed a case that does.
5. Seed 42 reproduces identical totals across full reruns.
6. `--no-llm` vs default run: both complete, and the ₹ delta is recorded as the agent's measured
   contribution.

### 8.3 Traceability

Every ₹ figure on the dashboard traces to ledger rows. `verify_totals.py` proves it independently.

### 8.4 The clone test — replaces "deploy at hour 3"

```
git clone <repo> /tmp/judge-test && cd /tmp/judge-test
# follow README literally, no improvising, no API key set
```

Anything fixed by instinct is a README bug. Run it on **2 Sep** and again before submitting.

---

## 9. Cut list

Ordered. Read this on 30 Aug if the gate in §10 has not passed.

1. **SHAP explainability (F9).** The agent writes its own reasoning to every row it touches, so
   explainability no longer depends on SHAP.
2. **Cohort breakdown charts (§2.5 panel 1).** Headline, failures panel, and false-positive cost all
   survive; the slicing goes.
3. **Promise-to-pay tracker (F8).** `DUNNING_P2P` degrades to a terminal action. If cut, delete
   "promise-to-pay tracker" from the README title — do not ship a title the repo does not earn.

**Never cut:** the honest-failures panel · the ledger · hard rules 1–4 · the clone test ·
`ARCHITECTURE.md`.

---

## 10. Build order and gates

No calendar. The deadline (5 Sep) is an outer bound, not a plan — submit as soon as Gate E passes.
What follows is dependency order, which does not compress, and five gates, which are the only real
checkpoints.

### 10.1 Dependency graph

```
critical path:   F1 generator ──▶ F2 scorer ──▶ F4 ledger/executor/clock ──▶ F6 runner
parallel:        F3 guardrails      (no dependencies — pure functions)
                 F7 dashboard shell (needs only the frozen §7.2 shape)
after F4:        F8 promise-to-pay
after F3 + F4:   F5 decider agent
after F2:        F9 SHAP
```

F3 and F7 can be built at any time, including before F1. They are the natural parallel worktrees.
Nothing else on the critical path can start early — a scorer with no data is not a thing.

### 10.2 Gates

| Gate | Passes when | Blocks |
|---|---|---|
| **A** — skeleton | `/health` returns ok; `/batch/run` returns the §7.2 shape as a stub; frontend renders six cards off it; `pytest` runs green on an empty suite | everything |
| **B** — the hard gate | `/batch/run` returns **one real ₹ recovered figure, end to end**, using the deterministic fallback policy and **no LLM** | F5, F8, F9 |
| **C** — agent live | agent decides the ambiguous band; §8.2 gate 4 produces a vetoed-proposal row; `--no-llm` ablation delta measured | video |
| **D** — hardened | §8.1 and §8.2 all green; secrets swept; §8.4 clone test passes with no API key set | submission |
| **E** — deliverables | `ARCHITECTURE.md`, `README.md`, 5-minute video recorded, repo public | — |

### 10.3 The hard gate — B

**Gate B is the one that decides this project.** The deterministic fallback policy (§4.3 layer 2) is
built as part of F3/F4, so the pipeline closes completely *before* the agent exists. The agent is an
upgrade to a working system, never a dependency of it.

If Gate B is not passing and the work is slowing rather than speeding up, stop feature work and open
§9. One loop closed completely beats three half-built — that rule does not relax because the
schedule did.

### 10.4 Order of work

1. Gate A — skeleton, `CLAUDE.md`, hooks, venv, repo
2. F3 guardrails (pure, testable immediately, and it is the piece the track bar rewards)
3. F1 generator
4. F2 scorer
5. F4 ledger + executor + clock → **Gate B**
6. F7 dashboard on real data
7. F5 decider agent → **Gate C**
8. F8 promise-to-pay
9. F9 SHAP
10. Harden → **Gate D**
11. Docs + video → **Gate E**

F3 moves ahead of F1 here because it needs nothing, it is fully testable in isolation, and it is the
component the Track 03 bar most directly rewards. Getting it done early means the rest of the build
is decorating a compliance layer that already works.

---

## 11. Non-negotiables

- Synthetic data only, generated in-repo, seeded, reproducible.
- No metric ever reported on data the model trained on.
- Every ₹ figure traceable to a ledger row.
- Money is integer paise. Never a float.
- Nothing offense-capable anywhere in this repo.
- If it cannot be shown in the 5-minute video, it is not a priority.
- One loop closed completely beats three half-built.
