"""F4 — the 14-day simulation clock. SPEC §5.3.

Why a clock at all: without one, "attempts per recovery" is not a distribution and the
cooling-period guardrail is an assertion rather than demonstrated behaviour. A record that
is blocked at hour 3 has to actually come back at hour 27 and succeed or fail on its own
merits, or rule 3 is just a branch nobody ever walks.

The clock owns *when*, and nothing else. It does not decide, execute, or record — those
belong to the guardrails, the executor, and the ledger respectively. It hands the runner a
list of row_ids that are due at each tick and takes back a time to wake each one again.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Iterator

from backend.app.policy import HORIZON_DAYS, SIM_START_ISO, TICK_HOURS, TerminalState

SIM_START = datetime.fromisoformat(SIM_START_ISO)
HORIZON_END = SIM_START + timedelta(days=HORIZON_DAYS)


class WakeReason(StrEnum):
    """Why a record is being woken. Carried onto the ledger row for traceability."""

    INITIAL = "initial"
    COOLING_EXPIRED = "cooling_expired"
    SCHEDULED_RETRY = "scheduled_retry"
    PROMISE_DUE = "promise_due"


@dataclass(order=True)
class _Wake:
    """Heap entry. Ordered by time, then row_id so ties resolve deterministically."""

    at: datetime
    row_id: str = field(compare=True)
    reason: WakeReason = field(compare=False, default=WakeReason.INITIAL)


class SimClock:
    """Steps `HORIZON_DAYS` in `TICK_HOURS` ticks, waking records when they come due.

    Ties within a tick are broken by row_id rather than by insertion order. Two runs of
    the same seed must produce the same ledger, and heap order on equal timestamps is not
    otherwise stable.
    """

    def __init__(self, *, start: datetime = SIM_START, end: datetime = HORIZON_END) -> None:
        self.start = start
        self.end = end
        self.now = start
        self._heap: list[_Wake] = []
        self._terminal: dict[str, TerminalState] = {}

    # -- scheduling ---------------------------------------------------------------------

    def schedule(self, row_id: str, at: datetime, reason: WakeReason) -> None:
        """Wake `row_id` at `at`. Past times are clamped to now, never dropped silently."""
        if row_id in self._terminal:
            raise ValueError(f"{row_id} is already terminal ({self._terminal[row_id]})")
        heapq.heappush(self._heap, _Wake(max(at, self.now), row_id, reason))

    def schedule_in(self, row_id: str, hours: float, reason: WakeReason) -> None:
        self.schedule(row_id, self.now + timedelta(hours=hours), reason)

    def finish(self, row_id: str, state: TerminalState) -> None:
        self._terminal[row_id] = state

    # -- state --------------------------------------------------------------------------

    def is_terminal(self, row_id: str) -> bool:
        return row_id in self._terminal

    @property
    def terminal_states(self) -> dict[str, TerminalState]:
        return dict(self._terminal)

    @property
    def pending(self) -> set[str]:
        return {w.row_id for w in self._heap if w.row_id not in self._terminal}

    # -- iteration ----------------------------------------------------------------------

    def run(self) -> Iterator[tuple[datetime, list[_Wake]]]:
        """Yield `(tick_time, due)` for each tick that has work.

        Ticks with nothing due are skipped rather than yielded — a 336-tick loop that does
        nothing 300 times is just noise in the trace. The clock still advances to the tick
        boundary, so cooling maths stays on the hour.
        """
        tick = timedelta(hours=TICK_HOURS)

        while self._heap and self.now < self.end:
            next_at = self._heap[0].at
            # Advance to the tick boundary at or after the next wake.
            if next_at > self.now:
                elapsed_ticks = -(-(next_at - self.start) // tick)  # ceil division
                self.now = min(self.start + elapsed_ticks * tick, self.end)
            if self.now >= self.end:
                break

            due: list[_Wake] = []
            while self._heap and self._heap[0].at <= self.now:
                wake = heapq.heappop(self._heap)
                if wake.row_id not in self._terminal:
                    due.append(wake)
            if due:
                due.sort(key=lambda w: w.row_id)
                yield self.now, due

        # Anything still queued when the horizon closes has run out of campaign.
        self.now = self.end
        for wake in self._heap:
            self._terminal.setdefault(wake.row_id, TerminalState.EXPIRED)
        self._heap.clear()
