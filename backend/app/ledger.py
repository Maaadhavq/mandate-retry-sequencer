"""F4 — the append-only ledger and the aggregate that becomes the API response.

SPEC §5.1. One row per decision, written once, never rewritten. A correction is a new row.
That rule is what makes every rupee on the dashboard traceable to a decision that actually
happened, which is the whole reason a payments panel would trust the number.

Money is integer paise throughout. `recovery_rate` and `attempts_per_recovery` are the only
floats here and neither is a currency value (CLAUDE.md).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, Iterable

from backend.app.guardrails import HARD_RULES
from backend.app.models import Decision, MandateRecord
from backend.app.policy import (
    ATTEMPT_COST_PAISE,
    HORIZON_DAYS,
    Action,
    AgentSource,
    FailureReason,
    Outcome,
    TerminalState,
)

DEFAULT_LEDGER_PATH: Final[Path] = Path("data/ledger.jsonl")

#: Outcomes that put money back. Everything else recovered nothing, by definition.
RECOVERING_OUTCOMES: Final[frozenset[Outcome]] = frozenset(
    {Outcome.RECOVERED, Outcome.PROMISE_KEPT}
)

#: Outcomes that consumed a real debit attempt, and therefore cost money to try.
BILLABLE_OUTCOMES: Final[frozenset[Outcome]] = frozenset(
    {Outcome.RECOVERED, Outcome.FAILED}
)


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One decision and what came of it. SPEC §5.1."""

    row_id: str
    sim_ts: str
    attempt_number: int
    wake_reason: str
    failure_reason: str
    merchant_category: str
    days_to_payday: int
    ticket_size_paise: int
    score: float
    rules_fired: tuple[str, ...]
    action: str
    retry_delay_hours: int | None
    agent_source: str
    agent_reasoning: str
    vetoed_proposal: str | None
    outcome: str
    amount_paise: int
    recovered_paise: int
    attempt_cost_paise: int

    def to_json(self) -> str:
        payload = asdict(self)
        payload["rules_fired"] = list(self.rules_fired)
        return json.dumps(payload, sort_keys=True)


def build_row(
    *,
    record: MandateRecord,
    decision: Decision,
    score: float,
    sim_ts: datetime,
    wake_reason: str,
    outcome: Outcome,
    recovered_paise: int,
    agent_source: AgentSource,
    agent_reasoning: str = "",
) -> LedgerRow:
    """Assemble a row from the pieces the runner holds. Pure — writes nothing."""
    return LedgerRow(
        row_id=record.row_id,
        sim_ts=sim_ts.isoformat(),
        attempt_number=record.attempt_number,
        wake_reason=wake_reason,
        failure_reason=record.failure_reason.value,
        merchant_category=record.merchant_category.value,
        days_to_payday=record.days_to_payday,
        ticket_size_paise=record.ticket_size_paise,
        score=round(float(score), 6),
        rules_fired=tuple(decision.rules_fired),
        action=decision.action.value,
        retry_delay_hours=decision.retry_delay_hours,
        agent_source=agent_source.value,
        agent_reasoning=agent_reasoning,
        vetoed_proposal=(
            decision.vetoed_proposal.value if decision.vetoed_proposal else None
        ),
        outcome=outcome.value,
        amount_paise=record.ticket_size_paise,
        recovered_paise=recovered_paise,
        attempt_cost_paise=ATTEMPT_COST_PAISE if outcome in BILLABLE_OUTCOMES else 0,
    )


