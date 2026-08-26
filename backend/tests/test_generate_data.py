"""SPEC §8.1 F1, plus the §2.2 properties that make this generator worth having.

The five gates are the floor. A generator that passes only those could still emit clean
separable signal, which would make F2's AUC meaningless — so the second half of this file
asserts the interaction, the noise, and the edtech blind spot actually exist.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.app.policy import MAX_ATTEMPTS, RETRY_WINDOWS_HOURS, FailureReason, MerchantCategory
from backend.scripts.generate_data import (
    NOISE_RATE,
    TICKET_MAX_PAISE,
    TICKET_MIN_PAISE,
    _ground_truth_probability,
    _payday_alignment,
    generate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_generator(out_dir: Path, seed: int, n: int = 200) -> None:
    """Invoke the CLI the way the README does, so the tests cover the real entry point."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.scripts.generate_data",
            "--seed",
            str(seed),
            "--n",
            str(n),
            "--out-dir",
            str(out_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result


# --------------------------------------------------------------------------------------
# SPEC §8.1 F1 — the five gates
# --------------------------------------------------------------------------------------


def test_same_seed_is_byte_identical(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    run_generator(a, seed=42)
    run_generator(b, seed=42)

    for name in ("batch_train.csv", "batch_holdout.csv", "ground_truth.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), f"{name} is not reproducible"


def test_different_seed_differs(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    run_generator(a, seed=42)
    run_generator(b, seed=43)

    assert (a / "batch_train.csv").read_bytes() != (b / "batch_train.csv").read_bytes()


def test_no_row_id_appears_in_both_splits(tmp_path: Path) -> None:
    run_generator(tmp_path, seed=42)
    truth = json.loads((tmp_path / "ground_truth.json").read_text(encoding="utf-8"))

    train = set(truth["split"]["train"])
    holdout = set(truth["split"]["holdout"])

    assert train & holdout == set(), "a row_id leaked across the split boundary"
    assert len(train) + len(holdout) == len(truth["rows"])


def test_revoked_mandate_ground_truth_is_exactly_zero() -> None:
    records, truth = generate(n=500, seed=42)

    revoked = [
        r for r in records if r["failure_reason"] == FailureReason.REVOKED_MANDATE.value
    ]
    assert revoked, "no revoked mandates generated — rule 1 would never fire"

    for record in revoked:
        row = truth[record["row_id"]]
        assert row["p_observed"] == 0.0
        assert all(p == 0.0 for p in row["p_by_delay_hours"].values())
        assert row["label"] == 0, "a revoked mandate was labelled recovered"
        assert row["noise_flipped"] is False, "noise was applied to a revoked mandate"


def test_all_failure_reasons_present_at_five_percent() -> None:
    records, _ = generate(n=500, seed=42)

    for reason in FailureReason:
        share = sum(r["failure_reason"] == reason.value for r in records) / len(records)
        assert share >= 0.05, f"{reason.value} is only {share:.1%} of the batch"


# --------------------------------------------------------------------------------------
# SPEC §2.1 — the record contract
# --------------------------------------------------------------------------------------


def test_fields_stay_inside_their_declared_ranges() -> None:
    records, _ = generate(n=500, seed=42)

    for r in records:
        assert r["row_id"].startswith("mrs_") and len(r["row_id"]) == 10
        assert 0 <= r["days_to_payday"] <= 30
        assert 1 <= r["attempt_number"] <= MAX_ATTEMPTS
        assert TICKET_MIN_PAISE <= r["ticket_size_paise"] <= TICKET_MAX_PAISE
        assert 0 <= r["days_since_last_success"] <= 180
        assert 1 <= r["mandate_age_days"] <= 1095
        assert r["merchant_category"] in {c.value for c in MerchantCategory}
        assert r["recovered"] in (0, 1)


def test_money_is_integer_paise() -> None:
    """CLAUDE.md: a float never touches a currency value."""
    records, _ = generate(n=500, seed=42)

    for r in records:
        assert isinstance(r["ticket_size_paise"], int)
        assert not isinstance(r["ticket_size_paise"], bool)


def test_row_ids_are_unique() -> None:
    records, _ = generate(n=500, seed=42)
    ids = [r["row_id"] for r in records]

    assert len(set(ids)) == len(ids)


def test_ground_truth_is_never_a_column(tmp_path: Path) -> None:
    """The hidden probability must not leak into the training data. SPEC §2.2."""
    run_generator(tmp_path, seed=42)
    header = (tmp_path / "batch_train.csv").read_text(encoding="utf-8").splitlines()[0]

    for leak in ("p_observed", "p_by_delay", "edtech_off_cycle", "observed_delay", "noise"):
        assert leak not in header, f"{leak} leaked into the CSV header"


# --------------------------------------------------------------------------------------
# SPEC §2.2 — the three properties that make it hard
# --------------------------------------------------------------------------------------


def test_payday_alignment_peaks_just_after_payday() -> None:
    """Recovery should be best when the retry lands on, or just after, payday.

    Swept finely rather than over a handful of points: a coarse grid can tie either side
    of the peak and pass on argmax order rather than on the shape being right.
    """
    delay = 24.0
    days = np.arange(-4.0, 12.0, 0.05)
    bump = _payday_alignment(days, np.full(days.size, delay))

    landing_day = days[bump.argmax()] - delay / 24.0  # <0 means the retry lands after payday
    assert -1.5 < landing_day <= 0.5, f"peak lands {landing_day:+.2f} days from payday"

    # Monotone away from the peak in both directions, so it is a single clean bump.
    peak = int(bump.argmax())
    assert np.all(np.diff(bump[: peak + 1]) > 0)
    assert np.all(np.diff(bump[peak:]) < 0)


def test_payday_interaction_is_conditional_on_failure_reason() -> None:
    """Dominant for insufficient_balance, near-inert for technical_decline. §2.2 prop 1."""

    def spread(reason: FailureReason) -> float:
        days = np.arange(0, 31, dtype=float)
        n = days.size
        p = _ground_truth_probability(
            failure_reason=np.full(n, reason.value),
            days_to_payday=days,
            attempt_number=np.ones(n),
            ticket_size_paise=np.full(n, 99_900),
            days_since_last_success=np.full(n, 30),
            mandate_age_days=np.full(n, 400),
            edtech_off_cycle=np.zeros(n, dtype=bool),
            is_edtech=np.zeros(n, dtype=bool),
            delay_hours=np.full(n, 24.0),
        )
        return float(p.max() - p.min())

    ib = spread(FailureReason.INSUFFICIENT_BALANCE)
    td = spread(FailureReason.TECHNICAL_DECLINE)

    assert ib > 0.20, f"payday barely moves insufficient_balance (spread {ib:.3f})"
    assert td < 0.05, f"payday should be near-inert for technical_decline (spread {td:.3f})"
    assert ib > 4 * td, "the interaction is not sharp enough for a model to have to find it"


def test_label_noise_is_present_and_near_the_declared_rate() -> None:
    """§2.2 prop 2. Noise on non-revoked rows only."""
    _, truth = generate(n=2000, seed=42)

    eligible = [r for r in truth.values() if r["p_observed"] > 0.0]
    flipped = sum(r["noise_flipped"] for r in eligible)
    rate = flipped / len(eligible)

    assert 0.5 * NOISE_RATE < rate < 1.5 * NOISE_RATE, f"noise rate is {rate:.3f}"


def test_edtech_is_the_blind_spot() -> None:
    """§2.2 prop 3: observably favourable, actually worse, and not explainable by a column."""
    records, truth = generate(n=2000, seed=42)

    edtech = [r for r in records if r["merchant_category"] == MerchantCategory.EDTECH.value]
    other = [r for r in records if r["merchant_category"] != MerchantCategory.EDTECH.value]

    # Observably favourable: the features a model can see look better for edtech.
    assert np.mean([r["days_to_payday"] for r in edtech]) < np.mean(
        [r["days_to_payday"] for r in other]
    )
    assert np.mean([r["days_since_last_success"] for r in edtech]) < np.mean(
        [r["days_since_last_success"] for r in other]
    )

    # But actual recovery is worse — that gap is what the scorer cannot see.
    edtech_rate = np.mean([r["recovered"] for r in edtech])
    other_rate = np.mean([r["recovered"] for r in other])
    assert edtech_rate < other_rate, (
        f"edtech recovers at {edtech_rate:.1%} vs {other_rate:.1%} — "
        "the blind spot is not biting"
    )

    # And the driver is hidden, not a column.
    off = [
        truth[r["row_id"]]["label"]
        for r in edtech
        if truth[r["row_id"]]["edtech_off_cycle"] is True
    ]
    on = [
        truth[r["row_id"]]["label"]
        for r in edtech
        if truth[r["row_id"]]["edtech_off_cycle"] is False
    ]
    assert off and on, "both phases of the academic cycle should appear"
    assert np.mean(on) > np.mean(off), "the hidden cycle should separate the two phases"


# --------------------------------------------------------------------------------------
# Downstream guarantees — the batch has to be able to fire every hard rule (SPEC §8.2)
# --------------------------------------------------------------------------------------


def test_batch_can_fire_every_hard_guardrail() -> None:
    records, _ = generate(n=500, seed=42)

    revoked = sum(
        r["failure_reason"] == FailureReason.REVOKED_MANDATE.value for r in records
    )
    capped = sum(r["attempt_number"] >= MAX_ATTEMPTS for r in records)

    assert revoked > 0, "rule 1 (revoked mandate) can never fire on this batch"
    assert capped > 0, "rule 2 (attempt cap) can never fire on this batch"


def test_some_records_sit_inside_the_cooling_period() -> None:
    """Rule 3 needs last_attempt_at inside the cooling window for at least one record."""
    from datetime import datetime

    from backend.app.policy import COOLING_PERIOD_HOURS, SIM_START_ISO

    records, _ = generate(n=500, seed=42)
    sim_start = datetime.fromisoformat(SIM_START_ISO)

    inside = [
        r
        for r in records
        if (sim_start - datetime.fromisoformat(r["last_attempt_at"])).total_seconds() / 3600.0
        < COOLING_PERIOD_HOURS
    ]
    assert inside, "rule 3 (cooling period) can never fire on this batch"


def test_ground_truth_covers_every_retry_window() -> None:
    """F4's executor samples the delay the policy chooses, not the historical one."""
    _, truth = generate(n=100, seed=42)

    for row in truth.values():
        assert set(row["p_by_delay_hours"]) == {str(w) for w in RETRY_WINDOWS_HOURS}


def test_holdout_split_is_written_at_generation_time(tmp_path: Path) -> None:
    """CLAUDE.md: batch_holdout.csv is not read before the model is fit."""
    run_generator(tmp_path, seed=42, n=200)
    truth = json.loads((tmp_path / "ground_truth.json").read_text(encoding="utf-8"))

    holdout_rows = (tmp_path / "batch_holdout.csv").read_text(encoding="utf-8").splitlines()
    assert len(holdout_rows) - 1 == len(truth["split"]["holdout"]) == 40


@pytest.mark.parametrize("seed", [42, 43, 7])
def test_recovery_rate_is_plausible(seed: int) -> None:
    """Neither degenerate nor trivially separable — a sanity floor under F2's AUC."""
    records, _ = generate(n=500, seed=seed)
    rate = sum(r["recovered"] for r in records) / len(records)

    assert 0.15 < rate < 0.60, f"recovery rate {rate:.1%} looks degenerate"
