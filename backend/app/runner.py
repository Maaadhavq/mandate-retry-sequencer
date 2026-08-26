"""F6 — the batch runner. SPEC §5.3, §10.2 Gate B.

One pass of the campaign: wake due records → guardrails → (agent, when ambiguous) →
re-validate → execute → ledger. The clock decides *when*, `guardrails` decides *whether*,
`executor` decides *what happened*, and `ledger` records it. This module owns the wiring
and nothing else, which is what keeps the veto path honest — the runner cannot execute an
action that did not come back out of `validate_proposal`.

The agent is optional by construction. With `use_llm=False` — or with no decider wired in
at all, which is the state at Gate B — the ambiguous band falls through to
`decide_fallback` and the whole pipeline still closes on a real rupee figure. That is the
point of the layering: the agent is an upgrade to a working system, never a dependency of
one (SPEC §10.3).
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Final, Protocol

from backend.app import guardrails
from backend.app.clock import SIM_START, SimClock, WakeReason
from backend.app.executor import Executor
from backend.app.ledger import Ledger, build_row
from backend.app.models import Decision, MandateRecord
from backend.app.policy import (
    COOLING_PERIOD_HOURS,
    MAX_ATTEMPTS,
    Action,
    AgentSource,
    FailureReason,
    MerchantCategory,
    Outcome,
    TerminalState,
)
from backend.app.scorer import Scorer

DEFAULT_BATCH_PATH: Final[Path] = Path("data/batch.csv")


class Decider(Protocol):
    """What F5 will provide. Absent at Gate B; the fallback covers the same surface."""

    def decide(
        self, record: MandateRecord, score: float, now: datetime
    ) -> tuple[Action, int | None, str, AgentSource]:
        ...


def load_batch(path: Path | str = DEFAULT_BATCH_PATH) -> list[MandateRecord]:
    """Read the operational batch into frozen domain records."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Generate the batch first (see README.md): "
            ".venv/Scripts/python -m backend.scripts.generate_data "
            "--seed 42 --n 500 --name batch"
        )
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    return [
        MandateRecord(
            row_id=r["row_id"],
            failure_reason=FailureReason(r["failure_reason"]),
            days_to_payday=int(r["days_to_payday"]),
            attempt_number=int(r["attempt_number"]),
            ticket_size_paise=int(r["ticket_size_paise"]),
            merchant_category=MerchantCategory(r["merchant_category"]),
            days_since_last_success=int(r["days_since_last_success"]),
            mandate_age_days=int(r["mandate_age_days"]),
            last_attempt_at=datetime.fromisoformat(r["last_attempt_at"]),
        )
        for r in rows
    ]


