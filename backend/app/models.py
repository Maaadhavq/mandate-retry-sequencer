"""Domain types. Separate from `schemas.py`, which is the HTTP contract.

Frozen dataclasses throughout: a record is evidence of what was true when a decision was
made, and the ledger stores a snapshot of it. Mutating one in place would silently
rewrite history (SPEC §5.1, §5.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from backend.app.policy import Action, FailureReason, MerchantCategory


@dataclass(frozen=True, slots=True)
class MandateRecord:
    """One failed UPI Autopay debit awaiting recovery. SPEC §2.1."""

    row_id: str
    failure_reason: FailureReason
    days_to_payday: int
    attempt_number: int
    ticket_size_paise: int
    merchant_category: MerchantCategory
    days_since_last_success: int
    mandate_age_days: int
    last_attempt_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.ticket_size_paise, int) or isinstance(
            self.ticket_size_paise, bool
        ):
            raise TypeError(
                f"ticket_size_paise must be int paise, got "
                f"{type(self.ticket_size_paise).__name__} (SPEC §11)"
            )
        if self.attempt_number < 1:
            raise ValueError(f"attempt_number is 1-indexed, got {self.attempt_number}")


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of evaluating one record. SPEC §3.

    `rules_fired` is never empty — there are no silent paths through the guardrails
    (SPEC §3.2). `needs_agent` marks the ambiguous band: the action carried here is a
    valid deterministic fallback that the agent may replace, subject to re-validation.
    """

    action: Action
    rules_fired: tuple[str, ...]
    reason: str
    retry_delay_hours: int | None = None
    needs_agent: bool = False
    vetoed_proposal: Action | None = field(default=None)

    def __post_init__(self) -> None:
        if not self.rules_fired:
            raise ValueError(
                f"Decision for action {self.action} has an empty rules_fired — "
                "silent paths are forbidden (SPEC §3.2)"
            )
        if self.action is Action.RETRY_SCHEDULED and self.retry_delay_hours is None:
            raise ValueError("RETRY_SCHEDULED requires a retry_delay_hours")
        if self.action is not Action.RETRY_SCHEDULED and self.retry_delay_hours is not None:
            raise ValueError(
                f"{self.action} must not carry retry_delay_hours "
                f"(got {self.retry_delay_hours})"
            )
