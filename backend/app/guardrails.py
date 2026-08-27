"""Hard compliance rules. SPEC §3.

Pure functions: no I/O, no model import, no network, no clock of their own. Everything
time-dependent arrives as the `now` argument, so the same inputs always produce the same
`Decision` — which is what makes the pipeline reproducible (SPEC §3.2).

The precedence is the whole point of this module. Rules 1-4 are absolute: no score, and
no agent proposal, may override them. `validate_proposal` is what enforces that against
the agent — an agent proposal is a request, never an authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.app import policy
from backend.app.models import Decision, MandateRecord
from backend.app.policy import Action, FailureReason

#: The campaign closes at a fixed simulated instant, so "past the horizon" is a pure
#: function of `now` rather than of wall-clock time.
SIM_START = datetime.fromisoformat(policy.SIM_START_ISO)
HORIZON_END = SIM_START + timedelta(days=policy.HORIZON_DAYS)

#: Rule tags that mean a hard rule stopped the record, as opposed to a score band.
#: Exported so the ledger can label failures without restating the names — two copies of
#: a string set is how a dashboard starts silently under-counting "stopped by a rule".
HARD_RULES: frozenset[str] = frozenset(
    {
        "hard_revoked_mandate",
        "hard_max_attempts",
        "hard_cooling_period",
        "hard_horizon_exhausted",
        "hard_peak_window",
    }
)

#: Tags emitted by the score-band path. Exhaustive across [0.0, 1.0] by construction.
BAND_RULES: frozenset[str] = frozenset(
    {"band_high", "band_mid_upper", "band_mid_lower", "band_low"}
)


def in_peak_window(moment: datetime) -> bool:
    """True when NPCI forbids autopay execution at `moment`. SPEC §3.3.

    Windows are half-open `[start, end)`, so 13:00 exactly is permitted and 12:59 is not.
    Evaluated in the moment's own offset — `SIM_START_ISO` is +05:30, so this is IST
    throughout and there is no timezone conversion to get wrong.
    """
    hour = moment.hour + moment.minute / 60.0 + moment.second / 3600.0
    return any(start <= hour < end for start, end in policy.PEAK_WINDOWS_IST)


def next_permitted_moment(moment: datetime) -> datetime:
    """The earliest instant at or after `moment` when a debit may execute.

    Returns `moment` unchanged when it is already permitted, so callers can use this
    unconditionally. A record is deferred to the window edge, never dropped and never
    silently executed inside a peak window.
    """
    for start, end in policy.PEAK_WINDOWS_IST:
        hour = moment.hour + moment.minute / 60.0 + moment.second / 3600.0
        if start <= hour < end:
            edge = moment.replace(
                hour=int(end), minute=int(round((end % 1) * 60)), second=0, microsecond=0
            )
            # Re-check: consecutive windows could in principle abut.
            return next_permitted_moment(edge) if in_peak_window(edge) else edge
    return moment


def hours_until_permitted(moment: datetime) -> float:
    """How long a record must wait for the next legal window. 0.0 when already legal."""
    return (next_permitted_moment(moment) - moment).total_seconds() / 3600.0


def hours_since_last_attempt(record: MandateRecord, now: datetime) -> float:
    return (now - record.last_attempt_at).total_seconds() / 3600.0


def _next_eligible_window(record: MandateRecord) -> int:
    """Smallest retry window that lands on or after payday, for balance failures.

    A technical decline is close to delay-insensitive, so retrying it soon is strictly
    better than waiting. An insufficient-balance failure is the opposite: retrying
    before money arrives burns an attempt against the cap for nothing.
    """
    if record.failure_reason is FailureReason.INSUFFICIENT_BALANCE:
        hours_to_payday = record.days_to_payday * 24
        for window in policy.RETRY_WINDOWS_HOURS:
            if window >= hours_to_payday:
                return window
        return policy.RETRY_WINDOWS_HOURS[-1]
    return policy.RETRY_WINDOWS_HOURS[0]


def decide_fallback(record: MandateRecord, score: float) -> Decision:
    """Deterministic band policy — SPEC §4.3 layer 2.

    Stands in for the agent when there is no API key and no cached decision, and is what
    `--no-llm` runs. Because this exists, the whole pipeline closes end to end before the
    agent is built, and the rupee delta between the two is the agent's measured
    contribution rather than a claim about it.
    """
    if score >= policy.BAND_HIGH:
        return Decision(
            action=Action.RETRY_NOW,
            rules_fired=("band_high",),
            reason=f"Score {score:.2f} at or above {policy.BAND_HIGH}; retry immediately.",
        )
    if score >= 0.35:
        window = _next_eligible_window(record)
        return Decision(
            action=Action.RETRY_SCHEDULED,
            rules_fired=("band_mid_upper",),
            reason=f"Score {score:.2f}; scheduling retry {window}h out.",
            retry_delay_hours=window,
        )
    if score >= policy.BAND_LOW:
        return Decision(
            action=Action.DUNNING_P2P,
            rules_fired=("band_mid_lower",),
            reason=f"Score {score:.2f} is weak; ask for a promise to pay instead of retrying.",
        )
    return Decision(
        action=Action.STOP,
        rules_fired=("band_low",),
        reason=f"Score {score:.2f} below {policy.BAND_LOW}; not worth an attempt. Write off.",
    )


def _hard_rule(record: MandateRecord, now: datetime) -> Decision | None:
    """Rules 1-4, in strict precedence order. Returns None when none of them fire."""

    # 1. A revoked mandate is not retryable. Ever. No score, no override.
    if record.failure_reason is FailureReason.REVOKED_MANDATE:
        return Decision(
            action=Action.STOP,
            rules_fired=("hard_revoked_mandate",),
            reason="Mandate revoked by the customer. Not retryable under any score.",
        )

    # 2. Attempt cap: 1 original debit plus 3 retries.
    if record.attempt_number >= policy.MAX_ATTEMPTS:
        return Decision(
            action=Action.STOP,
            rules_fired=("hard_max_attempts",),
            reason=(
                f"Attempt {record.attempt_number} of {policy.MAX_ATTEMPTS} permitted. "
                "Cap reached; write off."
            ),
        )

    # 3. Cooling period between attempts. Closed below: 24.0h is not cooling.
    elapsed = hours_since_last_attempt(record, now)
    if elapsed < policy.COOLING_PERIOD_HOURS:
        return Decision(
            action=Action.BLOCKED_COOLING,
            rules_fired=("hard_cooling_period",),
            reason=(
                f"Only {elapsed:.1f}h since the last attempt; "
                f"{policy.COOLING_PERIOD_HOURS:.0f}h required."
            ),
        )

    # 4. Campaign horizon.
    if now >= HORIZON_END:
        return Decision(
            action=Action.STOP,
            rules_fired=("hard_horizon_exhausted",),
            reason=f"Past the {policy.HORIZON_DAYS}-day campaign horizon. Expired.",
        )

    # 5. NPCI execution window. Checked last of the hard rules on purpose: the three STOPs
    #    above are terminal, and there is no sense deferring a record that is already dead.
    #    Cooling is checked before this because when both bind, cooling is the longer wait;
    #    when it expires into a peak window, this rule catches it on the next wake.
    if in_peak_window(now):
        resume = next_permitted_moment(now)
        return Decision(
            action=Action.BLOCKED_PEAK_WINDOW,
            rules_fired=("hard_peak_window",),
            reason=(
                f"{now:%H:%M} IST is inside an NPCI peak window; autopay execution is "
                f"restricted to non-peak hours. Deferred to {resume:%H:%M}."
            ),
        )

    return None


def evaluate(record: MandateRecord, score: float, now: datetime) -> Decision:
    """Decide what to do with one record. SPEC §3.1.

    Always returns an executable action. When `needs_agent` is set, the action is the
    deterministic fallback and the caller may consult the agent for a better one — but
    must pass whatever comes back through `validate_proposal` before executing it.
    """
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score must be in [0.0, 1.0], got {score}")

    hard = _hard_rule(record, now)
    if hard is not None:
        return hard

    fallback = decide_fallback(record, score)
    in_agent_band = policy.BAND_AGENT_MIN <= score < policy.BAND_AGENT_MAX
    if not in_agent_band:
        return fallback

    return Decision(
        action=fallback.action,
        rules_fired=fallback.rules_fired + ("routed_to_agent",),
        reason=fallback.reason,
        retry_delay_hours=fallback.retry_delay_hours,
        needs_agent=True,
    )


def validate_proposal(
    record: MandateRecord,
    score: float,
    now: datetime,
    proposed_action: Action,
    proposed_delay_hours: int | None = None,
) -> Decision:
    """Re-check an agent proposal against rules 1-4 before it executes. SPEC §3.1.

    This is the function that makes the guarantee true rather than aspirational. The
    agent never sees a record where a hard rule already fired, but re-validating here
    means a bug, a prompt injection, or a stale cache entry still cannot get a forbidden
    action executed.
    """
    hard = _hard_rule(record, now)
    if hard is not None:
        return Decision(
            action=hard.action,
            rules_fired=hard.rules_fired + ("vetoed_agent_proposal",),
            reason=f"{hard.reason} Agent proposed {proposed_action.value}; overruled.",
            retry_delay_hours=hard.retry_delay_hours,
            vetoed_proposal=proposed_action,
        )

    if proposed_action not in policy.AGENT_PROPOSABLE:
        return _reject(record, score, proposed_action, "not an agent-proposable action")

    if proposed_action is Action.RETRY_SCHEDULED:
        if proposed_delay_hours not in policy.RETRY_WINDOWS_HOURS:
            return _reject(
                record,
                score,
                proposed_action,
                f"retry_delay_hours {proposed_delay_hours} is not on the ladder "
                f"{policy.RETRY_WINDOWS_HOURS}",
            )
    elif proposed_delay_hours is not None:
        return _reject(
            record, score, proposed_action, f"{proposed_action.value} cannot carry a delay"
        )

    return Decision(
        action=proposed_action,
        rules_fired=("agent_proposal_accepted",),
        reason=f"Agent chose {proposed_action.value} at score {score:.2f}.",
        retry_delay_hours=proposed_delay_hours,
    )


def _reject(
    record: MandateRecord, score: float, proposed_action: Action, why: str
) -> Decision:
    """A malformed proposal is not an error — it falls back to the deterministic policy."""
    fallback = decide_fallback(record, score)
    return Decision(
        action=fallback.action,
        rules_fired=("agent_proposal_rejected",) + fallback.rules_fired,
        reason=f"Rejected agent proposal ({why}); used fallback. {fallback.reason}",
        retry_delay_hours=fallback.retry_delay_hours,
        vetoed_proposal=proposed_action,
    )
