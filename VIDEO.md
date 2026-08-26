# Video script — 5 minutes

Track 03's bar: *"Don't just identify the problem. Show measured money recovered across a batch,
with compliant escalation, stopping rules, and an audit trail."* The script below hits those four
in that order, and every number in it is real output from seed 42.

**Before recording:**

```bash
.venv/Scripts/python -m backend.scripts.generate_data --seed 42 --n 500 --name batch
.venv/Scripts/python -m backend.scripts.generate_data --seed 1042 --n 8000 --name corpus --split
.venv/Scripts/python -m uvicorn backend.app.main:app --port 8000     # terminal 1
cd frontend && npm run dev                                          # terminal 2
```

Open `http://localhost:5173`. Have a second terminal ready for `verify_totals`. Do **not** press
Run batch until the script says to — the empty state is part of the opening.

---

## 0:00–0:30 · The problem

> A UPI Autopay mandate debit fails. Something has to happen next, and today at most merchants that
> something is a fixed retry a day later, applied to everything.
>
> The interesting part isn't predicting which ones come back. It's that some of these must never be
> retried at all, no matter how recoverable a model thinks they are. A revoked mandate has no
> authority behind it. A record on its fourth attempt has used its cycle. Those are compliance
> facts, not probabilities.
>
> So this is built as a deterministic spine with a narrow model-driven segment — not a model with
> rules bolted on afterwards. Here's what it does to five hundred failed debits.

*(On screen: the dashboard in its empty state.)*

---

## 0:30–1:15 · Measured money recovered

**Press Run batch.** Let the numbers land, then read the headline.

> ₹44,25,090 recovered against ₹1,26,32,606 at risk. Thirty-five percent, across five hundred
> records, over a simulated fourteen-day campaign stepped in one-hour ticks.
>
> One-point-three-five attempts per recovery — so it isn't buying that number by hammering the
> rail. A hundred and eighty-seven records were stopped by a hard rule before the model got a vote.
> And ₹242 spent on retries that never recovered anything, which is the cost of being wrong,
> reported rather than hidden.

