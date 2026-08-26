"""Single source of truth for every policy constant in the system.

SPEC §3.3: the NPCI-derived constants below are the *only* place these numbers appear.
Nothing else in the codebase may hardcode an attempt cap, a cooling period, or a retry window.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# --------------------------------------------------------------------------------------
# Provenance — read this before citing any of it as a regulatory fact.
#
# The four constants in this block are derived from public industry summaries of NPCI
# UPI Autopay / e-mandate behaviour. They were NOT read out of the primary NPCI circular.
# They are plausible, they are internally consistent, and they are ASSUMPTIONS.
#
# ARCHITECTURE.md must repeat this disclosure. Claiming regulatory precision that has not
# been verified against the source document is the fastest way to lose a payments panel.
# --------------------------------------------------------------------------------------

#: Total debit attempts permitted per mandate cycle: 1 original + 3 retries.
MAX_ATTEMPTS: Final[int] = 4

#: Minimum hours between two debit attempts on the same mandate.
COOLING_PERIOD_HOURS: Final[float] = 24.0

#: The retry ladder. A scheduled retry fires at one of these offsets, never between them.
RETRY_WINDOWS_HOURS: Final[tuple[int, ...]] = (24, 72, 168)

#: Campaign horizon. After this, an unresolved record is written off as EXPIRED.
HORIZON_DAYS: Final[int] = 14

# --------------------------------------------------------------------------------------
# Score bands (SPEC §2.3). Exhaustive and non-overlapping across [0.0, 1.0].
# --------------------------------------------------------------------------------------

#: At or above this, retry immediately without consulting the agent.
BAND_HIGH: Final[float] = 0.65

#: Below this, stop. Not worth the cost of an attempt.
BAND_LOW: Final[float] = 0.15

#: The agent's call surface: BAND_LOW <= score < BAND_HIGH.
BAND_AGENT_MIN: Final[float] = BAND_LOW
BAND_AGENT_MAX: Final[float] = BAND_HIGH

#: Reported precision/recall cut points (SPEC §8.1 F2). The middle cut splits the
#: ambiguous band, which is why it is reported separately from the two band edges.
THRESHOLD_CUTS: Final[tuple[float, ...]] = (0.65, 0.35, 0.15)

# --------------------------------------------------------------------------------------
# Simulation and cost
# --------------------------------------------------------------------------------------

#: Fixed start of the simulated campaign. Everything is relative to this, so runs at
#: different wall-clock times produce identical ledgers.
SIM_START_ISO: Final[str] = "2026-09-01T00:00:00+05:30"

#: Clock granularity.
TICK_HOURS: Final[int] = 1

#: Cost charged per debit attempt, in paise. Drives the false-positive cost panel:
#: money spent retrying payments that were never recoverable.
ATTEMPT_COST_PAISE: Final[int] = 250

#: A promise-to-pay is captured with a due date this many days out.
PROMISE_DUE_DAYS_MIN: Final[int] = 2
PROMISE_DUE_DAYS_MAX: Final[int] = 7

# --------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------


class FailureReason(StrEnum):
    INSUFFICIENT_BALANCE = "insufficient_balance"
    TECHNICAL_DECLINE = "technical_decline"
    REVOKED_MANDATE = "revoked_mandate"


class MerchantCategory(StrEnum):
    SAAS = "saas"
    EDTECH = "edtech"
    OTT = "ott"
    FITNESS = "fitness"
    UTILITIES = "utilities"


class Action(StrEnum):
    RETRY_NOW = "RETRY_NOW"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    DUNNING_P2P = "DUNNING_P2P"
    STOP = "STOP"
    BLOCKED_COOLING = "BLOCKED_COOLING"


class Outcome(StrEnum):
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"
    PROMISED = "PROMISED"
    PROMISE_KEPT = "PROMISE_KEPT"
    PROMISE_BROKEN = "PROMISE_BROKEN"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


class TerminalState(StrEnum):
    RECOVERED = "RECOVERED"
    WRITTEN_OFF = "WRITTEN_OFF"
    EXPIRED = "EXPIRED"


class AgentSource(StrEnum):
    """How a decision was reached. Every ledger row carries one (SPEC §4.3)."""

    LIVE = "live"           # a real API call
    CACHE = "cache"         # replayed from the committed response cache
    FALLBACK = "fallback"   # deterministic policy stood in for the agent
    DETERMINISTIC = "deterministic"  # agent was never consulted; rules or bands decided


#: Actions the agent is permitted to propose. BLOCKED_COOLING is guardrail-imposed and
#: can never be chosen; a proposal containing it is invalid and triggers the fallback.
AGENT_PROPOSABLE: Final[frozenset[Action]] = frozenset(
    {Action.RETRY_NOW, Action.RETRY_SCHEDULED, Action.DUNNING_P2P, Action.STOP}
)

#: Bumped whenever the agent's prompt or schema changes. Part of the cache key, so a
#: policy edit invalidates stale cached decisions instead of silently replaying them.
POLICY_VERSION: Final[str] = "v1"
