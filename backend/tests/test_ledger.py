"""SPEC §8.1 F4 — the ledger, the executor, and the clock.

The ledger's job is to make every rupee on the dashboard traceable to a decision that
actually happened. So the tests here care less about arithmetic than about the properties
that keep that true: append-only, one row per decision, no double counting, and no path by
which a revoked mandate produces money.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.app.clock import SIM_START, SimClock, WakeReason
from backend.app.executor import Executor
from backend.app.ledger import Ledger, build_row
from backend.app.models import Decision, MandateRecord
from backend.app.policy import (
    ATTEMPT_COST_PAISE,
    HORIZON_DAYS,
    Action,
    AgentSource,
    FailureReason,
    MerchantCategory,
    Outcome,
    TerminalState,
)

NOW = SIM_START + timedelta(days=1)


def rec(**overrides) -> MandateRecord:
    base = dict(
        row_id="mrs_0000a1",
        failure_reason=FailureReason.INSUFFICIENT_BALANCE,
        days_to_payday=2,
        attempt_number=1,
        ticket_size_paise=99_900,
        merchant_category=MerchantCategory.SAAS,
        days_since_last_success=30,
        mandate_age_days=200,
        last_attempt_at=NOW - timedelta(hours=48),
    )
    base.update(overrides)
    return MandateRecord(**base)


def decision(action: Action = Action.RETRY_NOW, **kw) -> Decision:
    return Decision(action=action, rules_fired=("band_high",), reason="test", **kw)


def row_for(ledger_path: Path, **overrides):
    defaults = dict(
        record=rec(),
        decision=decision(),
        score=0.7,
        sim_ts=NOW,
        wake_reason=WakeReason.INITIAL.value,
        outcome=Outcome.RECOVERED,
        recovered_paise=99_900,
        agent_source=AgentSource.DETERMINISTIC,
    )
    defaults.update(overrides)
    return build_row(**defaults)


# --------------------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------------------


def test_ledger_writes_one_line_per_row(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()

    for i in range(3):
        ledger.append(row_for(tmp_path, sim_ts=NOW + timedelta(hours=i)))

    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3 == len(ledger)


def test_appending_the_same_decision_twice_raises(tmp_path: Path) -> None:
    """A duplicate row would double-count money. It must be a crash, not a warning."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    ledger.append(row_for(tmp_path))

    with pytest.raises(ValueError, match="append-only, not upsert"):
        ledger.append(row_for(tmp_path))


def test_existing_rows_are_never_rewritten(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    ledger.append(row_for(tmp_path))
    first_line = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0]

    ledger.append(row_for(tmp_path, sim_ts=NOW + timedelta(hours=5)))
    after = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()

    assert after[0] == first_line, "an existing ledger line changed"
    assert len(after) == 2