class Ledger:
    """Append-only writer plus the aggregate that becomes the §7.2 response.

    Rows are held in memory as well as written, because `aggregate()` runs at the end of a
    run and re-reading the file to build the response would make the API depend on its own
    side effects. `verify_totals.py` deliberately re-reads instead — that independence is
    the point of SPEC §8.2 gate 2.
    """

    def __init__(self, path: Path | str = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(path)
        self._rows: list[LedgerRow] = []
        self._written: set[tuple[str, int, str]] = set()

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def rows(self) -> tuple[LedgerRow, ...]:
        return tuple(self._rows)

    def open(self) -> None:
        """Start a fresh ledger file for this run."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self._rows.clear()
        self._written.clear()

    def append(self, row: LedgerRow) -> None:
        """Write one row. Appending the same decision twice is a bug, so it raises."""
        key = (row.row_id, row.attempt_number, row.sim_ts)
        if key in self._written:
            raise ValueError(
                f"duplicate ledger row for {row.row_id} attempt {row.attempt_number} "
                f"at {row.sim_ts} — the ledger is append-only, not upsert"
            )
        self._written.add(key)
        self._rows.append(row)
        with self.path.open("a", encoding="utf-8", newline="") as fh:
            fh.write(row.to_json() + "\n")

    # -- aggregation --------------------------------------------------------------------

    def aggregate(
        self,
        *,
        run_id: str,
        seed: int,
        n: int,
        use_llm: bool,
        terminal_states: dict[str, TerminalState],
    ) -> dict:
        """Exactly the §7.2 shape. The frontend is built against this — do not reshape it."""
        rows = self._rows

        at_risk = _at_risk_paise(rows)
        recovered = sum(r.recovered_paise for r in rows)
        recovered_rows = [r for r in rows if r.outcome in {o.value for o in RECOVERING_OUTCOMES}]
        n_recovered = len({r.row_id for r in recovered_rows})

        attempts_per_recovery = _attempts_per_recovery(rows)

        return {
            "run_id": run_id,
            "seed": seed,
            "config": {"n": n, "horizon_days": HORIZON_DAYS, "use_llm": use_llm},
            "totals": {
                "at_risk_paise": at_risk,
                "recovered_paise": recovered,
                "recovery_rate": round(recovered / at_risk, 6) if at_risk else 0.0,
                "attempts_per_recovery": round(attempts_per_recovery, 4),
                "false_positive_cost_paise": _false_positive_cost(rows),
                "stopped_by_hard_rule": _stopped_by_hard_rule(rows),
            },
            "cohorts": {
                "by_failure_reason": _cohort(rows, "failure_reason"),
                "by_merchant_category": _cohort(rows, "merchant_category"),
            },
            "attempts_histogram": _attempts_histogram(rows),
            "promises": _promises(rows),
            "failures": _failures(rows, terminal_states),
            "agent": _agent_summary(rows),
        }


# -- aggregation helpers -----------------------------------------------------------------


def _first_row_per_record(rows: Iterable[LedgerRow]) -> dict[str, LedgerRow]:
    """One row per record, the earliest. Used wherever double-counting would inflate ₹."""
    first: dict[str, LedgerRow] = {}
    for row in rows:
        first.setdefault(row.row_id, row)
    return first


def _at_risk_paise(rows: Iterable[LedgerRow]) -> int:
    """Each record's ticket counted once, however many attempts it took.

    Summing `amount_paise` across rows would multiply a ₹500 mandate by its retry count and
    quietly inflate the headline denominator.
    """
    return sum(r.ticket_size_paise for r in _first_row_per_record(rows).values())


def _false_positive_cost(rows: Iterable[LedgerRow]) -> int:
    """₹ spent attempting debits that never recovered. SPEC §2.5 panel 4."""
    recovered_ids = {r.row_id for r in rows if r.recovered_paise > 0}
    return sum(r.attempt_cost_paise for r in rows if r.row_id not in recovered_ids)


def _stopped_by_hard_rule(rows: Iterable[LedgerRow]) -> int:
    """Records a hard rule ended. Cooling is excluded: it defers, it does not stop."""
    stopping = HARD_RULES - {"hard_cooling_period"}
    return len({r.row_id for r in rows if stopping & set(r.rules_fired)})


def _cohort(rows: Iterable[LedgerRow], key: str) -> list[dict]:
    first = _first_row_per_record(rows).values()
    at_risk: dict[str, int] = defaultdict(int)
    counts: Counter[str] = Counter()
    for row in first:
        bucket = getattr(row, key)
        at_risk[bucket] += row.ticket_size_paise
        counts[bucket] += 1

    recovered: dict[str, int] = defaultdict(int)
    for row in rows:
        recovered[getattr(row, key)] += row.recovered_paise

    return [
        {
            "key": bucket,
            "at_risk_paise": at_risk[bucket],
            "recovered_paise": recovered.get(bucket, 0),
            "n": counts[bucket],
        }
        for bucket in sorted(at_risk)
    ]


def _attempts_per_recovery(rows: Iterable[LedgerRow]) -> float:
    """Mean debit attempts spent on each record that was recovered.

    Deliberately *not* total-attempts / total-recoveries. Promise-kept recoveries cost no
    debit, so that ratio drops below 1.0 and reads as nonsense on a dashboard — the first
    real run of this pipeline reported 0.28. Averaging over recovered records answers the
    question the card actually asks: when this works, how many tries did it take?
    """
    rows = list(rows)
    debits: Counter[str] = Counter()
    for row in rows:
        if row.attempt_cost_paise > 0:
            debits[row.row_id] += 1

    recovered_ids = {r.row_id for r in rows if r.recovered_paise > 0}
    if not recovered_ids:
        return 0.0
    return sum(debits.get(row_id, 0) for row_id in recovered_ids) / len(recovered_ids)


def _attempts_histogram(rows: Iterable[LedgerRow]) -> list[dict]:
    """Real debit attempts per record, 1..MAX_ATTEMPTS. Records with none are omitted."""
    per_record: Counter[str] = Counter()
    for row in rows:
        if row.attempt_cost_paise > 0:
            per_record[row.row_id] += 1

    histogram: Counter[int] = Counter(per_record.values())
    return [
        {"attempts": attempts, "count": histogram[attempts]}
        for attempts in sorted(histogram)
    ]


def _promises(rows: Iterable[LedgerRow]) -> dict:
    made = sum(1 for r in rows if r.outcome == Outcome.PROMISED.value)
    kept = sum(1 for r in rows if r.outcome == Outcome.PROMISE_KEPT.value)
    broken = sum(1 for r in rows if r.outcome == Outcome.PROMISE_BROKEN.value)
    via_promise = sum(
        r.recovered_paise for r in rows if r.outcome == Outcome.PROMISE_KEPT.value
    )
    return {"made": made, "kept": kept, "broken": broken, "recovered_paise": via_promise}


def _failures(rows: Iterable[LedgerRow], terminal_states: dict[str, TerminalState]) -> list[dict]:
    """Every unrecovered record, biggest ₹ first. SPEC §2.5 panel 3 — never collapsed."""
    rows = list(rows)
    recovered_ids = {r.row_id for r in rows if r.recovered_paise > 0}
    last: dict[str, LedgerRow] = {}
    for row in rows:
        last[row.row_id] = row

    failures = [
        {
            "row_id": row_id,
            "amount_paise": row.ticket_size_paise,
            "stopped_by": _stopped_by_label(row, terminal_states.get(row_id)),
            "rules_fired": list(row.rules_fired),
            "score": row.score,
            "agent_reasoning": row.agent_reasoning,
        }
        for row_id, row in last.items()
        if row_id not in recovered_ids
    ]
    failures.sort(key=lambda f: (-f["amount_paise"], f["row_id"]))
    return failures


def _stopped_by_label(row: LedgerRow, terminal: TerminalState | None) -> str:
    """What actually ended this record — a named rule beats a generic terminal state."""
    for rule in row.rules_fired:
        if rule in HARD_RULES:
            return rule
    if terminal is TerminalState.EXPIRED:
        return "horizon_expired"
    if row.action == Action.STOP.value:
        return "score_below_band"
    return terminal.value if terminal else "unknown"


def _agent_summary(rows: Iterable[LedgerRow]) -> dict:
    rows = list(rows)
    sources = Counter(r.agent_source for r in rows)
    routed = len(
        {r.row_id for r in rows if r.agent_source != AgentSource.DETERMINISTIC.value}
    )
    return {
        "records_routed": routed,
        "sources": {source.value: sources.get(source.value, 0) for source in AgentSource},
    }
