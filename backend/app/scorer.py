"""Scoring surface. SPEC §2.2, §2.3.

Training and inference share this module on purpose. The feature list, the column order,
and the categorical encoding are defined once here, so a model trained by
`scripts/train_scorer.py` cannot silently disagree with the pipeline that later serves it
— which is the usual way a scorer starts returning quietly wrong numbers.

Categoricals are mapped to fixed integer codes derived from the enums in `policy.py`
rather than to pandas category codes. Pandas codes depend on the categories present in
whatever frame you happened to load, so a batch that lacks one merchant category would
shift every code after it. Deriving them from the enum makes the encoding a property of
the domain instead of a property of the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import numpy as np

from backend.app.models import MandateRecord
from backend.app.policy import FailureReason, MerchantCategory

#: Feature columns, in the exact order the model is fed. Order is part of the contract.
FEATURES: Final[tuple[str, ...]] = (
    "failure_reason_code",
    "days_to_payday",
    "attempt_number",
    "ticket_size_paise",
    "merchant_category_code",
    "days_since_last_success",
    "mandate_age_days",
)

#: Indices into FEATURES that LightGBM should treat as categorical.
CATEGORICAL_FEATURES: Final[tuple[str, ...]] = (
    "failure_reason_code",
    "merchant_category_code",
)

FAILURE_REASON_CODES: Final[dict[str, int]] = {
    reason.value: i for i, reason in enumerate(FailureReason)
}
MERCHANT_CATEGORY_CODES: Final[dict[str, int]] = {
    cat.value: i for i, cat in enumerate(MerchantCategory)
}

DEFAULT_MODEL_PATH: Final[Path] = Path("models/scorer.txt")


def encode_row(row: dict) -> list[float]:
    """One CSV row (or record dict) to a feature vector in FEATURES order."""
    try:
        reason_code = FAILURE_REASON_CODES[str(row["failure_reason"])]
        category_code = MERCHANT_CATEGORY_CODES[str(row["merchant_category"])]
    except KeyError as exc:  # an unseen enum value must fail loudly, not score as 0
        raise ValueError(f"unknown categorical value in row {row.get('row_id')}: {exc}") from exc

    return [
        float(reason_code),
        float(row["days_to_payday"]),
        float(row["attempt_number"]),
        float(row["ticket_size_paise"]),
        float(category_code),
        float(row["days_since_last_success"]),
        float(row["mandate_age_days"]),
    ]


def encode_record(record: MandateRecord) -> list[float]:
    """Same encoding, from the frozen domain type the pipeline passes around."""
    return encode_row(
        {
            "row_id": record.row_id,
            "failure_reason": record.failure_reason.value,
            "merchant_category": record.merchant_category.value,
            "days_to_payday": record.days_to_payday,
            "attempt_number": record.attempt_number,
            "ticket_size_paise": record.ticket_size_paise,
            "days_since_last_success": record.days_since_last_success,
            "mandate_age_days": record.mandate_age_days,
        }
    )


def encode_matrix(rows: list[dict]) -> np.ndarray:
    """Feature matrix for a list of rows, shape (n, len(FEATURES))."""
    if not rows:
        return np.empty((0, len(FEATURES)), dtype=float)
    return np.asarray([encode_row(r) for r in rows], dtype=float)


class Scorer:
    """A fitted model plus the encoding it was trained with.

    Deliberately thin: it holds no policy. Turning a score into an action is the
    guardrail layer's job (SPEC §3), and keeping that boundary sharp is what lets a rule
    override the model rather than negotiate with it.
    """

    def __init__(self, booster) -> None:
        self._booster = booster

    @classmethod
    def load(cls, path: Path | str = DEFAULT_MODEL_PATH) -> "Scorer":
        import lightgbm as lgb

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"no scorer at {path}. Run: "
                f".venv/Scripts/python -m backend.scripts.train_scorer"
            )
        return cls(lgb.Booster(model_file=str(path)))

    def score_matrix(self, matrix: np.ndarray) -> np.ndarray:
        """P(recover) in [0, 1], one per row."""
        if matrix.shape[0] == 0:
            return np.empty(0, dtype=float)
        return np.asarray(self._booster.predict(matrix), dtype=float)

    def score_rows(self, rows: list[dict]) -> np.ndarray:
        return self.score_matrix(encode_matrix(rows))

    def score_records(self, records: list[MandateRecord]) -> np.ndarray:
        if not records:
            return np.empty(0, dtype=float)
        return self.score_matrix(
            np.asarray([encode_record(r) for r in records], dtype=float)
        )