def test_rows_round_trip_through_json(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    ledger.append(row_for(tmp_path))

    parsed = json.loads((tmp_path / "ledger.jsonl").read_text(encoding="utf-8").strip())
    assert parsed["row_id"] == "mrs_0000a1"
    assert parsed["recovered_paise"] == 99_900
    assert parsed["rules_fired"] == ["band_high"]


# --------------------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------------------


def test_paise_round_trip_with_no_floating_point_drift(tmp_path: Path) -> None:
    """CLAUDE.md: a float never touches a currency value."""
    awkward = [4_900, 99_999, 1_234_567, 4_999_900, 3_333_333]
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()

    for i, amount in enumerate(awkward):
        ledger.append(
            row_for(
                tmp_path,
                record=rec(row_id=f"mrs_00{i:04x}", ticket_size_paise=amount),
                sim_ts=NOW + timedelta(hours=i),
                recovered_paise=amount,
            )
        )

    parsed = [
        json.loads(line)
        for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [p["recovered_paise"] for p in parsed] == awkward
    for p in parsed:
        assert isinstance(p["recovered_paise"], int)

    agg = ledger.aggregate(
        run_id="run_test", seed=42, n=len(awkward), use_llm=False, terminal_states={}
    )
    assert agg["totals"]["recovered_paise"] == sum(awkward)


def test_at_risk_counts_each_mandate_once_across_retries(tmp_path: Path) -> None:
    """Summing amount_paise per row would multiply a ticket by its retry count."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()

    for i in range(3):
        ledger.append(
            row_for(
                tmp_path,
                sim_ts=NOW + timedelta(hours=i),
                outcome=Outcome.FAILED,
                recovered_paise=0,
            )
        )

    agg = ledger.aggregate(
        run_id="r", seed=42, n=1, use_llm=False, terminal_states={}
    )
    assert agg["totals"]["at_risk_paise"] == 99_900, "one mandate counted three times"


def test_aggregate_total_equals_a_manual_sum(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    amounts = [0, 50_000, 0, 125_000]

    for i, amount in enumerate(amounts):
        ledger.append(
            row_for(
                tmp_path,
                record=rec(row_id=f"mrs_01{i:04x}", ticket_size_paise=200_000),
                sim_ts=NOW + timedelta(hours=i),
                outcome=Outcome.RECOVERED if amount else Outcome.FAILED,
                recovered_paise=amount,
            )
        )

    agg = ledger.aggregate(run_id="r", seed=42, n=4, use_llm=False, terminal_states={})
    assert agg["totals"]["recovered_paise"] == sum(amounts)


def test_false_positive_cost_counts_only_unrecovered_records(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    # One record: fails, then recovers. Its attempts are not wasted spend.
    ledger.append(row_for(tmp_path, outcome=Outcome.FAILED, recovered_paise=0))
    ledger.append(
        row_for(tmp_path, sim_ts=NOW + timedelta(hours=1), outcome=Outcome.RECOVERED)
    )
    # Another record that never recovers. Its one attempt is.
    ledger.append(
        row_for(
            tmp_path,
            record=rec(row_id="mrs_0000b2"),
            outcome=Outcome.FAILED,
            recovered_paise=0,
        )
    )

    agg = ledger.aggregate(run_id="r", seed=42, n=2, use_llm=False, terminal_states={})
    assert agg["totals"]["false_positive_cost_paise"] == ATTEMPT_COST_PAISE


def test_attempts_per_recovery_is_never_below_one(tmp_path: Path) -> None:
    """Guards both regressions: the 0.28 bug and the 0.99 one.

    A card labelled "attempts per recovery" must never read below 1.0 — fewer than one
    attempt per recovery is not something a payments team can act on. Promise-kept
    recoveries cost no debit and must not be folded into this denominator.
    """
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    # Recovered by debit on the first try.
    ledger.append(row_for(tmp_path, outcome=Outcome.RECOVERED))
    # Recovered by a promise, no debit ever attempted. Must not drag the mean down.
    ledger.append(
        row_for(
            tmp_path,
            record=rec(row_id="mrs_0000c3"),
            outcome=Outcome.PROMISE_KEPT,
            recovered_paise=99_900,
        )
    )

    agg = ledger.aggregate(run_id="r", seed=42, n=2, use_llm=False, terminal_states={})
    assert agg["totals"]["attempts_per_recovery"] == 1.0


def test_attempts_per_recovery_counts_retries(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    ledger.append(row_for(tmp_path, outcome=Outcome.FAILED, recovered_paise=0))
    ledger.append(
        row_for(tmp_path, sim_ts=NOW + timedelta(hours=24), outcome=Outcome.FAILED, recovered_paise=0)
    )
    ledger.append(
        row_for(tmp_path, sim_ts=NOW + timedelta(hours=48), outcome=Outcome.RECOVERED)
    )

    agg = ledger.aggregate(run_id="r", seed=42, n=1, use_llm=False, terminal_states={})
    assert agg["totals"]["attempts_per_recovery"] == 3.0


def test_attempts_per_recovery_is_zero_when_nothing_recovered(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.open()
    ledger.append(row_for(tmp_path, outcome=Outcome.FAILED, recovered_paise=0))

    agg = ledger.aggregate(run_id="r", seed=42, n=1, use_llm=False, terminal_states={})
    assert agg["totals"]["attempts_per_recovery"] == 0.0


# --------------------------------------------------------------------------------------
# Executor
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def executor() -> Executor:
    truth_path = Path(__file__).resolve().parents[2] / "data" / "batch_truth.json"
    if not truth_path.exists():
        pytest.skip("generate the batch first (see CLAUDE.md)")
    return Executor.load(truth_path, seed=42)


def test_executor_never_recovers_a_revoked_mandate(executor: Executor) -> None:
    """SPEC §5.2. Tried across many delays and attempt numbers, not just the happy path."""
    for attempt in range(1, 5):
        for delay in (0.0, 24.0, 72.0, 168.0, 999.0):
            record = rec(
                row_id=next(iter(executor._rows)),
                failure_reason=FailureReason.REVOKED_MANDATE,
                attempt_number=attempt,
            )
            result = executor.attempt_debit(record, delay_hours=delay)
            assert result.outcome is Outcome.FAILED
            assert result.recovered_paise == 0
            assert result.probability == 0.0


def test_revoked_mandate_cannot_recover_via_a_promise(executor: Executor) -> None:
    """The promise path is a separate code path and needs its own guarantee."""
    record = rec(
        row_id=next(iter(executor._rows)),
        failure_reason=FailureReason.REVOKED_MANDATE,
    )
    result = executor.resolve_promise(record, delay_hours=48.0)

    assert result.outcome is Outcome.PROMISE_BROKEN
    assert result.recovered_paise == 0


def test_executor_never_mutates_the_input_record(executor: Executor) -> None:
    row_id = next(iter(executor._rows))
    record = rec(row_id=row_id)
    before = (record.attempt_number, record.ticket_size_paise, record.last_attempt_at)

    executor.attempt_debit(record, delay_hours=24.0)
    executor.resolve_promise(record, delay_hours=24.0)
    executor.capture_promise(record)

    assert (record.attempt_number, record.ticket_size_paise, record.last_attempt_at) == before


def test_executor_outcomes_do_not_depend_on_call_order(executor: Executor) -> None:
    """Per-attempt streams: a reordered tick must not change any record's outcome."""
    ids = list(executor._rows)[:12]
    records = [rec(row_id=i) for i in ids]

    forward = [executor.attempt_debit(r, delay_hours=24.0).outcome for r in records]
    backward = [
        executor.attempt_debit(r, delay_hours=24.0).outcome for r in reversed(records)
    ]

    assert forward == list(reversed(backward))


def test_promise_due_date_is_inside_the_spec_window(executor: Executor) -> None:
    from backend.app.policy import PROMISE_DUE_DAYS_MAX, PROMISE_DUE_DAYS_MIN

    for row_id in list(executor._rows)[:50]:
        _, due_days = executor.capture_promise(rec(row_id=row_id))
        assert PROMISE_DUE_DAYS_MIN <= due_days <= PROMISE_DUE_DAYS_MAX


def test_unknown_row_id_gives_an_actionable_error(executor: Executor) -> None:
    with pytest.raises(KeyError, match="out of sync"):
        executor.attempt_debit(rec(row_id="mrs_ffffff"), delay_hours=24.0)


# --------------------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------------------


def test_clock_wakes_records_in_time_order() -> None:
    clock = SimClock()
    clock.schedule_in("mrs_000003", 72, WakeReason.SCHEDULED_RETRY)
    clock.schedule_in("mrs_000001", 24, WakeReason.COOLING_EXPIRED)
    clock.schedule_in("mrs_000002", 48, WakeReason.SCHEDULED_RETRY)

    woken = [w.row_id for _, due in clock.run() for w in due]
    assert woken == ["mrs_000001", "mrs_000002", "mrs_000003"]


def test_ties_within_a_tick_resolve_by_row_id() -> None:
    """Determinism: heap order on equal timestamps is otherwise unspecified."""
    clock = SimClock()
    for row_id in ("mrs_00000c", "mrs_00000a", "mrs_00000b"):
        clock.schedule_in(row_id, 24, WakeReason.COOLING_EXPIRED)

    _, due = next(iter(clock.run()))
    assert [w.row_id for w in due] == ["mrs_00000a", "mrs_00000b", "mrs_00000c"]


def test_a_wake_past_the_horizon_fires_once_in_the_sweep() -> None:
    """The horizon sweep. A record past the horizon is woken exactly once, at HORIZON_END.

    It used to be dropped silently, which made guardrail rule 4 unreachable from the
    pipeline and left expired records with no ledger row saying why they stopped. Running
    out of campaign is a decision, and SPEC §5.1 wants a row for every decision.
    """
    clock = SimClock()
    clock.schedule_in("mrs_000001", HORIZON_DAYS * 24 + 48, WakeReason.SCHEDULED_RETRY)

    ticks = list(clock.run())

    assert len(ticks) == 1, "the sweep should fire exactly once"
    swept_at, due = ticks[0]
    assert swept_at == clock.end
    assert [w.row_id for w in due] == ["mrs_000001"]


def test_the_sweep_does_not_wake_already_terminal_records() -> None:
    clock = SimClock()
    clock.schedule_in("mrs_000001", HORIZON_DAYS * 24 + 48, WakeReason.SCHEDULED_RETRY)
    clock.schedule_in("mrs_000002", HORIZON_DAYS * 24 + 48, WakeReason.SCHEDULED_RETRY)
    clock.finish("mrs_000001", TerminalState.RECOVERED)

    ticks = list(clock.run())
    assert [w.row_id for _, due in ticks for w in due] == ["mrs_000002"]


def test_records_left_after_the_sweep_are_expired() -> None:
    clock = SimClock()
    clock.schedule_in("mrs_000001", HORIZON_DAYS * 24 + 48, WakeReason.SCHEDULED_RETRY)

    for _, due in clock.run():
        pass  # caller declines to terminate them
    clock.expire_unfinished({"mrs_000001"})

    assert clock.terminal_states["mrs_000001"] is TerminalState.EXPIRED


def test_terminal_records_are_not_woken_again() -> None:
    clock = SimClock()
    clock.schedule_in("mrs_000001", 24, WakeReason.COOLING_EXPIRED)
    clock.schedule_in("mrs_000002", 24, WakeReason.COOLING_EXPIRED)
    clock.finish("mrs_000001", TerminalState.RECOVERED)

    woken = [w.row_id for _, due in clock.run() for w in due]
    assert woken == ["mrs_000002"]


def test_scheduling_a_terminal_record_raises() -> None:
    clock = SimClock()
    clock.finish("mrs_000001", TerminalState.WRITTEN_OFF)

    with pytest.raises(ValueError, match="already terminal"):
        clock.schedule_in("mrs_000001", 24, WakeReason.COOLING_EXPIRED)
