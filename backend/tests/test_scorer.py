"""SPEC §8.1 F2. The gates, the leakage guarantees, and the blind spot.

The leakage tests are the ones that matter. An AUC is only worth reporting if you can
show what the model was not allowed to see, so those are asserted mechanically rather
than by reading the source and trusting it.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.app.policy import THRESHOLD_CUTS, FailureReason, MerchantCategory
from backend.app.scorer import (
    CATEGORICAL_FEATURES,
    FAILURE_REASON_CODES,
    FEATURES,
    MERCHANT_CATEGORY_CODES,
    Scorer,
    encode_matrix,
    encode_row,
)
from backend.scripts.train_scorer import (
    AUC_FLOOR,
    AUC_LEAK_CEILING,
    SealedHoldout,
    bootstrap_auc_ci,
    confusion_at,
    read_csv,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
MODELS = REPO_ROOT / "models"

needs_artifacts = pytest.mark.skipif(
    not (MODELS / "metrics.json").exists() or not (DATA / "batch.csv").exists(),
    reason="run the data and train commands in CLAUDE.md first",
)


@pytest.fixture(scope="module")
def metrics() -> dict:
    return json.loads((MODELS / "metrics.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def batch_rows() -> list[dict]:
    with (DATA / "batch.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------------------
# Leakage — asserted mechanically, not by reading the source
# --------------------------------------------------------------------------------------


def test_holdout_cannot_be_read_before_the_model_is_fit(tmp_path: Path) -> None:
    """SPEC §8.1 F2. The seal is the mechanism, not a comment asking nicely."""
    sealed = SealedHoldout(tmp_path / "corpus_holdout.csv")

    assert sealed.is_sealed
    with pytest.raises(RuntimeError, match="before the model was fit"):
        sealed.unseal(model_is_fit=False)

    # Still sealed after the refusal — a failed attempt must not half-open it.
    assert sealed.is_sealed


@needs_artifacts
def test_training_never_opens_the_operational_batch() -> None:
    """SPEC §2.1: the 500 records the demo runs on are out of sample by construction."""
    source = (REPO_ROOT / "backend" / "scripts" / "train_scorer.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("batch.csv", "batch_truth.json"):
        assert forbidden not in source, (
            f"train_scorer.py references {forbidden}; the operational batch must never "
            "be read during training"
        )


@needs_artifacts
def test_reported_metrics_come_from_the_holdout_not_the_training_corpus(
    metrics: dict,
) -> None:
    holdout_rows = read_csv(DATA / "corpus_holdout.csv")
    assert metrics["n_holdout"] == len(holdout_rows)
    assert metrics["n_train"] == len(read_csv(DATA / "corpus_train.csv"))

    # Early stopping used a slice of train, never the holdout.
    assert metrics["n_fit"] + metrics["n_valid_early_stopping"] == metrics["n_train"]


# --------------------------------------------------------------------------------------
# SPEC §8.1 F2 — the reported gates
# --------------------------------------------------------------------------------------


@needs_artifacts
def test_holdout_auc_is_inside_the_spec_band(metrics: dict) -> None:
    auc = metrics["auc"]
    assert AUC_FLOOR <= auc, f"AUC {auc} is a build failure (SPEC §2.2)"
    assert auc <= AUC_LEAK_CEILING, f"AUC {auc} suggests a leak (SPEC §2.2)"
    assert 0.78 <= auc <= 0.84, f"AUC {auc} is outside SPEC §2.2's expected band"


@needs_artifacts
def test_auc_is_reported_with_a_confidence_interval(metrics: dict) -> None:
    """A point estimate alone is what made the old 100-row gate meaningless."""
    lo, hi = metrics["auc_ci95"]
    assert lo < metrics["auc"] < hi
    assert hi - lo < 0.10, f"CI [{lo}, {hi}] is too wide to support the claim"


@needs_artifacts
def test_auc_stays_under_the_population_ceiling(metrics: dict) -> None:
    """Ranking by the true hidden probability tops out at ~0.824. Beating that is a leak."""
    assert metrics["auc"] < metrics["population_oracle_auc"] + 0.02


@needs_artifacts
def test_precision_and_recall_reported_at_every_band_edge(metrics: dict) -> None:
    reported = {row["threshold"] for row in metrics["thresholds"]}
    assert reported == set(THRESHOLD_CUTS)

    for row in metrics["thresholds"]:
        assert 0.0 <= row["precision"] <= 1.0
        assert 0.0 <= row["recall"] <= 1.0


@needs_artifacts
def test_recall_increases_as_the_threshold_falls(metrics: dict) -> None:
    by_cut = sorted(metrics["thresholds"], key=lambda r: r["threshold"], reverse=True)
    recalls = [r["recall"] for r in by_cut]
    assert recalls == sorted(recalls), "recall must not fall as the threshold drops"


@needs_artifacts
def test_confusion_matrix_at_065_accounts_for_every_holdout_row(metrics: dict) -> None:
    cm = metrics["confusion_at_0.65"]
    total = (
        cm["true_negative"] + cm["false_positive"] + cm["false_negative"] + cm["true_positive"]
    )
    assert total == metrics["n_holdout"]


def test_confusion_at_is_correct_on_a_known_case() -> None:
    y = np.array([1, 1, 0, 0])
    p = np.array([0.9, 0.1, 0.8, 0.2])
    cm = confusion_at(y, p, 0.65)

    assert cm["true_positive"] == 1
    assert cm["false_negative"] == 1
    assert cm["false_positive"] == 1
    assert cm["true_negative"] == 1


def test_bootstrap_ci_brackets_the_point_estimate() -> None:
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=400)
    p = np.clip(y * 0.4 + rng.normal(0.3, 0.2, size=400), 0, 1)

    lo, hi = bootstrap_auc_ci(y, p, seed=1, resamples=300)
    assert lo < roc_auc_score(y, p) < hi


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_same_seed_reproduces_metrics_exactly(tmp_path: Path) -> None:
    """SPEC §8.1 F2. Small corpus so this stays a test rather than a training run."""
    data_dir = tmp_path / "data"
    for i in (1, 2):
        subprocess.run(
            [
                sys.executable, "-m", "backend.scripts.generate_data",
                "--seed", "1042", "--n", "1200", "--name", "corpus",
                "--split", "--edtech-off-cycle", "0.45",
                "--out-dir", str(data_dir),
            ],
            cwd=REPO_ROOT, check=True, capture_output=True,
        )
        result = subprocess.run(
            [
                sys.executable, "-m", "backend.scripts.train_scorer",
                "--data-dir", str(data_dir), "--model-dir", str(tmp_path / f"m{i}"),
            ],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    a = (tmp_path / "m1" / "metrics.json").read_bytes()
    b = (tmp_path / "m2" / "metrics.json").read_bytes()
    assert a == b, "metrics.json is not reproducible at a fixed seed"


# --------------------------------------------------------------------------------------
# Encoding contract — training and serving must not drift apart
# --------------------------------------------------------------------------------------


def test_categorical_codes_come_from_the_enums_not_from_the_data() -> None:
    """Pandas category codes shift when a value is absent from a file. Enums do not."""
    assert FAILURE_REASON_CODES == {r.value: i for i, r in enumerate(FailureReason)}
    assert MERCHANT_CATEGORY_CODES == {c.value: i for i, c in enumerate(MerchantCategory)}


def test_encoding_is_stable_when_a_category_is_missing_from_the_batch() -> None:
    """A batch with no edtech rows must not renumber every other category."""
    row = {
        "row_id": "mrs_000001",
        "failure_reason": "technical_decline",
        "merchant_category": "utilities",
        "days_to_payday": 3,
        "attempt_number": 2,
        "ticket_size_paise": 99_900,
        "days_since_last_success": 40,
        "mandate_age_days": 300,
    }
    first = encode_row(row)
    assert first == encode_row(row)
    assert first[0] == FAILURE_REASON_CODES["technical_decline"]
    assert first[4] == MERCHANT_CATEGORY_CODES["utilities"]


def test_unknown_categorical_value_raises_rather_than_scoring_as_zero() -> None:
    row = {
        "row_id": "mrs_000002",
        "failure_reason": "not_a_real_reason",
        "merchant_category": "saas",
        "days_to_payday": 1,
        "attempt_number": 1,
        "ticket_size_paise": 4_900,
        "days_since_last_success": 1,
        "mandate_age_days": 1,
    }
    with pytest.raises(ValueError, match="unknown categorical value"):
        encode_row(row)


def test_feature_order_is_the_contract() -> None:
    assert len(FEATURES) == 7
    assert FEATURES[0] == "failure_reason_code"
    assert set(CATEGORICAL_FEATURES) <= set(FEATURES)

    matrix = encode_matrix(
        [
            {
                "row_id": "mrs_000003",
                "failure_reason": "insufficient_balance",
                "merchant_category": "saas",
                "days_to_payday": 5,
                "attempt_number": 1,
                "ticket_size_paise": 12_345,
                "days_since_last_success": 10,
                "mandate_age_days": 99,
            }
        ]
    )
    assert matrix.shape == (1, len(FEATURES))


def test_empty_input_scores_without_crashing() -> None:
    assert encode_matrix([]).shape == (0, len(FEATURES))


# --------------------------------------------------------------------------------------
# The model as served
# --------------------------------------------------------------------------------------


@needs_artifacts
def test_scores_are_probabilities(batch_rows: list[dict]) -> None:
    p = Scorer.load(MODELS / "scorer.txt").score_rows(batch_rows)

    assert p.shape == (len(batch_rows),)
    assert np.all((p >= 0.0) & (p <= 1.0)), "a score escaped [0, 1]"


@needs_artifacts
def test_scoring_is_deterministic(batch_rows: list[dict]) -> None:
    scorer = Scorer.load(MODELS / "scorer.txt")
    assert np.array_equal(scorer.score_rows(batch_rows), scorer.score_rows(batch_rows))


def test_missing_model_gives_an_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="train_scorer"):
        Scorer.load(tmp_path / "nope.txt")


# --------------------------------------------------------------------------------------
# SPEC §2.2 property 3 — the blind spot, measured where it actually bites
# --------------------------------------------------------------------------------------


@needs_artifacts
def test_edtech_is_over_predicted_on_the_operational_batch(batch_rows: list[dict]) -> None:
    """The drift check. This is the honest-failures panel's reason to exist.

    Measured on the batch, not the corpus holdout: the holdout shares the corpus's
    distribution, so the model is calibrated there by construction (+0.002 measured).
    The batch is generated off-cycle, which is where the staleness shows.
    """
    p = Scorer.load(MODELS / "scorer.txt").score_rows(batch_rows)
    y = np.asarray([int(r["recovered"]) for r in batch_rows])
    is_edtech = np.asarray(
        [r["merchant_category"] == MerchantCategory.EDTECH.value for r in batch_rows]
    )

    edtech_gap = float(p[is_edtech].mean() - y[is_edtech].mean())
    other_gap = float(p[~is_edtech].mean() - y[~is_edtech].mean())

    assert edtech_gap > 0.05, f"edtech over-prediction is only {edtech_gap:+.3f}"
    assert edtech_gap > other_gap + 0.05, (
        f"edtech {edtech_gap:+.3f} is not distinct from the rest {other_gap:+.3f} — "
        "the blind spot is not specific to the cohort"
    )


@needs_artifacts
def test_confident_edtech_predictions_are_the_wrong_ones(batch_rows: list[dict]) -> None:
    """High-scoring edtech rows should recover far below what their score implies."""
    p = Scorer.load(MODELS / "scorer.txt").score_rows(batch_rows)
    y = np.asarray([int(r["recovered"]) for r in batch_rows])
    is_edtech = np.asarray(
        [r["merchant_category"] == MerchantCategory.EDTECH.value for r in batch_rows]
    )

    confident = is_edtech & (p >= 0.35)
    assert confident.sum() >= 10, "too few confident edtech rows to claim a cluster"
    assert float(y[confident].mean()) < 0.25, (
        "confident edtech predictions recover too often for this to be a real blind spot"
    )
