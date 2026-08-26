"""F2 — fit the recovery scorer. SPEC §2.2, §8.1.

Reports on the corpus holdout only. The operational batch is never opened here at all:
it is the set the pipeline runs on, and keeping it out of training is what lets the demo
claim its numbers are out of sample (SPEC §2.1).

Leakage is prevented structurally rather than by convention. `SealedHoldout` refuses to
hand over its data until `unseal()` is called, and `unseal()` refuses to run before the
booster exists. Getting a metric out of the holdout early raises instead of quietly
returning a better-looking number. The `--prove-seal` flag demonstrates that on demand,
which is a more convincing artefact for a judge than a comment claiming discipline.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
from pathlib import Path
from typing import Final

import numpy as np

from backend.app.policy import THRESHOLD_CUTS
from backend.app.scorer import CATEGORICAL_FEATURES, FEATURES, encode_matrix

DEFAULT_SEED: Final[int] = 42
BOOTSTRAP_RESAMPLES: Final[int] = 2000

#: Deliberately conservative. The population ceiling is ~0.824 (SPEC §2.2), so a model
#: that scores far above that is reading something it should not be able to see, and the
#: right response is to investigate the generator rather than ship the number.
LGB_PARAMS: Final[dict] = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    # Reproducibility: threads and the feature-parallel path both perturb split choices,
    # so metrics.json would stop being byte-stable across runs without these.
    "num_threads": 1,
    "deterministic": True,
    "force_row_wise": True,
}
NUM_BOOST_ROUND: Final[int] = 3000
EARLY_STOPPING_ROUNDS: Final[int] = 100

#: Share of the training corpus held back for early stopping. Never the holdout.
VALID_FRACTION: Final[float] = 0.15

#: AUC outside this band is a build failure or a leak. SPEC §2.2.
AUC_FLOOR: Final[float] = 0.70
AUC_LEAK_CEILING: Final[float] = 0.90


class SealedHoldout:
    """Holds the holdout shut until the model is fit.

    The point is not ceremony. `train_test_split` inside a fit loop is one of the easiest
    ways to leak, and a code comment saying "do not touch this yet" is not checkable. An
    object that raises is.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sealed = True
        self._rows: list[dict] | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_sealed(self) -> bool:
        return self._sealed

    def unseal(self, *, model_is_fit: bool) -> list[dict]:
        if not model_is_fit:
            raise RuntimeError(
                f"{self._path.name} was opened before the model was fit. "
                "The holdout is evidence, not training signal (SPEC §8.1 F2, CLAUDE.md)."
            )
        if self._rows is None:
            self._rows = read_csv(self._path)
        self._sealed = False
        return self._rows


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Generate the corpus first (see README.md): "
            ".venv/Scripts/python -m backend.scripts.generate_data "
            "--seed 1042 --n 8000 --name corpus --split"
        )
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def labels_of(rows: list[dict]) -> np.ndarray:
    return np.asarray([int(r["recovered"]) for r in rows], dtype=int)


