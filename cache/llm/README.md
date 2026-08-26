# Committed agent decisions

Each file here is one decision from the decider agent (SPEC §4.3 layer 1), named
`<sha256>.json` where the key is `sha256(model + policy_version + canonical_record_json)`.
They are committed on purpose: a clone with no API key replays them and reproduces the
headline totals byte for byte.

**This directory is currently empty**, and the pipeline is fully functional that way. Cache
entries are only produced by a run with `ANTHROPIC_API_KEY` set; with no key and no entry,
the agent falls through to the deterministic policy in `backend/app/guardrails.py`
(`decide_fallback`), which is pure, tested, and needs no network. Every ledger row records
which layer decided it, so a replayed run and a fallback run are never confused.

To populate it, set a key and run a batch once:

```bash
.venv/Scripts/python -c "from backend.app.runner import run_campaign; run_campaign(seed=42, use_llm=True)"
```

Then commit whatever lands here. The `--no-llm` ablation delta is only meaningful once
this directory is populated — until then both arms of the comparison run the same fallback
and the delta is correctly ₹0.
