"""SPEC §8.2 gate 2 — recompute the headline totals straight from the ledger file.

This script deliberately **does not import `ledger.py`**. If it reused the same
aggregation code, agreement between the API and this check would be a tautology: a bug in
`aggregate()` would appear in both and cancel out. Reading the JSONL and summing it by
hand is the only version of this check that can actually fail.

Run it after `POST /batch/run`:

    .venv/Scripts/python -m backend.scripts.verify_totals
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_LEDGER = Path("data/ledger.jsonl")

#: Restated on purpose, not imported. See the module docstring — an independent check that
#: shares constants with the thing it is checking is not independent.
HARD_RULE_TAGS = {"hard_revoked_mandate", "hard_max_attempts", "hard_horizon_exhausted"}


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"{path} not found — run POST /batch/run first.")
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument(
        "--expect-recovered",
        type=int,
        default=None,
        help="paise the API reported; exits non-zero if the ledger disagrees",
    )
    args = parser.parse_args()

    rows = read_rows(args.ledger)
    if not rows:
        raise SystemExit(f"{args.ledger} is empty — nothing to verify.")

    recovered_paise = sum(int(r["recovered_paise"]) for r in rows)

    # At risk counts each mandate once, at whatever its ticket was on the first row.
    first_seen: dict[str, int] = {}
    for row in rows:
        first_seen.setdefault(row["row_id"], int(row["ticket_size_paise"]))
    at_risk_paise = sum(first_seen.values())

    recovered_ids = {r["row_id"] for r in rows if int(r["recovered_paise"]) > 0}
    attempt_cost = sum(int(r["attempt_cost_paise"]) for r in rows)
    wasted_cost = sum(
        int(r["attempt_cost_paise"]) for r in rows if r["row_id"] not in recovered_ids
    )

    debits: Counter[str] = Counter()
    for row in rows:
        if int(r_cost := row["attempt_cost_paise"]) > 0:
            debits[row["row_id"]] += 1
    per_recovery = (
        sum(debits.get(rid, 0) for rid in recovered_ids) / len(recovered_ids)
        if recovered_ids
        else 0.0
    )

    stopped_hard = len(
        {r["row_id"] for r in rows if HARD_RULE_TAGS & set(r["rules_fired"])}
    )

    outcomes: Counter[str] = Counter(r["outcome"] for r in rows)
    by_reason: dict[str, int] = defaultdict(int)
    for row in rows:
        by_reason[row["failure_reason"]] += int(row["recovered_paise"])

    unique_records = len(first_seen)
    print(f"ledger      {args.ledger}  ({len(rows)} rows, {unique_records} records)")
    print(f"at risk     {at_risk_paise:>14,} paise   Rs {at_risk_paise / 100:>12,.2f}")
    print(f"recovered   {recovered_paise:>14,} paise   Rs {recovered_paise / 100:>12,.2f}")
    print(f"rate        {recovered_paise / at_risk_paise:>14.4%}")
    print(f"attempts/recovery {per_recovery:>8.4f}")
    print(f"attempt cost{attempt_cost:>14,} paise   (wasted {wasted_cost:,})")
    print(f"hard-stopped{stopped_hard:>14,} records")
    print("\noutcomes")
    for outcome, count in sorted(outcomes.items()):
        print(f"  {outcome:<16} {count:>6}")
    print("\nrecovered by failure_reason")
    for reason, paise in sorted(by_reason.items()):
        print(f"  {reason:<22} {paise:>14,} paise")

    revoked_recovered = by_reason.get("revoked_mandate", 0)
    if revoked_recovered != 0:
        raise SystemExit(
            f"\nFAIL: revoked mandates recovered {revoked_recovered} paise. "
            "Rule 1 is absolute (SPEC §3)."
        )
    print("\nOK: revoked mandates recovered exactly 0 paise.")

    if args.expect_recovered is not None:
        if args.expect_recovered != recovered_paise:
            raise SystemExit(
                f"\nFAIL: API reported {args.expect_recovered:,} paise, "
                f"the ledger sums to {recovered_paise:,}. Difference "
                f"{args.expect_recovered - recovered_paise:,}."
            )
        print(f"OK: ledger agrees with the API at {recovered_paise:,} paise.")

    sys.exit(0)


if __name__ == "__main__":
    main()