class BatchRunner:
    """Runs one campaign to completion and returns the §7.2 response."""

    def __init__(
        self,
        *,
        records: list[MandateRecord],
        scorer: Scorer,
        executor: Executor,
        ledger: Ledger,
        seed: int,
        decider: Decider | None = None,
    ) -> None:
        self.seed = seed
        self.scorer = scorer
        self.executor = executor
        self.ledger = ledger
        self.decider = decider

        # Scores are computed once, up front, from the record as it arrived. Re-scoring
        # after each attempt would let the pipeline's own actions feed back into the
        # model's input, which is not something the scorer was trained for.
        self._scores: dict[str, float] = dict(
            zip(
                (r.row_id for r in records),
                (float(s) for s in scorer.score_records(records)),
            )
        )
        self._state: dict[str, MandateRecord] = {r.row_id: r for r in records}
        self._promised: dict[str, int] = {}
        #: Who authorised the retry currently booked for each row — so that if a hard
        #: rule vetoes it when the window arrives, the ledger says whose proposal died.
        self._booked_by: dict[str, AgentSource] = {}

    # -- the loop -----------------------------------------------------------------------

    def run(self, *, use_llm: bool = False) -> dict:
        clock = SimClock()
        self.ledger.open()

        for record in self._state.values():
            clock.schedule(record.row_id, SIM_START, WakeReason.INITIAL)

        for now, due in clock.run():
            for wake in due:
                self._handle(clock, wake.row_id, wake.reason, now, use_llm=use_llm)

        clock.expire_unfinished(set(self._state))

        return self.ledger.aggregate(
            run_id=f"run_{uuid.uuid4().hex[:8]}",
            seed=self.seed,
            n=len(self._state),
            use_llm=use_llm,
            terminal_states=clock.terminal_states,
        )

    def _handle(
        self,
        clock: SimClock,
        row_id: str,
        reason: WakeReason,
        now: datetime,
        *,
        use_llm: bool,
    ) -> None:
        record = self._state[row_id]
        score = self._scores[row_id]

        if reason is WakeReason.PROMISE_DUE:
            self._settle_promise(clock, record, score, now)
            return

        if reason is WakeReason.SCHEDULED_RETRY:
            # A booked retry fires; it is not re-decided. Re-running the bands here would
            # produce the same RETRY_SCHEDULED from the same unchanged score and the record
            # would reschedule itself until the horizon, never attempting anything — which
            # is exactly what the first run of this pipeline did (12 debits across 500
            # records, attempts_per_recovery 0.28). Hard rules still apply: `evaluate`
            # returns a hard-rule Decision when one fires, and only a clear pass converts
            # into the debit that was already authorised.
            hard = guardrails.evaluate(record, score, now)
            if set(hard.rules_fired) & guardrails.HARD_RULES:
                # The retry now sitting in this window was authorised earlier — by the
                # agent, if it was the agent that booked it. A hard rule that has become
                # true since is vetoing that proposal, so it is recorded as a veto rather
                # than as a plain block. This is SPEC §8.2 gate 4 arising naturally:
                # nothing is seeded, the record simply ran out of attempts or horizon
                # between being scheduled and coming due.
                vetoed = guardrails.validate_proposal(
                    record, score, now, Action.RETRY_NOW, None
                )
                source = self._booked_by.get(record.row_id, AgentSource.DETERMINISTIC)
                self._apply(clock, record, vetoed, score, now, reason, source, "")
                return
            fire = Decision(
                action=Action.RETRY_NOW,
                rules_fired=("scheduled_retry_fired",),
                reason="the booked retry window came due",
            )
            self._debit(clock, record, fire, score, now, reason, AgentSource.DETERMINISTIC, "")
            return

        decision = guardrails.evaluate(record, score, now)
        source = AgentSource.DETERMINISTIC
        reasoning = ""

        if decision.needs_agent and self.decider is not None and use_llm:
            action, delay, reasoning, source = self.decider.decide(record, score, now)
            # Every proposal goes back through the guardrails. A hard rule that fired
            # before the agent spoke still fires after it (SPEC §3, §8.2 gate 4).
            decision = guardrails.validate_proposal(record, score, now, action, delay)

        self._apply(clock, record, decision, score, now, reason, source, reasoning)

    # -- action dispatch ----------------------------------------------------------------

    def _apply(
        self,
        clock: SimClock,
        record: MandateRecord,
        decision: Decision,
        score: float,
        now: datetime,
        reason: WakeReason,
        source: AgentSource,
        reasoning: str,
    ) -> None:
        action = decision.action

        if action is Action.STOP:
            self._write(record, decision, score, now, reason, Outcome.NOT_ATTEMPTED, 0, source, reasoning)
            # Running out of campaign is not the same as being written off on the merits,
            # and the dashboard distinguishes them.
            expired = "hard_horizon_exhausted" in decision.rules_fired
            clock.finish(
                record.row_id,
                TerminalState.EXPIRED if expired else TerminalState.WRITTEN_OFF,
            )
            return

        if action is Action.BLOCKED_COOLING:
            self._write(record, decision, score, now, reason, Outcome.NOT_ATTEMPTED, 0, source, reasoning)
            wait = COOLING_PERIOD_HOURS - guardrails.hours_since_last_attempt(record, now)
            clock.schedule_in(record.row_id, max(wait, 1.0), WakeReason.COOLING_EXPIRED)
            return

        if action is Action.RETRY_SCHEDULED:
            # A scheduled retry consumes no attempt now; it books one for later.
            self._write(record, decision, score, now, reason, Outcome.NOT_ATTEMPTED, 0, source, reasoning)
            self._booked_by[record.row_id] = source
            clock.schedule_in(
                record.row_id, float(decision.retry_delay_hours), WakeReason.SCHEDULED_RETRY
            )
            return

        if action is Action.DUNNING_P2P:
            amount, due_days = self.executor.capture_promise(record)
            self._promised[record.row_id] = amount
            self._write(record, decision, score, now, reason, Outcome.PROMISED, 0, source, reasoning)
            clock.schedule_in(record.row_id, due_days * 24.0, WakeReason.PROMISE_DUE)
            return

        if action is Action.RETRY_NOW:
            self._debit(clock, record, decision, score, now, reason, source, reasoning)
            return

        raise AssertionError(f"unhandled action {action} for {record.row_id}")

    def _debit(
        self,
        clock: SimClock,
        record: MandateRecord,
        decision: Decision,
        score: float,
        now: datetime,
        reason: WakeReason,
        source: AgentSource,
        reasoning: str,
    ) -> None:
        delay = (now - record.last_attempt_at).total_seconds() / 3600.0
        attempt = self.executor.attempt_debit(record, delay_hours=delay)
        self._write(
            record, decision, score, now, reason,
            attempt.outcome, attempt.recovered_paise, source, reasoning,
        )

        if attempt.outcome is Outcome.RECOVERED:
            clock.finish(record.row_id, TerminalState.RECOVERED)
            return

        self._advance_attempt(clock, record, now)

    def _settle_promise(
        self, clock: SimClock, record: MandateRecord, score: float, now: datetime
    ) -> None:
        delay = (now - record.last_attempt_at).total_seconds() / 3600.0
        attempt = self.executor.resolve_promise(record, delay_hours=delay)
        decision = Decision(
            action=Action.DUNNING_P2P,
            rules_fired=("promise_resolved",),
            reason="promise came due",
        )
        self._write(
            record, decision, score, now, WakeReason.PROMISE_DUE,
            attempt.outcome, attempt.recovered_paise, AgentSource.DETERMINISTIC, "",
        )

        if attempt.outcome is Outcome.PROMISE_KEPT:
            clock.finish(record.row_id, TerminalState.RECOVERED)
            return

        # SPEC §5.4: a broken promise re-enters the pipeline with attempt_number + 1, so
        # it cannot be used to walk around the attempt cap.
        self._advance_attempt(clock, record, now)

    def _advance_attempt(
        self, clock: SimClock, record: MandateRecord, now: datetime
    ) -> None:
        """Book the next attempt, or write the record off if it has run out."""
        if record.attempt_number >= MAX_ATTEMPTS:
            clock.finish(record.row_id, TerminalState.WRITTEN_OFF)
            return

        self._state[record.row_id] = replace(
            record, attempt_number=record.attempt_number + 1, last_attempt_at=now
        )
        clock.schedule_in(
            record.row_id, COOLING_PERIOD_HOURS, WakeReason.COOLING_EXPIRED
        )

    def _write(
        self,
        record: MandateRecord,
        decision: Decision,
        score: float,
        now: datetime,
        reason: WakeReason,
        outcome: Outcome,
        recovered_paise: int,
        source: AgentSource,
        reasoning: str,
    ) -> None:
        self.ledger.append(
            build_row(
                record=record,
                decision=decision,
                score=score,
                sim_ts=now,
                wake_reason=reason.value,
                outcome=outcome,
                recovered_paise=recovered_paise,
                agent_source=source,
                agent_reasoning=reasoning,
            )
        )


def run_campaign(
    *,
    seed: int,
    n: int | None = None,
    use_llm: bool = False,
    batch_path: Path | str = DEFAULT_BATCH_PATH,
    ledger_path: Path | str | None = None,
    decider: Decider | None = None,
) -> dict:
    """Load everything, run one campaign, return the §7.2 response."""
    records = load_batch(batch_path)
    if n is not None:
        records = records[:n]

    if decider is None and use_llm:
        # Built here rather than at import time so the module never needs a key. With no
        # key and no cache entry this still resolves to the deterministic fallback — the
        # agent is an upgrade, not a dependency (SPEC §10.3).
        from backend.app.decider import Decider as LiveDecider

        decider = LiveDecider()

    ledger = Ledger(ledger_path) if ledger_path else Ledger()
    runner = BatchRunner(
        records=records,
        scorer=Scorer.load(),
        executor=Executor.load(seed=seed),
        ledger=ledger,
        seed=seed,
        decider=decider,
    )
    return runner.run(use_llm=use_llm)
