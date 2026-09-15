"""The trace behind a run — what the ledger says about *where* the debits went.

`/batch/run` returns the frozen §7.2 shape and nothing else. The dashboard's pipeline view
needs a second, read-only cut of the same ledger: how the 500 records partitioned across the
rules and the score bands, which hour of the day each debit actually ran, and how the ₹ came
in over the 14 days. This module derives all of that from ledger rows and nothing else, so a
figure here is exactly as traceable as a figure on the headline.

Pure functions over `LedgerRow`s. No model, no clock, no network. Constants come from
`policy.py`; nothing is re-declared here.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

from backend.app.guardrails import HARD_RULES, in_peak_window
from backend.app.ledger import DEFAULT_LEDGER_PATH, LedgerRow
from backend.app.policy import BAND_HIGH, BAND_LOW, HORIZON_DAYS, SIM_START_ISO

SIM_START = datetime.fromisoformat(SIM_START_ISO)
SCORE_BINS = 20

#: The rules that *end* a record. Cooling and the peak window defer; they never stop.
STOPPING_RULES: tuple[str, ...] = (
    "hard_revoked_mandate",
    "hard_max_attempts",
    "hard_horizon_exhausted",
)
DEFERRING_RULES: tuple[str, ...] = ("hard_cooling_period", "hard_peak_window")


def load_rows(path: Path | str = DEFAULT_LEDGER_PATH) -> list[LedgerRow]:
    """Re-read the ledger the last run wrote. Raises FileNotFoundError with the fix."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist — POST /batch/run first; the trace is derived from it."
        )
    rows: list[LedgerRow] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                payload = json.loads(line)
                payload["rules_fired"] = tuple(payload["rules_fired"])
                rows.append(LedgerRow(**payload))
    return rows


def summarise(rows: Iterable[LedgerRow]) -> dict:
    rows = list(rows)
    return {
        "flow": _flow(rows),
        "transitions": _transitions(rows),
        "rules_fired": _rules_fired(rows),
        "debits_by_hour": _debits_by_hour(rows),
        "peak_violations": _peak_violations(rows),
        "peak_deferrals": sum(1 for r in rows if "hard_peak_window" in r.rules_fired),
        "daily": _daily(rows),
        "score_histogram": _score_histogram(rows),
    }


# -- helpers -----------------------------------------------------------------------------


def _first_row_per_record(rows: Iterable[LedgerRow]) -> dict[str, LedgerRow]:
    first: dict[str, LedgerRow] = {}
    for row in rows:
        first.setdefault(row.row_id, row)
    return first


def _flow(rows: list[LedgerRow]) -> dict:
    """How the input partitioned. Every record lands in exactly one first-touch bucket.

    A record is classified by the *first* thing that happened to it: a stopping rule, or the
    score band it fell into. That is the order the guardrails evaluate in (SPEC §3.1), so it
    is also the order a reader should see the money leave the pipeline.
    """
    first = _first_row_per_record(rows)
    recovered_ids = {r.row_id for r in rows if r.recovered_paise > 0}

    stopped: Counter[str] = Counter()
    band_high = band_low = agent_band = 0
    for row in first.values():
        bucket = _entry_bucket(row)
        if bucket in STOPPING_RULES:
            stopped[bucket] += 1
        elif bucket == "band_high":
            band_high += 1
        elif bucket == "band_low":
            band_low += 1
        else:
            agent_band += 1

    # Stopped later, by a stopping rule, after at least one attempt — a record that got
    # through the first gate and was ended by the cap or the horizon further down the rail.
    stopped_later: Counter[str] = Counter()
    ended_by: dict[str, str] = {}
    for row in rows:
        rule = next((r for r in row.rules_fired if r in STOPPING_RULES), None)
        if rule and row.row_id not in ended_by:
            ended_by[row.row_id] = rule
    for row_id, rule in ended_by.items():
        first_rule = next((r for r in first[row_id].rules_fired if r in STOPPING_RULES), None)
        if first_rule is None:
            stopped_later[rule] += 1

    vetoed = len({r.row_id for r in rows if "vetoed_agent_proposal" in r.rules_fired})

    return {
        "input": len(first),
        "stopped_at_entry": {rule: stopped.get(rule, 0) for rule in STOPPING_RULES},
        "stopped_later": {rule: stopped_later.get(rule, 0) for rule in STOPPING_RULES},
        "band_high": band_high,
        "band_low": band_low,
        "agent_band": agent_band,
        "agent_vetoed": vetoed,
        "recovered_records": len(recovered_ids),
        "unrecovered_records": len(first) - len(recovered_ids),
    }


