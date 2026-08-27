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
# These constants are graded in three tiers, because "NPCI says so" and "everyone does it"
# are different claims and conflating them is how a payments review goes badly.
#
#   TIER 1 — REGULATION. Attributed to NPCI's "Guidelines on usage of Unified Payments
#   Interface (UPI) and Application Programming Interface (API)", notified 21 May 2025,
#   effective 1 August 2025. Confirmed by multiple independent reports of that document.
#   The primary PDF was NOT read directly (npci.org.in blocks automated fetches), so this
#   is second-hand from consistent reporting, not from the circular itself.
#     - MAX_ATTEMPTS = 4  — one original execution plus three retries
#     - PEAK_WINDOWS_IST  — autopay restricted to non-peak hours, hours quoted verbatim
#
#   TIER 2 — INDUSTRY CONVENTION, not regulation. Widely used and widely described as
#   good practice; NOT mandated by NPCI. An earlier revision of this file wrongly listed
#   it as corroborated regulation, which is precisely the over-claim the tiering exists to
#   prevent.
#     - RETRY_WINDOWS_HOURS = (24, 72, 168)
#
#   TIER 3 — ASSUMPTION. Plausible, internally consistent, unverified.
#     - COOLING_PERIOD_HOURS = 24
#     - HORIZON_DAYS = 14  — a campaign design choice, not a rule at all
#
# ARCHITECTURE.md must repeat this split. Claiming regulatory precision you have not
# verified is the fastest way to lose a payments panel — and so is flagging a verifiable
# rule as a guess. Both directions are errors.
# --------------------------------------------------------------------------------------

#: TIER 1. Total debit attempts permitted per mandate cycle: 1 original + 3 retries.
#: NPCI guidelines notified 21 May 2025, effective 1 Aug 2025.
MAX_ATTEMPTS: Final[int] = 4

#: TIER 3, ASSUMPTION. Minimum hours between two debit attempts on the same mandate.
#: Not corroborated by any source located; treat as a design choice.
COOLING_PERIOD_HOURS: Final[float] = 24.0

#: TIER 2, CONVENTION not regulation. The retry ladder: a scheduled retry fires at one of
#: these offsets, never between them. Widely described as good practice — spacing retries
#: so the customer has time to top up — but NPCI does not mandate these intervals.
RETRY_WINDOWS_HOURS: Final[tuple[int, ...]] = (24, 72, 168)

#: TIER 3. Campaign horizon, a design choice rather than a rule. After this an unresolved
#: record is written off as EXPIRED.
HORIZON_DAYS: Final[int] = 14

# --------------------------------------------------------------------------------------
# Rule 5 — the NPCI execution window (SPEC §3.3).
#
# From 1 August 2025 NPCI restricts non-customer-initiated APIs, which is what a mandate
# debit is, to non-peak hours. Roughly 40% of the day is closed to autopay execution.
#
# This is the only constraint here that is checkable against a dated public source, and it
# is the one that makes the scheduler interesting: the payday interaction says when the
# money lands, this says when we are allowed to ask for it, and they routinely disagree.
# --------------------------------------------------------------------------------------

#: TIER 1. Peak windows, IST, as [start_hour, end_hour) in decimal hours. Autopay is
#: BLOCKED here. NPCI defines peak as "the period during the day when UPI financial
#: transactions reach the highest transactions per second, observed from 10:00 hrs to
#: 13:00 hrs and from 17:00 hrs to 21:30 hrs". 21.5 is 21:30.
PEAK_WINDOWS_IST: Final[tuple[tuple[float, float], ...]] = ((10.0, 13.0), (17.0, 21.5))

#: Everything else is permitted: before 10:00, 13:00-17:00, and after 21:30.

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
    #: Rule 5. Guardrail-imposed like BLOCKED_COOLING: the record is deferred to the next
    #: permitted NPCI window, never dropped, and the agent may never propose it.
    BLOCKED_PEAK_WINDOW = "BLOCKED_PEAK_WINDOW"


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
