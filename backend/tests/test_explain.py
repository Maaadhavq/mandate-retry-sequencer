"""SPEC §7 F9 — SHAP explanations.

The point of these is that the explanation must be *true of the model*, not merely
plausible-sounding. So the tests check it against things the generator is known to have
built in: the payday interaction, the revoked-mandate signal, and the fact that the edtech
blind spot has no feature to point at.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.explain import (
    FEATURE_LABELS,
    NEGLIGIBLE,
    Contribution,
    Explainer,
    _summarise,
)
from backend.app.main import app
from backend.app.policy import FailureReason, MerchantCategory
from backend.app.scorer import FEATURES

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "data" / "batch.csv").exists()
    or not (REPO_ROOT / "models" / "scorer.txt").exists(),
    reason="run the data commands in README.md first",
)


@pytest.fixture(scope="module")
def batch():
    from backend.app.runner import load_batch

    return load_batch(REPO_ROOT / "data" / "batch.csv")


@pytest.fixture(scope="module")
def explainer() -> Explainer:
    return Explainer()


# --------------------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------------------


def test_every_feature_gets_a_contribution(explainer: Explainer, batch) -> None:
    explanation = explainer.explain(batch[0])
    assert {c.feature for c in explanation.contributions} == set(FEATURES)


def test_contributions_are_sorted_by_magnitude(explainer: Explainer, batch) -> None:
    contributions = explainer.explain(batch[0]).contributions
    magnitudes = [abs(c.contribution) for c in contributions]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_every_feature_has_a_human_label() -> None:
    """A payments operator should never see `merchant_category_code`."""
    assert set(FEATURE_LABELS) == set(FEATURES)
    assert all("_code" not in label for label in FEATURE_LABELS.values())


def test_explanations_are_deterministic(explainer: Explainer, batch) -> None:
    first = explainer.explain(batch[3])
    second = explainer.explain(batch[3])
    assert first.to_dict() == second.to_dict()


def test_the_reported_score_matches_the_scorer(explainer: Explainer, batch) -> None:
    from backend.app.scorer import Scorer

    record = batch[7]
    assert explainer.explain(record).score == pytest.approx(
        float(Scorer.load().score_records([record])[0])
    )


# --------------------------------------------------------------------------------------
# The explanation has to be true of the model, not just readable
# --------------------------------------------------------------------------------------


def test_payday_dominates_for_insufficient_balance(explainer: Explainer, batch) -> None:
    """SPEC §2.2 property 1, seen from the model's side.

    The generator makes payday alignment the driver for insufficient_balance. If SHAP does
    not surface it there, either the scorer never learned the interaction or the generator
    stopped producing it — both worth failing over.
    """
    candidates = [
        r
        for r in batch
        if r.failure_reason is FailureReason.INSUFFICIENT_BALANCE and r.days_to_payday <= 5
    ]
    assert candidates, "no near-payday insufficient_balance records in the batch"

    ranked_first = 0
    for record in candidates[:40]:
        top = explainer.explain(record).contributions[0]
        if top.feature == "days_to_payday" and top.contribution > 0:
            ranked_first += 1

    assert ranked_first >= len(candidates[:40]) * 0.5, (
        f"days_to_payday led the explanation for only {ranked_first} of "
        f"{len(candidates[:40])} near-payday records"
    )


def test_revoked_mandate_pushes_the_score_down(explainer: Explainer, batch) -> None:
    """The model learned rule 1 on its own. It is still the rule that enforces it."""
    revoked = [r for r in batch if r.failure_reason is FailureReason.REVOKED_MANDATE]
    assert revoked

    for record in revoked[:20]:
        reason = next(
            c
            for c in explainer.explain(record).contributions
            if c.feature == "failure_reason_code"
        )
        assert reason.contribution < 0, f"{record.row_id}: revoked did not lower the score"


def test_edtech_has_no_feature_explaining_its_failures(explainer: Explainer, batch) -> None:
    """The blind spot is unexplainable by construction — that is the honest finding.

    SHAP can only attribute to features that exist. The academic cycle is not one, so the
    explanation for a failed edtech record names something else. If this ever starts
    pointing at a real cause, the cycle has leaked into the data.
    """
    edtech = [r for r in batch if r.merchant_category is MerchantCategory.EDTECH]
    assert edtech

    for record in edtech[:15]:
        labels = {c.feature for c in explainer.explain(record).contributions}
        assert "academic_cycle" not in labels
        assert not any("cycle" in f for f in labels)


def test_contributions_are_log_odds_not_probabilities(explainer: Explainer, batch) -> None:
    """A number that looks like a probability and is not is worse than no number."""
    explanation = explainer.explain(batch[0])
    assert explanation.base_value < 0, "base value should be log-odds, not a probability"
    assert 0.0 <= explanation.score <= 1.0


# --------------------------------------------------------------------------------------
# The summary sentence
# --------------------------------------------------------------------------------------


def test_summary_names_both_directions() -> None:
    contributions = (
        Contribution("a", "failure reason", "technical_decline", 1.4),
        Contribution("b", "ticket size", "₹45,000", -0.3),
    )
    summary = _summarise(contributions)
    assert "raise this score" in summary and "lower it" in summary
    assert "technical_decline" in summary and "₹45,000" in summary


def test_summary_handles_one_sided_evidence() -> None:
    up_only = (Contribution("a", "failure reason", "technical_decline", 1.4),)
    assert "Nothing material pulls it down" in _summarise(up_only)

    down_only = (Contribution("a", "attempt number", "4", -1.1),)
    assert "Nothing material pushes it up" in _summarise(down_only)


def test_summary_ignores_negligible_contributions() -> None:
    noise = (Contribution("a", "mandate age", "300d", NEGLIGIBLE / 2),)
    assert "No feature moved this score materially" in _summarise(noise)


def test_summary_is_one_sentence(explainer: Explainer, batch) -> None:
    for record in batch[:20]:
        summary = explainer.explain(record).summary
        assert summary.endswith(".")
        assert summary.count(".") <= 2, f"not one sentence: {summary}"


# --------------------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------------------


def test_explain_endpoint_returns_a_record(batch) -> None:
    client = TestClient(app)
    response = client.get(f"/explain/{batch[0].row_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["row_id"] == batch[0].row_id
    assert len(payload["contributions"]) == len(FEATURES)
    assert payload["summary"]


def test_explain_endpoint_404s_for_an_unknown_record() -> None:
    client = TestClient(app)
    response = client.get("/explain/mrs_ffffff")

    assert response.status_code == 404
    assert "not in the current batch" in response.json()["detail"]


def test_explain_endpoint_needs_no_api_key(monkeypatch, batch) -> None:
    """The whole point of F9: this is the explanation layer that works in the ablation."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(app)
    assert client.get(f"/explain/{batch[1].row_id}").status_code == 200
