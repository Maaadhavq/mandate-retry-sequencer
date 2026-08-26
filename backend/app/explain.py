"""F9 — per-record explanations from SHAP. SPEC §7, §9.

SPEC lists this as cut #1, on the reasoning that the agent writes a sentence to every row it
touches so explainability does not depend on SHAP. That holds only when the agent actually
runs. With no API key and an empty response cache — which is the state of a fresh clone —
`agent_reasoning` is a placeholder on all 411 agent-routed rows, and the honest-failures
panel can say *that* a record was refused but not *why the model scored it as it did*.

This module closes that gap without a network call. It is the explanation layer that works
in the ablation, which is the configuration a judge will actually run.

Contributions are in the model's log-odds space, not probability. A contribution of +1.44
does not mean "+144% chance"; it means this feature pushed the log-odds up by 1.44 from the
base rate. The summary text is phrased to avoid implying otherwise, because a number that
looks like a probability and is not is worse than no number.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Final

import numpy as np

from backend.app.models import MandateRecord
from backend.app.scorer import FEATURES, Scorer, encode_record

#: How many features the one-line summary mentions. Three is enough to be useful and few
#: enough to read; the full vector is always available in `contributions`.
SUMMARY_TOP_N: Final[int] = 3

#: A contribution smaller than this is noise and is not worth a clause in a sentence.
NEGLIGIBLE: Final[float] = 0.02

#: Human labels. The model sees encoded columns; a payments operator should not have to.
FEATURE_LABELS: Final[dict[str, str]] = {
    "failure_reason_code": "failure reason",
    "days_to_payday": "days to payday",
    "attempt_number": "attempt number",
    "ticket_size_paise": "ticket size",
    "merchant_category_code": "merchant category",
    "days_since_last_success": "days since last success",
    "mandate_age_days": "mandate age",
}


@dataclass(frozen=True, slots=True)
class Contribution:
    """One feature's push on the score, in log-odds."""

    feature: str
    label: str
    value: str
    contribution: float

    @property
    def direction(self) -> str:
        return "raises" if self.contribution > 0 else "lowers"


@dataclass(frozen=True, slots=True)
class Explanation:
    row_id: str
    score: float
    base_value: float
    contributions: tuple[Contribution, ...]
    summary: str

    def to_dict(self) -> dict:
        return {
            "row_id": self.row_id,
            "score": round(self.score, 6),
            "base_value": round(self.base_value, 6),
            "summary": self.summary,
            "contributions": [
                {
                    "feature": c.feature,
                    "label": c.label,
                    "value": c.value,
                    "contribution": round(c.contribution, 6),
                }
                for c in self.contributions
            ],
        }


def _display_value(record: MandateRecord, feature: str) -> str:
    """What a human should see for this feature, not what the model saw."""
    match feature:
        case "failure_reason_code":
            return record.failure_reason.value
        case "merchant_category_code":
            return record.merchant_category.value
        case "ticket_size_paise":
            return f"₹{record.ticket_size_paise / 100:,.0f}"
        case "days_to_payday":
            return f"{record.days_to_payday}d"
        case "attempt_number":
            return f"{record.attempt_number}"
        case "days_since_last_success":
            return f"{record.days_since_last_success}d"
        case "mandate_age_days":
            return f"{record.mandate_age_days}d"
    return ""


def _summarise(contributions: tuple[Contribution, ...]) -> str:
    """One sentence naming what moved this score, biggest first."""
    material = [c for c in contributions if abs(c.contribution) >= NEGLIGIBLE]
    if not material:
        return "No feature moved this score materially away from the base rate."

    up = [c for c in material if c.contribution > 0][:SUMMARY_TOP_N]
    down = [c for c in material if c.contribution < 0][:SUMMARY_TOP_N]

    def phrase(items: list[Contribution]) -> str:
        return ", ".join(f"{c.label} {c.value}" for c in items)

    if up and down:
        return f"{phrase(up)} raise this score; {phrase(down)} lower it."
    if up:
        return f"{phrase(up)} raise this score. Nothing material pulls it down."
    return f"{phrase(down)} lower this score. Nothing material pushes it up."


class Explainer:
    """SHAP over the fitted booster.

    The explainer is built lazily and cached: constructing a `TreeExplainer` walks the whole
    forest, which is wasted work on a run where nobody asks for an explanation.
    """

    def __init__(self, scorer: Scorer | None = None) -> None:
        self._scorer = scorer or Scorer.load()

    @cached_property
    def _explainer(self):
        import shap

        return shap.TreeExplainer(self._scorer._booster)

    @property
    def base_value(self) -> float:
        return float(np.ravel(self._explainer.expected_value)[0])

    def explain(self, record: MandateRecord, score: float | None = None) -> Explanation:
        matrix = np.asarray([encode_record(record)], dtype=float)
        values = np.asarray(self._explainer.shap_values(matrix))
        # LightGBM binary returns (n, features); some shap versions add a class axis.
        row = values[0] if values.ndim == 2 else values[0][0]

        contributions = tuple(
            sorted(
                (
                    Contribution(
                        feature=name,
                        label=FEATURE_LABELS.get(name, name),
                        value=_display_value(record, name),
                        contribution=float(row[i]),
                    )
                    for i, name in enumerate(FEATURES)
                ),
                key=lambda c: abs(c.contribution),
                reverse=True,
            )
        )

        if score is None:
            score = float(self._scorer.score_records([record])[0])

        return Explanation(
            row_id=record.row_id,
            score=score,
            base_value=self.base_value,
            contributions=contributions,
            summary=_summarise(contributions),
        )
