"""Measure the agent's contribution by running the pipeline with and without it. SPEC §4.3 layer 3.

The number this prints is the only honest answer to "what does the LLM add?". It is a measured
rupee delta between two runs of the same seed, not a claim.

Two runs, identical in every respect except one:

    --no-llm   ambiguous band resolved by `decide_fallback`, pure and offline
    agent      ambiguous band resolved by the decider, which resolves cache -> live -> fallback

**With an empty cache and no API key both arms run the same fallback and the delta is ₹0.** That is
correct, not a bug, and this script says so rather than printing a zero and letting it look like a
finding. Populate the cache with one keyed run (`--populate`) and the comparison becomes real.

Usage:

    # measure whatever is currently possible
    .venv/Scripts/python -m backend.scripts.ablate

    # with ANTHROPIC_API_KEY set: fill cache/llm/ first, then measure
    .venv/Scripts/python -m backend.scripts.ablate --populate
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from backend.app.decider import MODEL, Decider
from backend.app.llm_cache import DEFAULT_CACHE_DIR, ResponseCache
from backend.app.runner import run_campaign

DEFAULT_SEED = 42


def rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def cache_size(directory: Path) -> int:
    return len(list(directory.glob("*.json"))) if directory.exists() else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--populate",
        action="store_true",
        help="run the agent live first to fill cache/llm/. Needs ANTHROPIC_API_KEY.",
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    before = cache_size(args.cache_dir)

    if args.populate:
        if not has_key:
            raise SystemExit(
                "--populate needs ANTHROPIC_API_KEY. Without it there is nothing to record:\n"
                "  set it, or run without --populate to measure what the current cache supports."
            )
        print(f"populating {args.cache_dir} via a live run on model {MODEL} ...")
        run_campaign(seed=args.seed, use_llm=True, decider=Decider(use_llm=True))
        print(f"  cache entries: {before} -> {cache_size(args.cache_dir)}\n")

    entries = cache_size(args.cache_dir)

    # Arm 1: no agent at all. Arm 2: the agent, resolving cache -> live -> fallback.
    off = run_campaign(seed=args.seed, use_llm=False)
    on = run_campaign(
        seed=args.seed,
        use_llm=True,
        decider=Decider(cache=ResponseCache(args.cache_dir), use_llm=has_key),
    )

    off_paise = off["totals"]["recovered_paise"]
    on_paise = on["totals"]["recovered_paise"]
    delta = on_paise - off_paise

    print(f"seed {args.seed}  ·  cache entries {entries}  ·  API key {'set' if has_key else 'not set'}\n")
    print(f"  {'--no-llm (deterministic fallback)':<38} {rupees(off_paise):>16}")
    print(f"  {'agent on the ambiguous band':<38} {rupees(on_paise):>16}")
    print(f"  {'-' * 38} {'-' * 16}")
    print(f"  {'measured contribution':<38} {rupees(delta):>16}")

    routed = on["agent"]["records_routed"]
    sources = on["agent"]["sources"]
    print(f"\n  routed to the agent: {routed}")
    print(f"  decided by: {sources}")

    if entries == 0 and not has_key:
        print(
            "\n  NOT A FINDING. The cache is empty and no API key is set, so both arms ran the\n"
            "  same deterministic fallback and the delta is ₹0 by construction. Run with\n"
            "  --populate and a key to make this comparison real."
        )
    elif delta == 0:
        print(
            "\n  A genuine ₹0: the agent was consulted and its decisions did not change the\n"
            "  outcome on this seed. Worth reporting as-is."
        )


if __name__ == "__main__":
    main()
