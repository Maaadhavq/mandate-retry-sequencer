"""F1 — synthetic batch of failed UPI Autopay mandate debits. SPEC §2.1, §2.2.

Everything here is synthetic and seeded. The same `--seed` must reproduce byte-identical
CSVs, so every random draw is vectorised in a fixed order and nothing depends on dict
iteration, wall-clock time, or platform line endings.

The hidden recovery model lives in `_ground_truth_probability`. It is deliberately not
trivially learnable — see SPEC §2.2. Three properties are load-bearing:

1. The payday interaction. Recovery for `insufficient_balance` peaks when the retry lands
   on or just after payday, i.e. it keys on `days_to_payday - delay_hours/24` rather than
   on either alone. For `technical_decline` the same term is near-inert.
2. Label noise at NOISE_RATE — real recovery data is not clean.
3. The `edtech` blind spot. Those rows are drawn with *observably favourable* features
   while a hidden academic fee cycle suppresses actual recovery for most of them. The
   scorer keys on the favourable observables and systematically over-predicts. The cycle
   is never written to a CSV column, so this is not recoverable by the model — which is
   the point: it gives the honest-failures panel a real cluster and ARCHITECTURE.md
   something true to say about where the model is weak.

`revoked_mandate` recovery is exactly 0.0 with no noise applied. It is a hard rule, not a
probability, and the noise pass skips it entirely.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import numpy as np

from backend.app.policy import (
    MAX_ATTEMPTS,
    RETRY_WINDOWS_HOURS,
    SIM_START_ISO,
    FailureReason,
    MerchantCategory,
)

# --------------------------------------------------------------------------------------
# Generator parameters.
#
# These describe the synthetic world, not the retry policy. Policy constants — attempt
# caps, cooling periods, retry windows, score bands — live in policy.py and are imported,
# never restated (CLAUDE.md).
# --------------------------------------------------------------------------------------

DEFAULT_N: Final[int] = 500
DEFAULT_SEED: Final[int] = 42
HOLDOUT_FRACTION: Final[float] = 0.20

#: Fraction of outcomes flipped against the ground-truth draw. SPEC §2.2 property 2.
NOISE_RATE: Final[float] = 0.08

TICKET_MIN_PAISE: Final[int] = 4_900          # ₹49
TICKET_MAX_PAISE: Final[int] = 4_999_900      # ₹49,999

FAILURE_REASON_WEIGHTS: Final[dict[FailureReason, float]] = {
    FailureReason.INSUFFICIENT_BALANCE: 0.55,
    FailureReason.TECHNICAL_DECLINE: 0.30,
    FailureReason.REVOKED_MANDATE: 0.15,
}

MERCHANT_CATEGORY_WEIGHTS: Final[dict[MerchantCategory, float]] = {
    MerchantCategory.SAAS: 0.26,
    MerchantCategory.EDTECH: 0.22,
    MerchantCategory.OTT: 0.20,
    MerchantCategory.FITNESS: 0.16,
    MerchantCategory.UTILITIES: 0.16,
}

#: attempt_number 1..MAX_ATTEMPTS. The tail is deliberately fat enough that rule 2
#: (attempt cap) fires on a 500-record batch — SPEC §8.2 gate 3.
ATTEMPT_WEIGHTS: Final[tuple[float, ...]] = (0.46, 0.27, 0.15, 0.12)

#: The delay the *historical* system used before this project existed: a naive fixed
#: retry at the first window, applied to every record regardless of payday.
#:
#: This is what the training labels were produced under. Holding it constant is a
#: modelling choice, not a performance fix: measured at the tuned constants, drawing the
#: delay per row instead costs only ~0.010 population oracle AUC (0.824 -> 0.814), because
#: a delay-blind scorer can still rank on the mean across the three windows. The reason to
#: fix it is that it is the honest baseline — choosing the delay per record is precisely
#: what this system does that the legacy one did not, so the training labels should
#: reflect a world where nobody was choosing.
#:
#: (An earlier revision justified this constant by claiming the sampled delay capped
#: oracle AUC at 0.70. That measurement was real but the attribution was wrong: the
#: ceiling came from PAYDAY_SPREAD being too narrow — see below — and survived fixing
#: the delay. Corrected here so ARCHITECTURE.md does not inherit the mistake.)
LEGACY_RETRY_DELAY_HOURS: Final[int] = RETRY_WINDOWS_HOURS[0]

#: Share of records whose last attempt is inside the cooling period, so rule 3 fires.
COOLING_SHARE: Final[float] = 0.20
COOLING_MAX_HOURS_AGO: Final[float] = 24.0
STALE_MIN_HOURS_AGO: Final[float] = 24.0
STALE_MAX_HOURS_AGO: Final[float] = 240.0

# Recovery model ------------------------------------------------------------------------

BASE_INSUFFICIENT_BALANCE: Final[float] = 0.05
BASE_TECHNICAL_DECLINE: Final[float] = 0.82

#: How much the payday alignment term moves recovery, per failure reason. The gap between
#: these two is the interaction the scorer has to discover.
PAYDAY_WEIGHT_IB: Final[float] = 0.88
PAYDAY_WEIGHT_TD: Final[float] = 0.05

#: Alignment peaks just *after* payday: a retry landing half a day late still catches the
#: balance, one landing early does not.
PAYDAY_PEAK_OFFSET: Final[float] = -0.5

#: Spread of the alignment bump, in days. This must stay wide relative to the 0-30 range
#: of days_to_payday. At 1.8 the bump was ~0 for roughly nine rows in ten, which flattened
#: insufficient_balance to a near-constant p (median 0.18, p75 0.22) and capped the
#: noise-free oracle AUC at 0.78 — below SPEC §2.2's target band before the scorer even
#: ran. Widening it turns payday into a gradient across the batch rather than a spike a
#: handful of rows happen to sit on.
PAYDAY_SPREAD: Final[float] = 4.0

#: Each additional attempt on the same mandate compounds this multiplier.
ATTEMPT_DECAY: Final[float] = 0.86

#: Hidden academic fee cycle for edtech. Never exposed as a column.
EDTECH_OFF_CYCLE_SHARE: Final[float] = 0.80
EDTECH_OFF_CYCLE_MULT: Final[float] = 0.18
EDTECH_IN_CYCLE_MULT: Final[float] = 1.25

P_FLOOR: Final[float] = 0.02
P_CEIL: Final[float] = 0.95

CSV_COLUMNS: Final[tuple[str, ...]] = (
    "row_id",
    "failure_reason",
    "days_to_payday",
    "attempt_number",
    "ticket_size_paise",
    "merchant_category",
    "days_since_last_success",
    "mandate_age_days",
    "last_attempt_at",
    "recovered",
)

SIM_START: Final[datetime] = datetime.fromisoformat(SIM_START_ISO)


def _weighted_choice(
    rng: np.random.Generator, values: list[str], weights: list[float], n: int
) -> np.ndarray:
    """Draw `n` values by weight. Weights are normalised so they need not sum to 1."""
    p = np.asarray(weights, dtype=float)
    p = p / p.sum()
    return rng.choice(np.asarray(values, dtype=object), size=n, p=p)


def _payday_alignment(days_to_payday: np.ndarray, delay_hours: np.ndarray) -> np.ndarray:
    """Gaussian bump on `days_to_payday - delay_hours/24`, peaking just after payday.

    This is the term the scorer must learn to use conditionally: it dominates recovery for
    insufficient_balance and is nearly inert for technical_decline (SPEC §2.2 property 1).
    """
    align = days_to_payday - delay_hours / 24.0
    return np.exp(-((align - PAYDAY_PEAK_OFFSET) ** 2) / (2.0 * PAYDAY_SPREAD**2))


def _ground_truth_probability(
    *,
    failure_reason: np.ndarray,
    days_to_payday: np.ndarray,
    attempt_number: np.ndarray,
    ticket_size_paise: np.ndarray,
    days_since_last_success: np.ndarray,
    mandate_age_days: np.ndarray,
    edtech_off_cycle: np.ndarray,
    is_edtech: np.ndarray,
    delay_hours: np.ndarray,
) -> np.ndarray:
    """Hidden P(recover | features, retry_delay_hours). Never written as a column.

    Returns exactly 0.0 for revoked mandates, with no floor applied — SPEC §2.2 is
    explicit that this is a rule rather than a small probability.
    """
    bump = _payday_alignment(days_to_payday, delay_hours)

    is_ib = failure_reason == FailureReason.INSUFFICIENT_BALANCE.value
    is_td = failure_reason == FailureReason.TECHNICAL_DECLINE.value
    is_revoked = failure_reason == FailureReason.REVOKED_MANDATE.value

    p = np.where(
        is_ib,
        BASE_INSUFFICIENT_BALANCE + PAYDAY_WEIGHT_IB * bump,
        np.where(is_td, BASE_TECHNICAL_DECLINE + PAYDAY_WEIGHT_TD * bump, 0.0),
    )

    # Retry fatigue: each prior attempt on the same mandate costs.
    p = p * (ATTEMPT_DECAY ** (attempt_number - 1))

    # A mandate that succeeded recently is a better bet than a long-dormant one.
    p = p * (0.70 + 0.30 * np.exp(-days_since_last_success / 90.0))

    # An older, established mandate carries a little more goodwill.
    p = p * (0.92 + 0.16 * np.clip(mandate_age_days / 1095.0, 0.0, 1.0))

    # Larger tickets are harder to recover.
    ticket_frac = np.clip(
        (ticket_size_paise - TICKET_MIN_PAISE) / (TICKET_MAX_PAISE - TICKET_MIN_PAISE),
        0.0,
        1.0,
    )
    p = p * (1.06 - 0.28 * ticket_frac)

    # The blind spot. Applied only to edtech, driven by a hidden cycle with no column.
    edtech_mult = np.where(edtech_off_cycle, EDTECH_OFF_CYCLE_MULT, EDTECH_IN_CYCLE_MULT)
    p = np.where(is_edtech, p * edtech_mult, p)

    p = np.clip(p, P_FLOOR, P_CEIL)
    return np.where(is_revoked, 0.0, p)


def generate(n: int, seed: int) -> tuple[list[dict], dict]:
    """Build `n` records plus the hidden ground-truth sidecar.

    Every draw below is a full-length array taken in a fixed order, including the ones
    only some rows use. Consuming the stream unconditionally is what keeps output
    byte-identical for a given seed regardless of how the categorical draws land.
    """
    rng = np.random.default_rng(seed)

    failure_reason = _weighted_choice(
        rng,
        [r.value for r in FAILURE_REASON_WEIGHTS],
        list(FAILURE_REASON_WEIGHTS.values()),
        n,
    ).astype(str)

    merchant_category = _weighted_choice(
        rng,
        [c.value for c in MERCHANT_CATEGORY_WEIGHTS],
        list(MERCHANT_CATEGORY_WEIGHTS.values()),
        n,
    ).astype(str)
    is_edtech = merchant_category == MerchantCategory.EDTECH.value

    # Two candidate draws per feature; edtech takes the observably favourable one. Both
    # are always drawn so the RNG stream does not depend on the category split.
    payday_general = rng.integers(0, 31, size=n)
    payday_edtech = rng.integers(0, 13, size=n)
    days_to_payday = np.where(is_edtech, payday_edtech, payday_general)

    dsls_general = rng.integers(0, 181, size=n)
    dsls_edtech = rng.integers(0, 46, size=n)
    days_since_last_success = np.where(is_edtech, dsls_edtech, dsls_general)

    attempt_number = rng.choice(
        np.arange(1, MAX_ATTEMPTS + 1), size=n, p=np.asarray(ATTEMPT_WEIGHTS)
    )
    ticket_size_paise = rng.integers(TICKET_MIN_PAISE, TICKET_MAX_PAISE + 1, size=n)
    mandate_age_days = rng.integers(1, 1096, size=n)

    # last_attempt_at: a fifth of the batch sits inside the cooling period so guardrail
    # rule 3 provably fires on a 500-record batch (SPEC §8.2 gate 3).
    in_cooling = rng.random(n) < COOLING_SHARE
    hours_cooling = rng.uniform(0.0, COOLING_MAX_HOURS_AGO, size=n)
    hours_stale = rng.uniform(STALE_MIN_HOURS_AGO, STALE_MAX_HOURS_AGO, size=n)
    hours_ago = np.where(in_cooling, hours_cooling, hours_stale)

    # The hidden academic cycle. The historical delay is a constant, not a draw — see
    # LEGACY_RETRY_DELAY_HOURS for why that is load-bearing rather than a simplification.
    edtech_off_cycle = rng.random(n) < EDTECH_OFF_CYCLE_SHARE
    observed_delay_hours = np.full(n, LEGACY_RETRY_DELAY_HOURS)

    row_ids = _unique_row_ids(rng, n)

    p_observed = _ground_truth_probability(
        failure_reason=failure_reason,
        days_to_payday=days_to_payday,
        attempt_number=attempt_number,
        ticket_size_paise=ticket_size_paise,
        days_since_last_success=days_since_last_success,
        mandate_age_days=mandate_age_days,
        edtech_off_cycle=edtech_off_cycle,
        is_edtech=is_edtech,
        delay_hours=observed_delay_hours,
    )

    is_revoked = failure_reason == FailureReason.REVOKED_MANDATE.value

    draw = rng.random(n)
    label = (draw < p_observed).astype(int)

    # Noise flips outcomes against the ground truth — but never for revoked mandates,
    # whose recovery is 0.0 by rule.
    flip = (rng.random(n) < NOISE_RATE) & ~is_revoked
    label = np.where(flip, 1 - label, label)
    label = np.where(is_revoked, 0, label)

    # The full delay curve, so F4's executor can sample the outcome of whatever delay the
    # policy actually chooses rather than being stuck with the historical one.
    p_by_delay: dict[int, np.ndarray] = {
        int(d): _ground_truth_probability(
            failure_reason=failure_reason,
            days_to_payday=days_to_payday,
            attempt_number=attempt_number,
            ticket_size_paise=ticket_size_paise,
            days_since_last_success=days_since_last_success,
            mandate_age_days=mandate_age_days,
            edtech_off_cycle=edtech_off_cycle,
            is_edtech=is_edtech,
            delay_hours=np.full(n, d),
        )
        for d in RETRY_WINDOWS_HOURS
    }

    records: list[dict] = []
    truth: dict[str, dict] = {}
    for i in range(n):
        last_attempt = SIM_START - timedelta(hours=float(hours_ago[i]))
        records.append(
            {
                "row_id": row_ids[i],
                "failure_reason": str(failure_reason[i]),
                "days_to_payday": int(days_to_payday[i]),
                "attempt_number": int(attempt_number[i]),
                "ticket_size_paise": int(ticket_size_paise[i]),
                "merchant_category": str(merchant_category[i]),
                "days_since_last_success": int(days_since_last_success[i]),
                "mandate_age_days": int(mandate_age_days[i]),
                "last_attempt_at": last_attempt.isoformat(),
                "recovered": int(label[i]),
            }
        )
        truth[row_ids[i]] = {
            "p_by_delay_hours": {
                str(int(d)): round(float(p_by_delay[int(d)][i]), 6)
                for d in RETRY_WINDOWS_HOURS
            },
            "observed_delay_hours": int(observed_delay_hours[i]),
            "p_observed": round(float(p_observed[i]), 6),
            "label": int(label[i]),
            "noise_flipped": bool(flip[i]),
            "edtech_off_cycle": bool(edtech_off_cycle[i]) if bool(is_edtech[i]) else None,
        }

    return records, truth


def _unique_row_ids(rng: np.random.Generator, n: int) -> list[str]:
    """`mrs_<6 hex>`, unique across the whole batch and therefore across both splits."""
    seen: set[str] = set()
    out: list[str] = []
    while len(out) < n:
        for value in rng.integers(0, 16**6, size=n - len(out)):
            candidate = f"mrs_{int(value):06x}"
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    """Fixed column order, LF endings, no platform-dependent formatting."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    records, truth = generate(n=args.n, seed=args.seed)

    # The split is written here, at generation time, before anything is modelled. Nothing
    # downstream is permitted to re-split (CLAUDE.md, SPEC §2.1).
    rng = np.random.default_rng(args.seed + 1)
    order = rng.permutation(len(records))
    n_holdout = int(round(len(records) * HOLDOUT_FRACTION))
    holdout_idx = sorted(int(i) for i in order[:n_holdout])
    train_idx = sorted(int(i) for i in order[n_holdout:])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.out_dir / "batch_train.csv", [records[i] for i in train_idx])
    _write_csv(args.out_dir / "batch_holdout.csv", [records[i] for i in holdout_idx])

    payload = {
        "seed": args.seed,
        "n": args.n,
        "noise_rate": NOISE_RATE,
        "holdout_fraction": HOLDOUT_FRACTION,
        "split": {
            "train": [records[i]["row_id"] for i in train_idx],
            "holdout": [records[i]["row_id"] for i in holdout_idx],
        },
        "rows": truth,
    }
    with (args.out_dir / "ground_truth.json").open("w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")

    recovered = sum(r["recovered"] for r in records)
    print(f"seed={args.seed} n={args.n}")
    print(f"  train   {len(train_idx):>4}  ->  {args.out_dir / 'batch_train.csv'}")
    print(f"  holdout {len(holdout_idx):>4}  ->  {args.out_dir / 'batch_holdout.csv'}")
    print(f"  truth        ->  {args.out_dir / 'ground_truth.json'}")
    print(f"  recovered {recovered}/{args.n} ({recovered / args.n:.1%})")


if __name__ == "__main__":
    main()