*(Point at each of the six cards as you say them. Don't linger — the veto is the payload.)*

---

## 1:15–2:15 · Compliant escalation, and the twenty seconds that matter

Scroll to **Honest failures**. Click the `hard_max_attempts` filter chip.

> This is the part I'd want a payments team to look at first.
>
> `mrs_805558`. The scorer gives it **0.622** on a **₹45,627** ticket — a confident, high-band
> prediction, and the model's genuine opinion that this is recoverable.
>
> The system tried. Three real debit attempts, on the 2nd, the 4th and the 6th of September, each
> one spaced a full cooling period apart. All three failed. On the 7th it came due for a fourth,
> and the attempt cap refused it — still at 0.622, still ₹45,627 on the table, still the model
> saying yes.
>
> That's what a stopping rule has to look like. Not "never tried" — tried, exhausted its cycle, and
> then stopped, against an optimistic score rather than in agreement with it. The cap is checked
> before the score is read at all, and there's no code path from a hard rule back to the bands.

Scroll up to the red **A hard rule overrode a proposed retry** panel.

> And here's the same principle one layer further in. The agent decides the ambiguous middle of the
> distribution — scores between 0.15 and 0.65, about 250 of the 500. `mrs_cc107e`, score 0.483,
> ₹45,035: the agent booked a retry on the 9th of September.
>
> That window landed past the 15th, when the fourteen-day campaign closes. The rule fired on
> re-validation, the retry never executed, and the ledger row records `vetoed_proposal:
> RETRY_NOW` — what was asked for, and what refused it.
>
> That's the design in one row. The agent's output is a proposal, not an authority. Every proposal
> goes back through the same rules before anything runs.

*(Eleven rows carry a veto on this seed. Filter the table by `vetoed_agent_proposal` if you want to
show more than one.)*

---

## 2:15–3:00 · Stopping rules and the audit trail

Point at the cohort chart.

> Revoked mandates: sixty-six records, **zero percent recovered**. Not "nearly zero" — zero. That
> bar is the compliance layer, visible as a number.

Switch to the terminal:

```bash
.venv/Scripts/python -m backend.scripts.verify_totals
```

> Every rupee on that dashboard comes from an append-only ledger — one row per decision, written
> once, never rewritten. This script re-derives the headline figure straight from the JSONL, and it
> deliberately does not import the aggregation code. If it shared it, a bug would show up on both
> sides and cancel out; this is the only version of the check that can actually fail.
>
> Same number. And it confirms revoked mandates recovered exactly zero paise.

---

## 3:00–3:50 · Where it's wrong, and how I know

Scroll to the cohort chart, point at `edtech`.

> Every model has a blind spot. I built one on purpose so I could show you mine.
>
> Edtech recovery follows an academic fee cycle that is not a feature and never will be — nobody
> ships a column for the school-fees calendar. The training corpus is mostly in-cycle. The batch it
> actually runs on is mostly off-cycle.
>
> The result: on this batch the model over-predicts edtech by **+0.097**, while every other cohort
> sits at **−0.014**. Edtech records it scores above 0.35 recover at **eleven-point-five percent**.
> The model is confidently wrong about exactly one segment and looks fine in aggregate.
>
> That's what distribution drift does to a production scorer, and it's why the honest-failures panel
> isn't collapsible. Three hundred and thirty records, ₹82,07,516 left on the table — sixty-five
> percent of everything at risk. If a system tells you it recovered a third of the money, you should
> be able to see the other two-thirds.

---

## 3:50–4:30 · Honest limitations

> Four things I'd want said out loud.
>
> **The data is synthetic.** All of it, seeded and generated in-repo. The AUC — 0.784, against a
> measured ceiling of 0.824 — grades the pipeline, not a real-world outcome.
>
> **The NPCI constants are assumptions.** Attempt caps, cooling periods, the retry ladder: those
> come from public industry summaries, not the primary circular. They're labelled as assumptions in
> the one file they live in, because claiming regulatory precision I haven't verified is worse than
> being slightly wrong.
>
> **The false-positive cost is understated.** ₹242 is processing spend. The real cost of a fourth
> failed debit is customer goodwill, and I don't model it at all.
>
> **The agent's measured contribution is currently ₹0.** The ablation runs the whole pipeline with
> and without it. With the response cache empty, both arms fall back to the same deterministic
> policy, so the delta is zero — and I'd rather report that than a number I didn't actually measure.

---

## 4:30–5:00 · Close

> The thing I'd defend is the layering. The scorer answers how likely recovery is. The rules answer
> what we're allowed to do about it. Those stay separate, the rules run first, and every action —
> model-chosen or not — passes back through them before it executes.
>
> Which is why the pipeline closes completely with the agent switched off. It's an upgrade to a
> working system, not a dependency of one. A hundred and fifty-four tests, and a clone with no API
> key reproduces every figure you just saw.

---

## If it runs long, cut in this order

1. **The promise-to-pay panel** (~15s). Nice, not load-bearing.
2. **The attempts-per-recovery card** at 0:30 (~8s). Fold it into the headline sentence.
3. **`verify_totals`** (~20s) — cut the terminal, say the sentence over the dashboard. Reluctantly;
   it's the strongest audit-trail evidence you have.
4. **Never cut** the veto section or the edtech section. Those are the two things nobody else's
   submission will have.

---

## Five questions a panel will ask

**1. "Your revoked mandates all score 0.03. Isn't your veto demo just the model agreeing with the
rule?"**

Fair, and worth being precise about. For rule 1 the model *does* agree — it learned revoked never
recovers, which is a good sign, not a bad one. The real conflict is rule 2: `mrs_805558` scores
0.622 and gets refused on the attempt cap. That's a genuine disagreement on ₹45,627, and it's the
case I show. I wouldn't claim a revoked-at-0.99 conflict, because on this data it doesn't happen.

**2. "The AUC is 0.78. That's not very good."**

It's close to the maximum achievable on this data. Because the generator's hidden probability is
known, I can measure the ceiling: ranking by the true probability gives 0.824. The scorer gets
0.784, with a bootstrap CI of 0.759 to 0.807. A 0.95 here would mean something leaked into the
features, and I'd be investigating rather than celebrating.

**3. "How do I know the model never saw the batch it's scoring?"**

Two datasets from the same generator: an 8,000-row corpus for training, a separate 500-row batch for
the run. `train_scorer.py` never names `batch.csv` — there's a test that greps the file to enforce
it. And the holdout is held shut by an object that raises if it's read before the model is fit; run
`--prove-seal` and you'll watch it refuse.

**4. "What's the weakest part of what you built?"**

The executor. Outcomes are sampled against the same ground-truth function that generated the
training labels, so the simulator and the model share an author. That makes recovery rates internally
consistent but not externally meaningful — it's a systems result, not a modelling one. Second
weakest is that the agent's contribution is unmeasured, for the reason I gave.

**5. "Why an LLM here at all, if the deterministic policy handles everything?"**

Because a ₹49 OTT renewal two days before payday and a ₹40,000 SaaS invoice nineteen days out can
carry the same 0.44 and warrant different treatment, and the score alone doesn't distinguish them.
The agent writes one sentence of reasoning to the ledger for every record it touches, which is the
explainability story. But I deliberately built it so the system works without it — the deterministic
fallback covers the same band, and the ablation is there to measure whether the agent is actually
earning its place rather than assuming it does.
