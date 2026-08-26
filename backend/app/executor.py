"""F4 — the simulated payment rail. SPEC §5.2.

Two constraints define this module:

- It **never mutates the input record** and **never writes to the ledger**. The runner owns
  both. An executor that could edit history would make the audit trail worthless.
- A `revoked_mandate` never returns a recovery under any code path. The ground-truth
  function already returns exactly 0.0 for those, but that is a probability, and a
  probability is the wrong kind of thing to rest a compliance claim on. There is an
  explicit guard as well, and a test that reaches past the guard to prove it is load-bearing.

Randomness is drawn from a per-attempt stream keyed on `row_id` and `attempt_number`, not
from one shared generator. Outcomes then do not depend on the order the runner happens to
walk the batch, which is what keeps a reordered tick from silently changing the totals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from backend.app.models import MandateRecord
from backend.app.policy import (
    PROMISE_DUE_DAYS_MAX,
    PROMISE_DUE_DAYS_MIN,
    FailureReason,
    Outcome,
)
from backend.scripts.generate_data import recovery_probability

DEFAULT_TRUTH_PATH: Final[Path] = Path("data/batch_truth.json")

#: A captured promise is kept this often, before the mandate's own recovery odds apply.
#: Promises are softer than debits — the customer has said yes, but saying yes is not paying.
PROMISE_KEPT_BASE: Final[float] = 0.62


@dataclass(frozen=True, slots=True)
class Attempt:
    """What the rail did. Pure data — the runner turns this into a ledger row."""

    outcome: Outcome
    recovered_paise: int
    probability: float
    delay_hours: float


class Executor:
    """Samples outcomes against the same hidden function the labels came from.

    Holding the truth sidecar here — rather than passing probabilities in — keeps the
    hidden state (`edtech_off_cycle`) out of every other module's reach. Nothing upstream
    can accidentally start conditioning on it.
    """

    def __init__(self, truth: dict, *, seed: int) -> None:
        self._rows: dict[str, dict] = truth["rows"]
        self._seed = seed

    @classmethod
    def load(cls, path: Path | str = DEFAULT_TRUTH_PATH, *, seed: int) -> "Executor":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing — generate the batch first (see CLAUDE.md)."
            )
        return cls(json.loads(path.read_text(encoding="utf-8")), seed=seed)

    # -- internals ----------------------------------------------------------------------

    def _rng(self, row_id: str, attempt_number: int, salt: int = 0) -> np.random.Generator:
        """Stable per-attempt stream.

        `row_id` is `mrs_<6 hex>`, so the hex tail is a stable integer. Python's built-in
        `hash` is salted per process and would make runs irreproducible across restarts.
        """
        row_key = int(row_id[4:], 16)
        return np.random.default_rng((self._seed, row_key, attempt_number, salt))

    def _hidden(self, row_id: str) -> dict:
        try:
            return self._rows[row_id]
        except KeyError:
            raise KeyError(
                f"{row_id} has no ground-truth entry. The batch CSV and its truth sidecar "
                "are out of sync — regenerate both from the same seed."
            ) from None

    def probability(self, record: MandateRecord, delay_hours: float) -> float:
        """P(recover) at the delay actually realised, before any sampling."""
        if record.failure_reason is FailureReason.REVOKED_MANDATE:
            return 0.0
        hidden = self._hidden(record.row_id)
        return recovery_probability(
            {
                "failure_reason": record.failure_reason.value,
                "merchant_category": record.merchant_category.value,
                "days_to_payday": record.days_to_payday,
                "attempt_number": record.attempt_number,
                "ticket_size_paise": record.ticket_size_paise,
                "days_since_last_success": record.days_since_last_success,
                "mandate_age_days": record.mandate_age_days,
            },
            edtech_off_cycle=hidden.get("edtech_off_cycle"),
            delay_hours=delay_hours,
        )

    # -- the rail -----------------------------------------------------------------------

    def attempt_debit(self, record: MandateRecord, *, delay_hours: float) -> Attempt:
        """Run one debit. Never mutates `record`."""
        if record.failure_reason is FailureReason.REVOKED_MANDATE:
            # Guard, not an optimisation. See the module docstring.
            return Attempt(Outcome.FAILED, 0, 0.0, delay_hours)

        p = self.probability(record, delay_hours)
        rng = self._rng(record.row_id, record.attempt_number)
        if rng.random() < p:
            return Attempt(Outcome.RECOVERED, record.ticket_size_paise, p, delay_hours)
        return Attempt(Outcome.FAILED, 0, p, delay_hours)

    def capture_promise(self, record: MandateRecord) -> tuple[int, int]:
        """Capture a promise-to-pay. Returns `(promised_amount_paise, due_in_days)`.

        The full ticket is promised — a partial-payment model is out of scope (SPEC §5.4),
        and pretending otherwise would put a number on the dashboard nothing supports.
        """
        rng = self._rng(record.row_id, record.attempt_number, salt=1)
        due_days = int(rng.integers(PROMISE_DUE_DAYS_MIN, PROMISE_DUE_DAYS_MAX + 1))
        return record.ticket_size_paise, due_days

    def resolve_promise(self, record: MandateRecord, *, delay_hours: float) -> Attempt:
        """Settle a promise that has come due.

        Kept-ness is the promise base scaled by the mandate's own odds at this delay, so a
        promise on a hopeless mandate is still mostly hopeless. A revoked mandate returns
        `PROMISE_BROKEN` — it cannot pay, whatever anyone agreed to.
        """
        if record.failure_reason is FailureReason.REVOKED_MANDATE:
            return Attempt(Outcome.PROMISE_BROKEN, 0, 0.0, delay_hours)

        p_recover = self.probability(record, delay_hours)
        p_kept = PROMISE_KEPT_BASE * (0.45 + 0.55 * p_recover)
        rng = self._rng(record.row_id, record.attempt_number, salt=2)
        if rng.random() < p_kept:
            return Attempt(
                Outcome.PROMISE_KEPT, record.ticket_size_paise, p_kept, delay_hours
            )
        return Attempt(Outcome.PROMISE_BROKEN, 0, p_kept, delay_hours)