def bootstrap_auc_ci(
    y: np.ndarray, p: np.ndarray, *, seed: int, resamples: int = BOOTSTRAP_RESAMPLES
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI for AUC.

    Reported because the point estimate alone invites over-reading. SPEC §2.1 records
    what happened when this project tried to gate on a point estimate from 100 rows.
    """
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    n = y.size
    draws: list[float] = []
    for _ in range(resamples):
        idx = rng.integers(0, n, size=n)
        if np.unique(y[idx]).size < 2:
            continue
        draws.append(roc_auc_score(y[idx], p[idx]))
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def threshold_report(y: np.ndarray, p: np.ndarray, cut: float) -> dict:
    from sklearn.metrics import precision_score, recall_score

    pred = (p >= cut).astype(int)
    return {
        "threshold": cut,
        "predicted_positive": int(pred.sum()),
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
    }


def confusion_at(y: np.ndarray, p: np.ndarray, cut: float) -> dict:
    pred = (p >= cut).astype(int)
    return {
        "threshold": cut,
        "true_negative": int(((y == 0) & (pred == 0)).sum()),
        "false_positive": int(((y == 0) & (pred == 1)).sum()),
        "false_negative": int(((y == 1) & (pred == 0)).sum()),
        "true_positive": int(((y == 1) & (pred == 1)).sum()),
    }


def cohort_calibration(rows: list[dict], y: np.ndarray, p: np.ndarray) -> list[dict]:
    """Mean predicted vs actual recovery per merchant category.

    This is where the edtech blind spot has to show up. If it does not, either the
    generator stopped biting or the scorer found the hidden cycle — both worth knowing.
    """
    out: list[dict] = []
    categories = sorted({r["merchant_category"] for r in rows})
    for category in categories:
        mask = np.asarray([r["merchant_category"] == category for r in rows], dtype=bool)
        if not mask.any():
            continue
        out.append(
            {
                "merchant_category": category,
                "n": int(mask.sum()),
                "mean_predicted": round(float(p[mask].mean()), 4),
                "actual_recovery_rate": round(float(y[mask].mean()), 4),
                "over_prediction": round(float(p[mask].mean() - y[mask].mean()), 4),
            }
        )
    return out


def train(
    train_path: Path, holdout: SealedHoldout, *, seed: int
) -> tuple[object, dict, list[dict], np.ndarray, np.ndarray]:
    import lightgbm as lgb

    train_rows = read_csv(train_path)
    x_all = encode_matrix(train_rows)
    y_all = labels_of(train_rows)

    assert holdout.is_sealed, "the holdout was opened before the fit — abort"

    # Early stopping needs a validation set. It is carved out of the *training* corpus,
    # never the holdout — stopping on the holdout would tune the model to the set the
    # metric is reported on, which is leakage wearing a lab coat.
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(train_rows))
    n_valid = int(round(len(train_rows) * VALID_FRACTION))
    valid_idx, fit_idx = order[:n_valid], order[n_valid:]

    params = dict(LGB_PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
    shared = dict(
        feature_name=list(FEATURES),
        categorical_feature=list(CATEGORICAL_FEATURES),
        free_raw_data=False,
    )
    fit_set = lgb.Dataset(x_all[fit_idx], label=y_all[fit_idx], **shared)
    valid_set = lgb.Dataset(x_all[valid_idx], label=y_all[valid_idx], reference=fit_set, **shared)

    booster = lgb.train(
        params,
        fit_set,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
    )

    # The model exists. Only now may the holdout be opened.
    holdout_rows = holdout.unseal(model_is_fit=booster is not None)
    x_holdout = encode_matrix(holdout_rows)
    y_holdout = labels_of(holdout_rows)
    p_holdout = np.asarray(booster.predict(x_holdout), dtype=float)

    meta = {
        "n_train": len(train_rows),
        "n_fit": int(len(fit_idx)),
        "n_valid_early_stopping": int(len(valid_idx)),
        "best_iteration": int(booster.best_iteration or NUM_BOOST_ROUND),
        "n_holdout": len(holdout_rows),
        "train_positive_rate": round(float(y_all.mean()), 4),
        "holdout_positive_rate": round(float(y_holdout.mean()), 4),
    }
    return booster, meta, holdout_rows, y_holdout, p_holdout


def prove_seal(holdout: SealedHoldout) -> None:
    """Show the guard refusing, so the leakage claim is demonstrated rather than asserted."""
    try:
        holdout.unseal(model_is_fit=False)
    except RuntimeError as exc:
        print("seal proof — reading the holdout before the fit raises:")
        print(f"  RuntimeError: {exc}")
        return
    raise AssertionError("the holdout seal did not hold")


def main() -> None:
    from sklearn.metrics import roc_auc_score

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument(
        "--prove-seal",
        action="store_true",
        help="demonstrate that the holdout cannot be read before the model is fit",
    )
    args = parser.parse_args()

    train_path = args.data_dir / "corpus_train.csv"
    holdout = SealedHoldout(args.data_dir / "corpus_holdout.csv")

    if args.prove_seal:
        prove_seal(holdout)
        print()

    booster, meta, holdout_rows, y, p = train(train_path, holdout, seed=args.seed)

    auc = float(roc_auc_score(y, p))
    ci_lo, ci_hi = bootstrap_auc_ci(y, p, seed=args.seed)

    metrics = {
        "seed": args.seed,
        "auc": round(auc, 4),
        "auc_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
        "population_oracle_auc": 0.824,
        "thresholds": [threshold_report(y, p, cut) for cut in THRESHOLD_CUTS],
        "confusion_at_0.65": confusion_at(y, p, 0.65),
        "cohort_calibration": cohort_calibration(holdout_rows, y, p),
        "params": {k: v for k, v in sorted(LGB_PARAMS.items())},
        "num_boost_round": NUM_BOOST_ROUND,
        **meta,
    }

    args.model_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(args.model_dir / "scorer.txt"))
    with (args.model_dir / "metrics.json").open("w", encoding="utf-8", newline="") as fh:
        json.dump(metrics, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"trained on {meta['n_train']} rows, evaluated on {meta['n_holdout']} held out")
    print(f"  holdout AUC   {auc:.4f}   95% CI [{ci_lo:.4f}, {ci_hi:.4f}]")
    print(f"  ceiling       {metrics['population_oracle_auc']:.4f}  (ranking by true p)")
    print()
    for row in metrics["thresholds"]:
        print(
            f"  cut {row['threshold']:.2f}   precision {row['precision']:.3f}   "
            f"recall {row['recall']:.3f}   flagged {row['predicted_positive']}"
        )
    cm = metrics["confusion_at_0.65"]
    print(
        f"\n  confusion @0.65   TN {cm['true_negative']}  FP {cm['false_positive']}  "
        f"FN {cm['false_negative']}  TP {cm['true_positive']}"
    )
    print("\n  cohort            n   pred   actual    gap")
    for row in metrics["cohort_calibration"]:
        flag = "  <-- over-predicted" if row["over_prediction"] > 0.05 else ""
        print(
            f"  {row['merchant_category']:<12} {row['n']:>5}  {row['mean_predicted']:.3f}  "
            f"{row['actual_recovery_rate']:.3f}  {row['over_prediction']:+.3f}{flag}"
        )

    print(f"\n  -> {args.model_dir / 'scorer.txt'}")
    print(f"  -> {args.model_dir / 'metrics.json'}")
    print(f"  (python {platform.python_version()})")

    if auc < AUC_FLOOR:
        raise SystemExit(
            f"\nBUILD FAILURE: holdout AUC {auc:.4f} is below {AUC_FLOOR} (SPEC §2.2). "
            "The generator's signal is too weak or the noise too high — do not ship this."
        )
    if auc > AUC_LEAK_CEILING:
        raise SystemExit(
            f"\nBUILD FAILURE: holdout AUC {auc:.4f} exceeds {AUC_LEAK_CEILING} against a "
            f"{metrics['population_oracle_auc']} ceiling (SPEC §2.2). The generator is "
            "leaking something into the features — investigate, do not celebrate."
        )


if __name__ == "__main__":
    main()
