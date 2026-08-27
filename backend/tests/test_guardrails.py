"""SPEC §8.1 F3. One test per rule, plus the adversarial cases that matter.

The adversarial ones are the point: a guardrail that only holds when the score agrees
with it is not a guardrail.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.app import guardrails, policy
from backend.app.guardrails import HORIZON_END, SIM_START, decide_fallback, evaluate
from backend.app.models import Decision, MandateRecord
from backend.app.policy import Action, FailureReason, MerchantCategory

NOW = SIM_START + timedelta(days=3)


def rec(**overrides) -> MandateRecord:
    base = dict(
        row_id="mrs_test01",
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


# --------------------------------------------------------------------------------------
# Rule 1 — revoked mandate
# --------------------------------------------------------------------------------------


def test_rule1_revoked_mandate_stops():
    d = evaluate(rec(failure_reason=FailureReason.REVOKED_MANDATE), 0.5, NOW)
    assert d.action is Action.STOP
    assert "hard_revoked_mandate" in d.rules_fired


def test_rule1_revoked_mandate_stops_even_at_score_099():
    """Adversarial: the model is as confident as it can be. The rule still wins."""
    d = evaluate(rec(failure_reason=FailureReason.REVOKED_MANDATE), 0.99, NOW)
    assert d.action is Action.STOP
    assert d.needs_agent is False, "a revoked mandate must never reach the agent"


# --------------------------------------------------------------------------------------
# Rule 2 — attempt cap
# --------------------------------------------------------------------------------------


def test_rule2_attempt_cap_stops():
    d = evaluate(rec(attempt_number=policy.MAX_ATTEMPTS), 0.5, NOW)
    assert d.action is Action.STOP
    assert "hard_max_attempts" in d.rules_fired


def test_rule2_attempt_cap_stops_even_at_score_099():
    d = evaluate(rec(attempt_number=4), 0.99, NOW)
    assert d.action is Action.STOP


def test_rule2_one_below_cap_is_allowed():
    d = evaluate(rec(attempt_number=3), 0.9, NOW)
    assert d.action is Action.RETRY_NOW


# --------------------------------------------------------------------------------------
# Rule 3 — cooling period, closed below at 24.0h
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "elapsed_hours, expect_cooling",
    [(0.0, True), (12.0, True), (23.9, True), (24.0, False), (24.1, False), (100.0, False)],
)
def test_rule3_cooling_boundary(elapsed_hours: float, expect_cooling: bool):
    d = evaluate(rec(last_attempt_at=NOW - timedelta(hours=elapsed_hours)), 0.9, NOW)
    assert (d.action is Action.BLOCKED_COOLING) is expect_cooling


def test_rule3_cooling_beats_a_high_score():
    d = evaluate(rec(last_attempt_at=NOW - timedelta(hours=1)), 0.99, NOW)
    assert d.action is Action.BLOCKED_COOLING


# --------------------------------------------------------------------------------------
# Rule 4 — horizon
# --------------------------------------------------------------------------------------


def test_rule4_past_horizon_stops():
    d = evaluate(rec(), 0.9, HORIZON_END + timedelta(hours=1))
    assert d.action is Action.STOP
    assert "hard_horizon_exhausted" in d.rules_fired


def test_rule4_inside_horizon_is_allowed():
    d = evaluate(rec(), 0.9, HORIZON_END - timedelta(hours=1))
    assert d.action is Action.RETRY_NOW


# --------------------------------------------------------------------------------------
# Rule precedence
# --------------------------------------------------------------------------------------


def test_precedence_revoked_beats_cooling():
    """Both fire. Rule 1 must win, because the reason shown to a judge has to be the real one."""
    d = evaluate(
        rec(
            failure_reason=FailureReason.REVOKED_MANDATE,
            last_attempt_at=NOW - timedelta(hours=1),
        ),
        0.5,
        NOW,
    )
    assert d.rules_fired[0] == "hard_revoked_mandate"


def test_precedence_attempt_cap_beats_cooling():
    d = evaluate(rec(attempt_number=4, last_attempt_at=NOW - timedelta(hours=1)), 0.5, NOW)
    assert d.rules_fired[0] == "hard_max_attempts"


# --------------------------------------------------------------------------------------
# Bands — exhaustive and non-overlapping across [0.0, 1.0]
# --------------------------------------------------------------------------------------


def test_bands_are_exhaustive_across_the_unit_interval():
    """Every score in [0,1] yields exactly one action. No gaps, no ambiguity."""
    for i in range(0, 1001):
        score = i / 1000
        d = decide_fallback(rec(), score)
        assert isinstance(d, Decision)
        assert d.action in {
            Action.RETRY_NOW,
            Action.RETRY_SCHEDULED,
            Action.DUNNING_P2P,
            Action.STOP,
        }
        assert d.rules_fired


def test_band_edges():
    assert decide_fallback(rec(), 0.0).action is Action.STOP
    assert decide_fallback(rec(), 0.1499).action is Action.STOP
    assert decide_fallback(rec(), 0.15).action is Action.DUNNING_P2P
    assert decide_fallback(rec(), 0.3499).action is Action.DUNNING_P2P
    assert decide_fallback(rec(), 0.35).action is Action.RETRY_SCHEDULED
    assert decide_fallback(rec(), 0.6499).action is Action.RETRY_SCHEDULED
    assert decide_fallback(rec(), 0.65).action is Action.RETRY_NOW
    assert decide_fallback(rec(), 1.0).action is Action.RETRY_NOW


def test_score_outside_unit_interval_is_rejected():
    with pytest.raises(ValueError):
        evaluate(rec(), 1.5, NOW)
    with pytest.raises(ValueError):
        evaluate(rec(), -0.01, NOW)


# --------------------------------------------------------------------------------------
# No silent paths
# --------------------------------------------------------------------------------------


def test_every_decision_names_a_rule():
    cases = [
        (rec(failure_reason=FailureReason.REVOKED_MANDATE), 0.5, NOW),
        (rec(attempt_number=4), 0.5, NOW),
        (rec(last_attempt_at=NOW - timedelta(hours=2)), 0.5, NOW),
        (rec(), 0.9, NOW),
        (rec(), 0.5, NOW),
        (rec(), 0.2, NOW),
        (rec(), 0.01, NOW),
        (rec(), 0.9, HORIZON_END + timedelta(days=1)),
    ]
    for record, score, now in cases:
        assert evaluate(record, score, now).rules_fired


def test_decision_rejects_empty_rules_fired():
    with pytest.raises(ValueError, match="empty rules_fired"):
        Decision(action=Action.STOP, rules_fired=(), reason="x")


# --------------------------------------------------------------------------------------
# Agent routing
# --------------------------------------------------------------------------------------


def test_only_the_ambiguous_band_routes_to_the_agent():
    assert evaluate(rec(), 0.9, NOW).needs_agent is False
    assert evaluate(rec(), 0.05, NOW).needs_agent is False
    assert evaluate(rec(), 0.44, NOW).needs_agent is True
    assert evaluate(rec(), 0.15, NOW).needs_agent is True
    assert evaluate(rec(), 0.6499, NOW).needs_agent is True
    assert evaluate(rec(), 0.65, NOW).needs_agent is False


def test_agent_band_still_carries_an_executable_fallback():
    """Gate B runs with no agent at all, so the routed decision must already be valid."""
    d = evaluate(rec(), 0.44, NOW)
    assert d.needs_agent is True
    assert d.action is Action.RETRY_SCHEDULED
    assert d.retry_delay_hours in policy.RETRY_WINDOWS_HOURS


# --------------------------------------------------------------------------------------
# Proposal re-validation — SPEC §8.2 gate 4
# --------------------------------------------------------------------------------------


def test_hard_rule_vetoes_an_agent_retry_proposal():
    """The demo centrepiece: the agent asks to retry, a rule refuses."""
    revoked = rec(failure_reason=FailureReason.REVOKED_MANDATE)
    d = guardrails.validate_proposal(revoked, 0.62, NOW, Action.RETRY_NOW)
    assert d.action is Action.STOP
    assert d.vetoed_proposal is Action.RETRY_NOW
    assert "vetoed_agent_proposal" in d.rules_fired


def test_cooling_period_vetoes_an_agent_retry_proposal():
    cooling = rec(last_attempt_at=NOW - timedelta(hours=3))
    d = guardrails.validate_proposal(cooling, 0.55, NOW, Action.RETRY_NOW)
    assert d.action is Action.BLOCKED_COOLING
    assert d.vetoed_proposal is Action.RETRY_NOW


def test_valid_proposal_is_accepted():
    d = guardrails.validate_proposal(rec(), 0.5, NOW, Action.RETRY_SCHEDULED, 72)
    assert d.action is Action.RETRY_SCHEDULED
    assert d.retry_delay_hours == 72
    assert d.vetoed_proposal is None


def test_agent_cannot_propose_blocked_cooling():
    """BLOCKED_COOLING is guardrail-imposed; the agent must not be able to claim it."""
    d = guardrails.validate_proposal(rec(), 0.5, NOW, Action.BLOCKED_COOLING)
    assert d.action is not Action.BLOCKED_COOLING
    assert "agent_proposal_rejected" in d.rules_fired


def test_off_ladder_retry_delay_is_rejected():
    d = guardrails.validate_proposal(rec(), 0.5, NOW, Action.RETRY_SCHEDULED, 48)
    assert "agent_proposal_rejected" in d.rules_fired
    assert d.retry_delay_hours in policy.RETRY_WINDOWS_HOURS


def test_stop_proposal_carrying_a_delay_is_rejected():
    d = guardrails.validate_proposal(rec(), 0.5, NOW, Action.STOP, 24)
    assert "agent_proposal_rejected" in d.rules_fired


# --------------------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------------------


def test_evaluate_is_deterministic_and_does_not_mutate():
    record = rec()
    before = (record.attempt_number, record.days_to_payday, record.last_attempt_at)
    first = evaluate(record, 0.44, NOW)
    second = evaluate(record, 0.44, NOW)
    assert first == second
    assert (record.attempt_number, record.days_to_payday, record.last_attempt_at) == before


def test_retry_window_waits_for_payday_on_balance_failures():
    """A balance failure retried before payday burns an attempt against the cap."""
    d = decide_fallback(rec(failure_reason=FailureReason.INSUFFICIENT_BALANCE,
                           days_to_payday=5), 0.5)
    assert d.retry_delay_hours == 168  # 5 days out -> the 168h rung, not 24h or 72h


def test_technical_decline_retries_on_the_shortest_rung():
    d = decide_fallback(rec(failure_reason=FailureReason.TECHNICAL_DECLINE,
                           days_to_payday=20), 0.5)
    assert d.retry_delay_hours == 24


# --------------------------------------------------------------------------------------
# Rule 5 — the NPCI execution window (SPEC §3.3)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hour,expected_peak",
    [
        (0.0, False),    # midnight — permitted
        (9.99, False),   # just before the morning peak opens
        (10.0, True),    # peak opens, inclusive
        (11.5, True),
        (12.99, True),   # still peak
        (13.0, False),   # peak closes, exclusive — 13:00 is permitted
        (16.99, False),
        (17.0, True),    # evening peak opens
        (20.0, True),
        (21.49, True),   # 21:29 — still peak
        (21.5, False),   # 21:30 — permitted
        (23.99, False),
    ],
)
def test_peak_window_boundaries(hour: float, expected_peak: bool) -> None:
    """Half-open [start, end): 13:00 is legal, 12:59 is not. Off-by-one here is a fine."""
    moment = SIM_START + timedelta(hours=hour)
    assert guardrails.in_peak_window(moment) is expected_peak


@pytest.mark.parametrize(
    "hour,expected_resume_hour",
    [
        (10.0, 13.0),
        (12.5, 13.0),
        (17.0, 21.5),
        (21.0, 21.5),
    ],
)
def test_deferral_lands_on_the_window_edge(hour: float, expected_resume_hour: float) -> None:
    moment = SIM_START + timedelta(hours=hour)
    resume = guardrails.next_permitted_moment(moment)

    assert resume.hour + resume.minute / 60.0 == expected_resume_hour
    assert not guardrails.in_peak_window(resume)


def test_a_permitted_moment_is_returned_unchanged() -> None:
    for hour in (0.0, 8.0, 14.0, 22.0):
        moment = SIM_START + timedelta(hours=hour)
        assert guardrails.next_permitted_moment(moment) == moment
        assert guardrails.hours_until_permitted(moment) == 0.0


def test_rule5_defers_rather_than_stopping() -> None:
    """BLOCKED_PEAK_WINDOW postpones. It must never be a write-off."""
    peak = SIM_START + timedelta(hours=11)
    decision = evaluate(rec(last_attempt_at=peak - timedelta(hours=48)), 0.9, peak)

    assert decision.action is Action.BLOCKED_PEAK_WINDOW
    assert "hard_peak_window" in decision.rules_fired
    assert decision.action is not Action.STOP


def test_rule5_beats_even_a_top_score() -> None:
    """A 0.99 does not buy an execution inside a window NPCI has closed."""
    peak = SIM_START + timedelta(hours=19)
    decision = evaluate(rec(last_attempt_at=peak - timedelta(hours=72)), 0.99, peak)

    assert decision.action is Action.BLOCKED_PEAK_WINDOW


def test_terminal_rules_beat_the_peak_window() -> None:
    """No sense deferring a record that is already dead. STOP wins over a deferral."""
    peak = SIM_START + timedelta(hours=11)

    revoked = evaluate(
        rec(failure_reason=FailureReason.REVOKED_MANDATE, last_attempt_at=peak - timedelta(hours=48)),
        0.9,
        peak,
    )
    assert revoked.action is Action.STOP
    assert "hard_revoked_mandate" in revoked.rules_fired

    capped = evaluate(
        rec(attempt_number=4, last_attempt_at=peak - timedelta(hours=48)), 0.9, peak
    )
    assert capped.action is Action.STOP


def test_cooling_beats_the_peak_window_when_both_bind() -> None:
    """Both defer; cooling is the longer wait, so it is the binding constraint."""
    peak = SIM_START + timedelta(hours=11)
    decision = evaluate(rec(last_attempt_at=peak - timedelta(hours=2)), 0.9, peak)

    assert decision.action is Action.BLOCKED_COOLING


def test_the_agent_can_never_propose_a_peak_block() -> None:
    """Guardrail-imposed actions are not on the agent's menu (SPEC §3.3)."""
    assert Action.BLOCKED_PEAK_WINDOW not in policy.AGENT_PROPOSABLE

    peak = SIM_START + timedelta(hours=11)
    vetoed = guardrails.validate_proposal(
        rec(last_attempt_at=peak - timedelta(hours=48)), 0.44, peak, Action.RETRY_NOW, None
    )
    assert vetoed.action is not Action.RETRY_NOW