def _entry_bucket(row: LedgerRow) -> str:
    """Where a record went at its first touch: a stopping rule, or a score band."""
    rule = next((r for r in row.rules_fired if r in STOPPING_RULES), None)
    if rule:
        return rule
    if row.score >= BAND_HIGH:
        return "band_high"
    if row.score < BAND_LOW:
        return "band_low"
    return "agent_band"


def _outcome_label(last: LedgerRow, recovered: bool) -> str:
    """What ended a record. Mirrors `ledger._stopped_by_label` without needing the clock."""
    if recovered:
        return "recovered"
    for rule in last.rules_fired:
        if rule in STOPPING_RULES:
            return rule
    if last.action == "STOP":
        return "score_below_band"
    return "horizon_expired"


def _transitions(rows: list[LedgerRow]) -> list[dict]:
    """Entry bucket → outcome, one edge per (bucket, outcome) pair. The Sankey's data.

    Every record contributes exactly one unit, so the edge weights sum to the input.
    """
    first = _first_row_per_record(rows)
    last: dict[str, LedgerRow] = {}
    for row in rows:
        last[row.row_id] = row
    recovered_ids = {r.row_id for r in rows if r.recovered_paise > 0}

    edges: Counter[tuple[str, str]] = Counter()
    for row_id, row in first.items():
        edges[(_entry_bucket(row), _outcome_label(last[row_id], row_id in recovered_ids))] += 1

    return [
        {"source": src, "target": dst, "n": n}
        for (src, dst), n in sorted(edges.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _rules_fired(rows: list[LedgerRow]) -> dict[str, int]:
    counts: Counter[str] = Counter(rule for r in rows for rule in r.rules_fired)
    labels = sorted(set(counts) | HARD_RULES)
    return {label: counts.get(label, 0) for label in labels}


def _debit_rows(rows: list[LedgerRow]) -> list[LedgerRow]:
    """Rows where a debit actually hit the rail. Deferrals and stops cost nothing."""
    return [r for r in rows if r.attempt_cost_paise > 0]


def _debits_by_hour(rows: list[LedgerRow]) -> list[int]:
    hours = [0] * 24
    for row in _debit_rows(rows):
        hours[datetime.fromisoformat(row.sim_ts).hour] += 1
    return hours


def _peak_violations(rows: list[LedgerRow]) -> int:
    """Debits that executed inside an NPCI peak window. The rule says this is always zero."""
    return sum(1 for r in _debit_rows(rows) if in_peak_window(datetime.fromisoformat(r.sim_ts)))


def _daily(rows: list[LedgerRow]) -> list[dict]:
    days = [
        {"day": d, "recovered_paise": 0, "attempts": 0, "recoveries": 0}
        for d in range(HORIZON_DAYS + 1)
    ]
    for row in rows:
        day = (datetime.fromisoformat(row.sim_ts) - SIM_START).days
        day = min(max(day, 0), HORIZON_DAYS)
        days[day]["recovered_paise"] += row.recovered_paise
        if row.attempt_cost_paise > 0:
            days[day]["attempts"] += 1
        if row.recovered_paise > 0:
            days[day]["recoveries"] += 1
    return days


def _score_histogram(rows: list[LedgerRow]) -> list[int]:
    bins = [0] * SCORE_BINS
    for row in _first_row_per_record(rows).values():
        index = min(int(row.score * SCORE_BINS), SCORE_BINS - 1)
        bins[index] += 1
    return bins
